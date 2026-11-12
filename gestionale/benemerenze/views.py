"""Premiazioni: preparazione, revisione, assegnazione ed export (README M4).

I volontari preparano la premiazione (crea, calcola, rivedi); solo gli amministratori la assegnano (README §2).
"""

from collections import Counter

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from gestionale.core.eventi import registra
from gestionale.core.forms import CampoData, FormBootstrap
from gestionale.soci.models import Socio

from . import calcolo, excel, regole
from .models import Benemerenza, Premiazione, StatoPremiazione

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# i campi che cambiano gli aventi diritto: se cambiano, la lista va ricalcolata
CAMPI_CALCOLO = {"data_riferimento", "ex_soci_cessati_dal"}


class PremiazioneForm(FormBootstrap, forms.ModelForm):
    class Meta:
        model = Premiazione
        fields = ["descrizione", "data_consegna", "data_riferimento", "ex_soci_cessati_dal"]
        widgets = {"data_consegna": CampoData, "data_riferimento": CampoData, "ex_soci_cessati_dal": CampoData}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["data_riferimento"].required = True
        self.fields["data_consegna"].help_text = "La data della cerimonia, che diventa la data delle benemerenze."
        self.fields["data_riferimento"].help_text = "Si contano le donazioni fatte fino a questo giorno."
        self.fields["ex_soci_cessati_dal"].help_text = ("Di solito la consegna dell'ultima premiazione; vuota = "
                                                         "nessun ex socio.")


class AggiuntaForm(FormBootstrap, forms.Form):
    socio = forms.IntegerField(label="ID del socio", min_value=1, help_text="Il numero nella scheda del socio.")
    benemerenza = forms.ModelChoiceField(queryset=Benemerenza.objects.filter(codice__in=regole.LIVELLO))
    motivo = forms.CharField(max_length=200)

    def clean_socio(self):
        socio = Socio.objects.filter(pk=self.cleaned_data["socio"]).first()
        if socio is None:
            raise forms.ValidationError("Nessun socio con questo ID.")
        return socio


def _premiazione(pk, modificabile=False):
    premiazione = get_object_or_404(Premiazione, pk=pk)
    if modificabile and premiazione.stato == StatoPremiazione.ASSEGNATA:
        raise calcolo.PremiazioneError("La premiazione è già assegnata: non si può più modificare.")
    return premiazione


@permission_required("benemerenze.view_premiazione", raise_exception=True)
def elenco(request):
    # con un'aggregazione Django non applica Meta.ordering
    premiazioni = Premiazione.objects.annotate(
        soci=Count("premiati__socio", filter=Q(premiati__escluso=False), distinct=True)
    ).order_by("-data_consegna", "-pk")
    return render(request, "benemerenze/elenco.html", {"premiazioni": premiazioni, "aperta": calcolo.aperta()})


@permission_required("benemerenze.add_premiazione", raise_exception=True)
def nuova(request):
    aperta = calcolo.aperta()
    if aperta:
        messages.warning(request, "C'è già una premiazione da assegnare: se ne prepara una alla volta.")
        return redirect("premiazione", pk=aperta.pk)
    oggi = timezone.localdate()
    form = PremiazioneForm(request.POST or None, initial={
        "descrizione": f"Premiazione {oggi.year}", "data_riferimento": oggi,
        "ex_soci_cessati_dal": calcolo.ultima_consegna()})
    if form.is_valid():
        premiazione = form.save()
        registra("nuova_premiazione", request, oggetto=f"premiazione {premiazione.pk}")
        return redirect("premiazione", pk=premiazione.pk)
    return render(request, "benemerenze/modifica.html", {"form": form})


@permission_required("benemerenze.change_premiazione", raise_exception=True)
def modifica(request, pk):
    premiazione = get_object_or_404(Premiazione, pk=pk)
    if premiazione.stato == StatoPremiazione.ASSEGNATA:
        messages.error(request, "La premiazione è già assegnata: non si può più modificare.")
        return redirect("premiazione", pk=pk)
    form = PremiazioneForm(request.POST or None, instance=premiazione)
    if form.is_valid():
        if CAMPI_CALCOLO & set(form.changed_data) and premiazione.stato == StatoPremiazione.CALCOLATA:
            premiazione.stato = StatoPremiazione.BOZZA
            messages.warning(request, "Sono cambiati i parametri del calcolo: ricalcola la lista prima di assegnarla.")
        form.save()
        registra("modifica_premiazione", request, oggetto=f"premiazione {pk}", campi=form.changed_data)
        return redirect("premiazione", pk=pk)
    return render(request, "benemerenze/modifica.html", {"form": form, "premiazione": premiazione})


