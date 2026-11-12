from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied
from django.db.models import Max
from django.shortcuts import redirect, render
from django.utils import timezone

from gestionale.core.forms import FormBootstrap
from gestionale.core.models import STATI_ATTIVI, Esecuzione, Sezione, TipoEsecuzione
from gestionale.soci.models import Socio


class AggiornaDatiForm(FormBootstrap, forms.Form):
    anno = forms.TypedChoiceField(label="Donazioni dell'anno", coerce=int)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        anno = timezone.localdate().year
        # a gennaio conviene riscaricare anche l'anno appena chiuso (README M1)
        self.fields["anno"].choices = [(anno, anno), (anno - 1, anno - 1)]


@permission_required("core.view_esecuzione", raise_exception=True)
def aggiorna_dati(request):
    """Mette in coda il job Aggiorna dati (download da AssoWeb e import) e mostra lo storico delle esecuzioni."""
    esecuzioni = Esecuzione.objects.filter(tipo=TipoEsecuzione.AGGIORNA_DATI).select_related("utente")
    attiva = esecuzioni.filter(stato__in=STATI_ATTIVI).first()
    form = AggiornaDatiForm(request.POST or None)

    if request.method == "POST":
        if not request.user.has_perm("core.avvia_aggiorna_dati"):
            raise PermissionDenied
        sezione = Sezione.objects.exclude(nome_assoweb="").first()
        if attiva:
            messages.warning(request, "C'è già un aggiornamento in coda o in corso.")
        elif sezione is None:
            messages.error(request, "Nessuna sezione ha il nome con cui compare in AssoWeb: va impostato "
                                    "nell'amministrazione.")
        elif form.is_valid():
            Esecuzione.objects.create(tipo=TipoEsecuzione.AGGIORNA_DATI, utente=request.user,
                                      parametri={"anno": form.cleaned_data["anno"], "sezione": sezione.codice})
            messages.success(request, "Aggiornamento messo in coda.")
        return redirect("aggiorna_dati")

    contesto = {
        "form": form,
        "attiva": attiva,
        "esecuzioni": list(esecuzioni[:20]),
        "ultimo_aggiornamento": Socio.objects.aggregate(m=Max("aggiornato_assoweb_il"))["m"],
    }
    return render(request, "importazioni/aggiorna_dati.html", contesto)
