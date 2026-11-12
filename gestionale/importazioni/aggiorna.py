"""Job "Aggiorna dati" (README M1): scarica i due CSV da AssoWeb e li importa nel DB.

Lo esegue il worker (core/worker.py). Il download con Playwright gira in un thread separato, perché nel thread
di Playwright Django non permette l'accesso al DB; l'import e il registro eventi restano in questo thread.
Parametri dell'esecuzione: `anno` delle donazioni e codice della `sezione`.
"""

from django.conf import settings
from django.db import transaction

from gestionale.core.eventi import registra
from gestionale.core.models import Sezione
from gestionale.core.worker import esegui_in_thread
from gestionale.portali.assoweb import scarica
from gestionale.soci.stati import descrivi_passaggi

from .assoweb import FILE_DONATORI, FILE_DONAZIONI, importa, leggi_csv, righe_report


def aggiorna_dati(esecuzione, log):
    anno = esecuzione.parametri["anno"]
    sezione = Sezione.objects.get(codice=esecuzione.parametri["sezione"])
    cartella = settings.CARTELLA_DOWNLOAD
    oggetto = f"esecuzione {esecuzione.pk}"

    log(f"Download da AssoWeb dei donatori e delle donazioni del {anno}")
    esegui_in_thread(log, scarica, settings.ASSOWEB_USERNAME, settings.ASSOWEB_PASSWORD, sezione.nome_assoweb,
                     cartella, anno=anno, headless=settings.PORTALI_HEADLESS,
                     cartella_errori=settings.PORTALI_CARTELLA_ERRORI)
    registra("download_assoweb", utente=esecuzione.utente, oggetto=oggetto, sezione=sezione.codice, anno=anno)

    log("Import nel database")
    with transaction.atomic():
        report = importa(leggi_csv(cartella / FILE_DONATORI), leggi_csv(cartella / FILE_DONAZIONI))
        registra("import_assoweb", utente=esecuzione.utente, oggetto=oggetto, conteggi=dict(report.conteggi),
                 stati=descrivi_passaggi(report.passaggi_stato), anomalie=len(report.anomalie))
    log(*righe_report(report))
