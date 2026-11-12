"""Posti liberi (README M6): giornata e testo scelti dal volontario, due conferme, poi il worker manda l'SMS da AssoWeb.

La pagina e lo storico richiedono `core.view_esecuzione`, l'invio `core.avvia_posti_liberi` (Volontario e
Amministratore, README §2). Il primo POST del form non invia niente: mostra il riepilogo da confermare.
"""

import datetime

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.text import capfirst
from django.views.decorators.http import require_POST

from gestionale.core.forms import CampoData, FormBootstrap
from gestionale.core.models import STATI_ATTIVI, Esecuzione, StatoEsecuzione, TipoEsecuzione

from .posti_liberi import Modalita, filtro

SEGNAPOSTO = "[giornata]"
# testo dell'avviso che la sezione inviava da AssoWeb, con la data al posto del segnaposto
TESTO_INIZIALE = f"""AVVISO AI DONATORI

Sono disponibili ancora posti per le donazioni:

{SEGNAPOSTO}

Prenota la tua donazione su sit-donor.azero.veneto.it
Chiama il 335 1358623
oppure WhatsApp 335 1358621

Grazie
Avis Altopiano"""


class PostiLiberiForm(FormBootstrap, forms.Form):
    giornata = forms.DateField(label="Giornata di donazione", widget=CampoData,
                               help_text="L'SMS va ai donatori che per quel giorno possono donare.")
    testo = forms.CharField(label="Testo dell'SMS", max_length=1000, widget=forms.Textarea(attrs={"rows": 13}),
                            help_text=f"{SEGNAPOSTO} diventa la giornata scelta, es. «Lunedì 9 novembre».")
    modalita = forms.ChoiceField(label="Destinatari", widget=forms.RadioSelect(attrs={"class": "form-check-input"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["modalita"].choices = [
            (Modalita.INVIO, f"Donatori del filtro «{filtro(Modalita.INVIO)}» di AssoWeb"),
            (Modalita.PROVA, f"Prova: solo chi trova il filtro «{filtro(Modalita.PROVA)}»"),
            (Modalita.SIMULAZIONE, "Simulazione: cerca e seleziona i donatori, ma non invia l'SMS"),
        ]

    def clean_giornata(self):
        giornata = self.cleaned_data["giornata"]
        if giornata < timezone.localdate():
            raise ValidationError("La giornata è già passata.")
        return giornata

    def clean(self):
        dati = super().clean()
        if "giornata" in dati and "testo" in dati:
            # il testo come arriverà ai donatori; Django scrive i mesi con la maiuscola
            dati["sms"] = dati["testo"].replace("\r\n", "\n").replace(
                SEGNAPOSTO, capfirst(date_format(dati["giornata"], "l j F").lower()))
        return dati


def _esecuzioni():
    return Esecuzione.objects.filter(tipo=TipoEsecuzione.POSTI_LIBERI).select_related("utente")


def _attiva():
    return _esecuzioni().filter(stato__in=STATI_ATTIVI).first()


def _conferma(request, form, errore=""):
    dati = form.cleaned_data
    contesto = {
        "form": form,
        "dati": dati,
        "modalita": Modalita(dati["modalita"]),
        "filtro": filtro(dati["modalita"]),
        "attiva": _attiva(),
        # avvisi già partiti per la stessa giornata
        "gia_inviati": list(_esecuzioni().filter(stato=StatoEsecuzione.COMPLETATA, parametri__modalita=Modalita.INVIO,
                                                 parametri__giornata=dati["giornata"].isoformat())),
        "errore": errore,
    }
    return render(request, "comunicazioni/conferma_posti_liberi.html", contesto)


@permission_required("core.view_esecuzione", raise_exception=True)
def posti_liberi(request):
    if request.method == "POST":
        if not request.user.has_perm("core.avvia_posti_liberi"):
            raise PermissionDenied
        form = PostiLiberiForm(request.POST)
        # "Modifica" dal riepilogo torna al form con i dati inseriti
        if form.is_valid() and "modifica" not in request.POST:
            return _conferma(request, form)
    else:
        form = PostiLiberiForm(initial={"testo": TESTO_INIZIALE, "modalita": Modalita.INVIO})

    esecuzioni = list(_esecuzioni()[:20])
    for e in esecuzioni:
        e.giornata = datetime.date.fromisoformat(e.parametri["giornata"])
        e.modalita = Modalita(e.parametri["modalita"]).label
    contesto = {"form": form, "attiva": _attiva(), "esecuzioni": esecuzioni}
    return render(request, "comunicazioni/posti_liberi.html", contesto)


@require_POST
@permission_required("core.avvia_posti_liberi", raise_exception=True)
def invia(request):
    """Seconda conferma: mette in coda il job, uno alla volta."""
    form = PostiLiberiForm(request.POST)
    if not form.is_valid():
        messages.error(request, "I dati dell'avviso non sono validi: vanno inseriti di nuovo.")
        return redirect("posti_liberi")
    if request.POST.get("conferma") != "1":
        return _conferma(request, form, "Per inviare va spuntata la conferma.")
    if _attiva():
        messages.warning(request, "C'è già un avviso dei posti liberi in coda o in corso.")
        return redirect("posti_liberi")
    dati = form.cleaned_data
    esecuzione = Esecuzione.objects.create(
        tipo=TipoEsecuzione.POSTI_LIBERI, utente=request.user,
        parametri={"giornata": dati["giornata"].isoformat(), "testo": dati["sms"], "modalita": dati["modalita"],
                   "filtro": filtro(dati["modalita"])})
    messages.success(request, "Avviso dei posti liberi messo in coda.")
    return redirect("esecuzione", pk=esecuzione.pk)
