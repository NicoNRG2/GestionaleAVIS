from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from gestionale.core.eventi import registra
from gestionale.importazioni.assoavis import ImportazioneError
from gestionale.importazioni.assoweb import FILE_DONATORI, FILE_DONAZIONI, importa, leggi_csv, righe_report
from gestionale.soci.stati import descrivi_passaggi


class Command(BaseCommand):
    help = f"Importa {FILE_DONATORI} e {FILE_DONAZIONI} scaricati da AssoWeb e aggiorna lo stato dei soci."

    def add_arguments(self, parser):
        parser.add_argument("--cartella", default=settings.CARTELLA_DOWNLOAD,
                            help="cartella dei due CSV (default: download/ nella root del progetto)")
        parser.add_argument("--tutte", action="store_true",
                            help="mostra tutte le anomalie, non solo le prime 10 per tipo")

    def handle(self, *args, **opzioni):
        cartella = Path(opzioni["cartella"])
        try:
            donatori, donazioni = leggi_csv(cartella / FILE_DONATORI), leggi_csv(cartella / FILE_DONAZIONI)
        except FileNotFoundError as errore:
            raise CommandError(f"File non trovato: {errore.filename}")

        try:
            with transaction.atomic():
                report = importa(donatori, donazioni)
                registra("import_assoweb", oggetto=str(cartella), nome_utente="manage.py importa_assoweb",
                         conteggi=dict(report.conteggi), stati=descrivi_passaggi(report.passaggi_stato),
                         anomalie=len(report.anomalie))
        except ImportazioneError as errore:
            raise CommandError(str(errore))

        for riga in righe_report(report, massimo=None if opzioni["tutte"] else 10):
            self.stdout.write(riga)
        self.stdout.write(self.style.SUCCESS("\nImport completato."))
