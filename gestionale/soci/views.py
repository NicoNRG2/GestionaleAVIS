"""Elenco e scheda dei soci (README M2)."""

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, F, Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from gestionale.benemerenze.calcolo import situazione
from gestionale.core.eventi import registra
from gestionale.core.forms import CampoData, FormBootstrap

from .models import Idoneita, Socio, StatoSocio

PER_PAGINA = 50
ATTIVI, NON_SOCIO, COLLABORATORI = "attivi", "non_socio", "collaboratori"
# "Solo presentazione" non è una donazione
VALIDE = Q(donazioni__tipo__e_donazione=True)


class FiltriSoci(FormBootstrap, forms.Form):
    q = forms.CharField(label="Cerca", required=False,
                        widget=forms.TextInput(attrs={"type": "search", "placeholder": "cognome, nome, CF, tessera"}))
    stato = forms.ChoiceField(required=False, choices=[("", "Tutti"), (ATTIVI, "Soci attivi"), *StatoSocio.choices,
                                                       (COLLABORATORI, "Collaboratori (anche donatori)"),
                                                       (NON_SOCIO, "Non soci")])
    idoneita = forms.ChoiceField(label="Idoneità", required=False, choices=[("", "Tutte"), *Idoneita.choices])
    ultima_dal = forms.DateField(label="Ultima donazione dal", required=False, widget=CampoData)
    ultima_al = forms.DateField(label="al", required=False, widget=CampoData)