@permission_required("benemerenze.view_premiazione", raise_exception=True)
def dettaglio(request, pk):
    premiazione = get_object_or_404(Premiazione, pk=pk)
    premiati, esclusi = calcolo.premiati_per_socio(premiazione)
    per_benemerenza = Counter(g.principale.benemerenza for g in premiati)
    modificabile = premiazione.stato != StatoPremiazione.ASSEGNATA
    contesto = {
        "premiazione": premiazione,
        "statuto": regole.statuto(premiazione.data_riferimento) if premiazione.data_riferimento else None,
        "premiati": premiati,
        "esclusi": esclusi,
        "per_benemerenza": sorted(per_benemerenza.items(), key=lambda b: b[0].ordine),
        "modificabile": modificabile,
        "aggiunta": AggiuntaForm() if modificabile and request.user.has_perm("benemerenze.change_premiazione")
        else None,
    }
    return render(request, "benemerenze/premiazione.html", contesto)


def _esegui(request, pk, funzione):
    """Esegue un'azione sulla premiazione e torna alla sua pagina; gli errori diventano un messaggio."""
    try:
        funzione()
    except calcolo.PremiazioneError as errore:
        messages.error(request, str(errore))
    return redirect("premiazione", pk=pk)


@require_POST
@permission_required("benemerenze.change_premiazione", raise_exception=True)
def calcola(request, pk):
    def azione():
        premiazione = _premiazione(pk)
        soci = calcolo.calcola(premiazione)
        registra("calcola_premiazione", request, oggetto=f"premiazione {pk}", soci=soci)
        messages.success(request, f"Lista calcolata: {soci} soci da premiare.")
    return _esegui(request, pk, azione)


@require_POST
@permission_required("benemerenze.change_premiazione", raise_exception=True)
def escludi(request, pk, socio):
    def azione():
        motivo = request.POST.get("motivo", "").strip()[:200]
        if request.POST.get("riammetti"):
            motivo = ""
        elif not motivo:
            raise calcolo.PremiazioneError("Per escludere un socio serve il motivo.")
        calcolo.escludi(_premiazione(pk), socio, motivo)
        # il motivo è testo libero: nel registro va solo il socio
        registra("escludi_premiato" if motivo else "riammetti_premiato", request, oggetto=f"premiazione {pk}",
                 socio=socio)
    return _esegui(request, pk, azione)


@require_POST
@permission_required("benemerenze.change_premiazione", raise_exception=True)
def aggiungi(request, pk):
    def azione():
        form = AggiuntaForm(request.POST)
        if not form.is_valid():
            errori = [e for campo in form.errors.values() for e in campo]
            raise calcolo.PremiazioneError(f"Socio non aggiunto: {' '.join(errori)}")
        premiato = calcolo.aggiungi(_premiazione(pk), form.cleaned_data["socio"], form.cleaned_data["benemerenza"],
                                    form.cleaned_data["motivo"])
        registra("aggiungi_premiato", request, oggetto=f"premiazione {pk}", socio=premiato.socio_id,
                 benemerenza=premiato.benemerenza.codice)
        messages.success(request, f"Aggiunto il socio {premiato.socio_id}.")
    return _esegui(request, pk, azione)


@require_POST
@permission_required("benemerenze.assegna_premiazione", raise_exception=True)
def assegna(request, pk):
    def azione():
        if not request.POST.get("conferma"):
            raise calcolo.PremiazioneError("Spunta la conferma: l'assegnazione non si può annullare.")
        assegnate = calcolo.assegna(_premiazione(pk))
        registra("assegna_premiazione", request, oggetto=f"premiazione {pk}", benemerenze=assegnate)
        messages.success(request, f"Premiazione assegnata: registrate {assegnate} benemerenze.")
    return _esegui(request, pk, azione)


@require_POST
@permission_required("benemerenze.delete_premiazione", raise_exception=True)
def elimina(request, pk):
    try:
        _premiazione(pk, modificabile=True).delete()
    except calcolo.PremiazioneError as errore:
        messages.error(request, str(errore))
        return redirect("premiazione", pk=pk)
    registra("elimina_premiazione", request, oggetto=f"premiazione {pk}")
    messages.success(request, "Premiazione eliminata.")
    return redirect("benemerenze")


@permission_required("benemerenze.view_premiazione", raise_exception=True)
def esporta(request, pk):
    premiazione = get_object_or_404(Premiazione, pk=pk)
    premiati, esclusi = calcolo.premiati_per_socio(premiazione)
    registra("export_premiazione", request, oggetto=f"premiazione {pk}")
    risposta = HttpResponse(excel.cartella(premiazione, premiati, esclusi), content_type=XLSX)
    risposta["Content-Disposition"] = f'attachment; filename="premiazione_{premiazione.data_consegna:%Y-%m-%d}.xlsx"'
    return risposta
