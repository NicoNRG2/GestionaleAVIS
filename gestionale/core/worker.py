"""Job in background (README §5): la coda è la tabella Esecuzione, il worker è `manage.py worker`.

Un job è una funzione `job(esecuzione, log)`: legge i parametri dall'esecuzione e scrive l'avanzamento con
`log(riga, ...)`, che salva subito, così la pagina lo mostra mentre il job gira. Se il job solleva un'eccezione
l'esecuzione è fallita, altrimenti è completata. Si prevede un solo worker per installazione.
"""

import logging
import queue
import traceback
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from django.db import transaction
from django.utils import timezone
from django.utils.module_loading import import_string

from .models import Esecuzione, StatoEsecuzione, TipoEsecuzione

logger = logging.getLogger(__name__)

# tipo di esecuzione -> funzione del job
JOB = {
    TipoEsecuzione.AGGIORNA_DATI: "gestionale.importazioni.aggiorna.aggiorna_dati",
    TipoEsecuzione.INVIO_SIAN: "gestionale.sian.invio.invia",
    TipoEsecuzione.POSTI_LIBERI: "gestionale.comunicazioni.posti_liberi.invia",
}


def scrivi_log(esecuzione, *righe):
    """Aggiunge le righe al log e salva; l'ora va sulla prima, le altre sono allineate sotto."""
    ora = timezone.localtime().strftime("%H:%M:%S")
    esecuzione.log += "".join(f"{ora if i == 0 else '':8} {riga}".rstrip() + "\n" for i, riga in enumerate(righe))
    esecuzione.save(update_fields=["log"])


def prossima():
    """Prende l'esecuzione in coda più vecchia e la segna in corso; None se la coda è vuota."""
    with transaction.atomic():
        esecuzione = (Esecuzione.objects.select_for_update(skip_locked=True).filter(stato=StatoEsecuzione.IN_CODA)
                      .order_by("creata_il").first())
        if esecuzione:
            esecuzione.stato, esecuzione.iniziata_il = StatoEsecuzione.IN_CORSO, timezone.now()
            esecuzione.save(update_fields=["stato", "iniziata_il"])
    return esecuzione


def esegui(esecuzione):
    log = partial(scrivi_log, esecuzione)
    try:
        import_string(JOB[esecuzione.tipo])(esecuzione, log)
    except Exception as errore:
        # solo la prima riga del messaggio, nel log e in console: gli errori del DB riportano nelle righe
        # successive i valori dei campi, cioè dati personali
        messaggio = _prima_riga(errore)
        logger.error("Esecuzione %s fallita (%s: %s)\n%s", esecuzione.pk, type(errore).__name__, messaggio,
                     "".join(traceback.format_tb(errore.__traceback__)))
        log(f"Errore: {messaggio}")
        # es. ErrorePortale dei portali: l'immagine della pagina al momento dell'errore
        if screenshot := getattr(errore, "screenshot", None):
            log(f"Screenshot della pagina: {screenshot}")
        esecuzione.stato = StatoEsecuzione.FALLITA
    else:
        esecuzione.stato = StatoEsecuzione.COMPLETATA
    esecuzione.terminata_il = timezone.now()
    esecuzione.save(update_fields=["stato", "terminata_il"])


def _segna_fallite(stato, motivo):
    """Segna come fallite le esecuzioni nello stato indicato, con il motivo nel log; restituisce quante sono."""
    esecuzioni = list(Esecuzione.objects.filter(stato=stato))
    for esecuzione in esecuzioni:
        scrivi_log(esecuzione, motivo)
        esecuzione.stato, esecuzione.terminata_il = StatoEsecuzione.FALLITA, timezone.now()
        esecuzione.save(update_fields=["stato", "terminata_il"])
    return len(esecuzioni)


def recupera_interrotte():
    """All'avvio del worker le esecuzioni ancora in corso sono state interrotte (worker fermato, PC spento)."""
    return _segna_fallite(StatoEsecuzione.IN_CORSO, "Interrotta: il worker si è fermato durante l'esecuzione")


def annulla_in_coda():
    """Segna come fallite le esecuzioni rimaste in coda da una sessione precedente.

    Le chiama `manage.py avvia` prima di avviare il worker: il gestionale si accende poche volte all'anno e un job
    rimasto in coda potrebbe essere superato, come l'SMS dei posti liberi per una giornata già passata.
    """
    return _segna_fallite(StatoEsecuzione.IN_CODA, "Annullata: era in coda da una sessione precedente del gestionale")


def esegui_in_thread(log, funzione, *args, **kwargs):
    """Esegue `funzione(*args, avanza=..., **kwargs)` in un thread separato e ne restituisce il risultato.

    Serve per Playwright: nel suo thread Django rifiuta ogni accesso al DB (SynchronousOnlyOperation). I messaggi
    di `avanza` passano da una coda e li scrive nel log il thread chiamante, che può usare l'ORM.
    """
    messaggi = queue.Queue()
    with ThreadPoolExecutor(max_workers=1) as esecutore:
        futuro = esecutore.submit(funzione, *args, avanza=messaggi.put, **kwargs)
        while not (futuro.done() and messaggi.empty()):
            try:
                log(messaggi.get(timeout=0.5))
            except queue.Empty:
                pass
    return futuro.result()


def _prima_riga(errore):
    righe = str(errore).strip().splitlines()
    return righe[0] if righe else type(errore).__name__