class GestioneSocioForm(FormBootstrap, forms.ModelForm):
    """Dati associativi decisi dal gestionale, non da AssoWeb (README §3 e M2): solo per gli amministratori."""

    class Meta:
        model = Socio
        fields = ["stato", "data_iscrizione", "data_cessazione", "causa_cessazione", "collaboratore", "no_benemerenze",
                  "note"]
        widgets = {"data_iscrizione": CampoData, "data_cessazione": CampoData,
                   "note": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["stato"].choices = [("", "Non socio"), *StatoSocio.choices]
        self.fields["stato"].help_text = ("Si ricalcola a ogni import con la regola dei 2 anni; le cessazioni decise "
                                          "qui restano.")
        self.fields["collaboratore"].label = "Fa parte dei collaboratori"
        self.fields["data_iscrizione"].help_text = ("Un import la cambia solo quando cambia la data su AssoWeb, se "
                                                    "è plausibile e più vecchia (o qui manca o è il 31/12/1997).")

    def clean(self):
        dati = super().clean()
        # lo stato di chi fa parte dei collaboratori lo decide la regola: donatore finché dona, poi collaboratore
        if dati.get("collaboratore") and dati.get("stato") not in (StatoSocio.DONATORE, StatoSocio.COLLABORATORE):
            self.add_error("stato", "Un collaboratore è socio: per farlo cessare togli la spunta dei collaboratori.")
        if dati.get("stato") == StatoSocio.COLLABORATORE and not dati.get("collaboratore"):
            self.add_error("collaboratore", "Spuntalo per segnare il socio come collaboratore.")
        if dati.get("stato") == StatoSocio.EX_SOCIO:
            for campo in ("data_cessazione", "causa_cessazione"):
                if not dati.get(campo):
                    self.add_error(campo, "Obbligatoria per un ex socio.")
        else:
            dati["data_cessazione"] = dati["causa_cessazione"] = None
        return dati


def filtra(soci, filtri):
    """Applica i filtri validi di FiltriSoci; `soci` deve avere l'annotazione `ultima` (ultima donazione)."""
    for parola in filtri.get("q", "").split():
        condizione = Q(cognome__icontains=parola) | Q(nome__icontains=parola) | Q(codice_fiscale__istartswith=parola)
        if parola.isdigit():
            # anche l'ID del DB, con cui le anomalie degli import citano i soci
            condizione |= Q(tessera=parola) | Q(codice_interno_assoavis__startswith=parola)
            if len(parola) < 10:
                condizione |= Q(pk=parola)
        soci = soci.filter(condizione)
    stato = filtri.get("stato")
    if stato == ATTIVI:
        soci = soci.filter(stato__in=[StatoSocio.DONATORE, StatoSocio.COLLABORATORE])
    elif stato == NON_SOCIO:
        soci = soci.filter(stato="")
    elif stato == COLLABORATORI:
        soci = soci.filter(collaboratore=True)
    elif stato:
        soci = soci.filter(stato=stato)
    if filtri.get("idoneita"):
        soci = soci.filter(idoneita=filtri["idoneita"])
    if filtri.get("ultima_dal"):
        soci = soci.filter(ultima__gte=filtri["ultima_dal"])
    if filtri.get("ultima_al"):
        soci = soci.filter(ultima__lte=filtri["ultima_al"])
    return soci


@permission_required("soci.view_socio", raise_exception=True)
def elenco(request):
    """Elenco con ricerca e filtri. Le richieste HTMX (filtri cambiati) ricevono solo la tabella dei risultati."""
    dati = request.GET.copy()
    # all'apertura della pagina si vedono i soci attivi; "Tutti" invia lo stato vuoto
    dati.setdefault("stato", ATTIVI)
    form = FiltriSoci(dati)
    form.is_valid()  # con un filtro non valido (es. una data) si applicano gli altri e si mostra l'errore
    soci = Socio.objects.annotate(
        ultima=Max("donazioni__data", filter=VALIDE),
        totale=Count("donazioni", filter=VALIDE) + F("donazioni_pregresse_si") + F("donazioni_pregresse_aferesi"),
    )
    soci = filtra(soci, form.cleaned_data).order_by("cognome", "nome", "pk")
    dati.pop("pagina", None)
    contesto = {
        "form": form,
        "pagina": Paginator(soci, PER_PAGINA).get_page(request.GET.get("pagina")),
        "parametri": dati.urlencode(),
    }
    template = "soci/_risultati.html" if request.headers.get("HX-Request") else "soci/elenco.html"
    return render(request, template, contesto)


@permission_required("soci.view_socio", raise_exception=True)
def scheda(request, pk):
    """Scheda del socio; gli amministratori ci modificano i dati associativi. Ogni apertura va nel registro eventi."""
    socio = get_object_or_404(Socio.objects.select_related("sezione"), pk=pk)
    puo_modificare = request.user.has_perm("soci.change_socio")
    if request.method == "POST":
        if not puo_modificare:
            raise PermissionDenied
        # un'istanza a parte: un form non valido la modifica, e il resto della pagina deve mostrare i dati salvati
        form = GestioneSocioForm(request.POST, instance=Socio.objects.get(pk=pk))
        if form.is_valid():
            form.save()
            # solo i nomi dei campi: le note sono testo libero
            registra("modifica_socio", request, oggetto=f"socio {pk}", campi=form.changed_data)
            messages.success(request, "Dati associativi salvati.")
            return redirect("socio", pk=pk)
    else:
        form = GestioneSocioForm(instance=socio) if puo_modificare else None
        registra("scheda_socio", request, oggetto=f"socio {pk}")

    donazioni = list(socio.donazioni.select_related("tipo"))
    registrate = [d for d in donazioni if d.tipo.e_donazione]
    pregresse = socio.donazioni_pregresse_si + socio.donazioni_pregresse_aferesi
    contesto = {
        "socio": socio,
        "form": form,
        "donazioni": donazioni,
        "per_tipo": socio.donazioni.values("tipo__codice", "tipo__descrizione").annotate(n=Count("id")).order_by("-n"),
        "registrate": len(registrate),
        "pregresse": pregresse,
        "totale": len(registrate) + pregresse,
        "ultima": registrate[0].data if registrate else None,
        "benemerenze": socio.benemerenze.select_related("benemerenza").order_by("benemerenza__ordine"),
        "premiazioni": socio.premiazioni.filter(escluso=False).select_related("premiazione", "benemerenza")
                            .order_by("-premiazione__data_consegna", "benemerenza__ordine"),
        "situazione": situazione(socio, timezone.localdate()),
    }
    return render(request, "soci/scheda.html", contesto)
