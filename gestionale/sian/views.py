"""Libro soci SIAN: estrazione, controlli, export per l'Uploader e invio con il web service (README M5).

Riservato agli amministratori (README §2): la pagina richiede `sian.view_inviosian`, l'invio
`core.avvia_invio_sian`.
"""

from collections import Counter

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from gestionale.core.eventi import registra
from gestionale.core.forms import FormBootstrap
from gestionale.core.models import STATI_ATTIVI, Esecuzione, Sezione, TipoEsecuzione
from gestionale.portali import sian

from . import excel, invio, libro_soci
from .models import EsitoSian, InvioSian, ModalitaSian

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
# IsAlive dalla pagina: breve, per non bloccarla se il SIAN non risponde
TIMEOUT_STATO = 10


def _anni():
    anno = timezone.localdate().year
    # a gennaio sul SIAN può essere ancora aperto il registro dell'anno prima
    return [anno, anno - 1]


def _anno(valore):
    try:
        anno = int(valore)
    except (TypeError, ValueError):
        return _anni()[0]
    return anno if anno in _anni() else _anni()[0]


class InvioForm(FormBootstrap, forms.Form):
    anno = forms.TypedChoiceField(coerce=int)
    conferma = forms.BooleanField(label="Ho controllato il libro soci. Il registro della sede sul SIAN viene "
                                        "sostituito da questo elenco, e le modifiche fatte a mano sul portale si "
                                        "perdono.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["anno"].choices = [(a, a) for a in _anni()]


class ProvaForm(FormBootstrap, forms.Form):
    anno = forms.TypedChoiceField(coerce=int)
    socio = forms.IntegerField(label="ID del socio", min_value=1,
                               help_text="Un socio già presente sul SIAN con i dati corretti.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["anno"].choices = [(a, a) for a in _anni()]


def _sezione():
    return Sezione.objects.order_by("codice").first()


def _attiva():
    return Esecuzione.objects.filter(tipo=TipoEsecuzione.INVIO_SIAN, stato__in=STATI_ATTIVI).first()


@permission_required("sian.view_inviosian", raise_exception=True)
def libro(request):
    anno = _anno(request.GET.get("anno"))
    libro = libro_soci.estrai(anno, _sezione(), timezone.localdate())
    invii = list(InvioSian.objects.select_related("esecuzione__utente")[:20])
    for i in invii:
        conteggi = Counter(i.righe.values_list("esito", flat=True))
        i.esiti = [(nome, conteggi[esito]) for esito, nome in EsitoSian.choices if conteggi[esito]]
    contesto = {
        "anno": anno,
        "anni": _anni(),
        "libro": libro,
        "riepilogo": libro.riepilogo(),
        "scarti": libro.motivi("scarti"),
        "avvisi": libro.motivi("avvisi"),
        "invio_form": InvioForm(initial={"anno": anno}),
        "prova_form": ProvaForm(initial={"anno": anno}),
        "attiva": _attiva(),
        "invii": invii,
    }
    return render(request, "sian/libro_soci.html", contesto)


@permission_required("sian.view_inviosian", raise_exception=True)
def stato_sian(request):
    """Riquadro con il registro aperto sul SIAN, caricato dalla pagina con HTMX per non rallentarla."""
    contesto = {"anno": _anno(request.GET.get("anno"))}
    if not settings.SIAN_TOKEN:
        contesto["errore"] = "Token del SIAN mancante: va impostato SIAN_TOKEN in .env."
    else:
        try:
            contesto["anno_registro"], _ = sian.is_alive(_sezione().codice, settings.SIAN_TOKEN,
                                                         url=settings.SIAN_URL, timeout=TIMEOUT_STATO)
        except sian.ErroreSian as errore:
            contesto["errore"] = str(errore)
    return render(request, "sian/_stato_sian.html", contesto)


@permission_required("sian.view_inviosian", raise_exception=True)
def esporta(request):
    anno = _anno(request.GET.get("anno"))
    libro = libro_soci.estrai(anno, _sezione(), timezone.localdate())
    registra("export_libro_soci", request, oggetto=f"libro soci {anno}", anno=anno, record=len(libro.inviabili))
    risposta = HttpResponse(excel.cartella(libro), content_type=XLSX)
    risposta["Content-Disposition"] = f'attachment; filename="libro_soci_{libro.sede}_{anno}.xlsx"'
    return risposta


def _accoda(request, parametri):
    if _attiva():
        messages.warning(request, "C'è già un invio al SIAN in coda o in corso.")
        return None
    esecuzione = Esecuzione.objects.create(tipo=TipoEsecuzione.INVIO_SIAN, utente=request.user,
                                           parametri={"sezione": _sezione().codice, **parametri})
    return esecuzione


@require_POST
@permission_required("core.avvia_invio_sian", raise_exception=True)
def invia(request):
    form = InvioForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Per inviare il registro completo va spuntata la conferma.")
        return redirect(f"{reverse('libro_soci')}?anno={_anno(request.POST.get('anno'))}")
    anno = form.cleaned_data["anno"]
    if esecuzione := _accoda(request, {"anno": anno, "modalita": ModalitaSian.TOTALE}):
        messages.success(request, f"Invio del libro soci {anno} messo in coda.")
        return redirect("esecuzione", pk=esecuzione.pk)
    return redirect(f"{reverse('libro_soci')}?anno={anno}")


@require_POST
@permission_required("core.avvia_invio_sian", raise_exception=True)
def prova(request):
    form = ProvaForm(request.POST)
    anno = _anno(request.POST.get("anno"))
    if not form.is_valid():
        messages.error(request, "Indica l'ID di un socio.")
        return redirect(f"{reverse('libro_soci')}?anno={anno}")
    socio = form.cleaned_data["socio"]
    if esecuzione := _accoda(request, {"anno": anno, "modalita": ModalitaSian.SINGOLO, "socio": socio}):
        messages.success(request, f"Invio del socio {socio} messo in coda.")
        return redirect("esecuzione", pk=esecuzione.pk)
    return redirect(f"{reverse('libro_soci')}?anno={anno}")


@permission_required("sian.view_inviosian", raise_exception=True)
def dettaglio(request, pk):
    invio_sian = get_object_or_404(InvioSian.objects.select_related("esecuzione__utente"), pk=pk)
    # prima i soci da sistemare
    ordine = [EsitoSian.ERRORE, EsitoSian.SCARTATO, EsitoSian.INVIATO, EsitoSian.OK]
    righe = sorted(invio_sian.righe.select_related("socio"), key=lambda r: (ordine.index(r.esito), r.socio_id or 0))
    conteggi = Counter(r.esito for r in righe)
    contesto = {
        "invio": invio_sian,
        "righe": righe,
        "conteggi": [(nome, conteggi[esito]) for esito, nome in EsitoSian.choices],
        "in_attesa": invio_sian.id_invio and any(r.esito == EsitoSian.INVIATO for r in righe),
    }
    return render(request, "sian/invio.html", contesto)


@require_POST
@permission_required("core.avvia_invio_sian", raise_exception=True)
def aggiorna_esito(request, pk):
    invio_sian = get_object_or_404(InvioSian, pk=pk)
    try:
        conteggi = invio.aggiorna_esito(invio_sian, settings.SIAN_TOKEN, settings.SIAN_URL)
    except sian.ErroreSian as errore:
        messages.error(request, str(errore))
    else:
        if conteggi is None:
            messages.info(request, "Il SIAN non ha ancora elaborato il registro: riprova tra qualche minuto.")
        else:
            registra("esito_invio_sian", request, oggetto=f"invio SIAN {invio_sian.pk}", **conteggi)
            messages.success(request, " ".join(invio.righe_esito(conteggi)))
    return redirect("invio_sian", pk=pk)
