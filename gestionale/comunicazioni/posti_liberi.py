"""Job "Posti liberi" (README M6): SMS ai donatori, da AssoWeb, per una giornata di donazione con posti liberi.

Lo esegue il worker (core/worker.py). Come per Aggiorna dati, Playwright gira in un thread separato. Parametri
dell'esecuzione: `giornata` (ISO), `testo` dell'SMS, `modalita` e `filtro`, il filtro salvato su AssoWeb che sceglie i
destinatari. Alla fine il job aggiunge `destinatari`, il numero dei donatori selezionati. Non c'è una tabella delle
comunicazioni: cosa è stato inviato, quando e da chi resta nell'esecuzione.
"""

import datetime

from django.conf import settings
from django.db import models

from gestionale.core.eventi import registra
from gestionale.core.worker import esegui_in_thread
from gestionale.portali.assoweb import invia_sms


class Modalita(models.TextChoices):
    INVIO = "invio", "Invio ai donatori"
    # lo stesso percorso con il filtro di prova, che trova solo l'operatore
    PROVA = "prova", "Prova"
    # il filtro dei donatori, ma la finestra Invia SMS si chiude con Annulla
    SIMULAZIONE = "simulazione", "Simulazione"


def filtro(modalita):
    """Nome del filtro salvato su AssoWeb per la modalità."""
    return settings.ASSOWEB_FILTRO_PROVA if modalita == Modalita.PROVA else settings.ASSOWEB_FILTRO_POSTI_LIBERI


def invia(esecuzione, log):
    parametri = esecuzione.parametri
    giornata = datetime.date.fromisoformat(parametri["giornata"])
    simulazione = parametri["modalita"] == Modalita.SIMULAZIONE

    log(f"SMS dei posti liberi del {giornata:%d/%m/%Y} ai donatori del filtro {parametri['filtro']} di AssoWeb"
        + (" (simulazione: si ferma prima dell'invio)" if simulazione else ""))
    destinatari = esegui_in_thread(log, invia_sms, settings.ASSOWEB_USERNAME, settings.ASSOWEB_PASSWORD,
                                   parametri["filtro"], parametri["testo"], giornata=giornata, invia=not simulazione,
                                   headless=settings.PORTALI_HEADLESS, cartella_errori=settings.PORTALI_CARTELLA_ERRORI)
    esecuzione.parametri["destinatari"] = destinatari
    esecuzione.save(update_fields=["parametri"])
    registra("posti_liberi", utente=esecuzione.utente, oggetto=f"esecuzione {esecuzione.pk}",
             giornata=parametri["giornata"], modalita=parametri["modalita"], filtro=parametri["filtro"],
             destinatari=destinatari)

    if not destinatari:
        log("Il filtro non ha trovato donatori: nessun SMS inviato")
    elif simulazione:
        log(f"Simulazione completata: l'SMS andrebbe a {destinatari} donatori. Nessun SMS inviato")
    else:
        log(f"SMS inviato a {destinatari} donatori")
