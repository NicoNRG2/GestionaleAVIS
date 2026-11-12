from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import ProtectedError

from gestionale.core.eventi import registra
from gestionale.importazioni.anomalie import righe_anomalie
from gestionale.importazioni.assoavis import ImportazioneError, azzera, importa, leggi_storico


def _n(numero):
    return f"{numero:,}".replace(",", ".")


class Command(BaseCommand):
    help = "Importa una tantum lo storico AssoAVIS dal DB SQL Server ASSOAVIS_STORICO (solo sul PC di sviluppo)."

    def add_arguments(self, parser):
        parser.add_argument("--azzera", action="store_true",
                            help="cancella prima tutti i soci, le donazioni, le benemerenze e le premiazioni")

    def handle(self, *args, **opzioni):
        try:
            # gruppo di dipendenze "migrazione": serve solo sul PC che ha lo storico
            import pyodbc
        except ImportError:
            raise CommandError("pyodbc non è installato: serve il gruppo di dipendenze 'migrazione' (uv sync).")

        self.stdout.write("Lettura di ASSOAVIS_STORICO...")
        connessione = pyodbc.connect(settings.ASSOAVIS_ODBC, readonly=True)
        try:
            storico = leggi_storico(connessione.cursor())
        finally:
            connessione.close()
        self.stdout.write(f"Lette {_n(len(storico.soci))} anagrafiche e {_n(len(storico.donazioni))} donazioni.")

        try:
            with transaction.atomic():
                if opzioni["azzera"]:
                    azzera()
                report = importa(storico)
                registra("import_storico", oggetto="ASSOAVIS_STORICO", nome_utente="manage.py importa_assoavis",
                         righe={voce: nuovo_db for voce, _, nuovo_db, _, _ in report.righe},
                         anomalie=len(report.anomalie), coincide=report.coincide)
        except ImportazioneError as errore:
            raise CommandError(f"{errore} Per rifarlo da zero usa --azzera.")
        except ProtectedError:
            raise CommandError("Ci sono invii SIAN collegati ai soci: --azzera non li cancella.")
        self._stampa(report)

    def _stampa(self, report):
        self.stdout.write("\nRiconciliazione AssoAVIS -> nuovo DB")
        larghezza = max(len(voce) for voce, *_ in report.righe) + 2
        self.stdout.write(f"{'':3}{'voce':{larghezza}}{'AssoAVIS':>10}{'nuovo DB':>10}  note")
        for voce, assoavis, nuovo_db, attesa, nota in report.righe:
            esito = self.style.SUCCESS("ok ") if assoavis + attesa == nuovo_db else self.style.ERROR("NO ")
            self.stdout.write(f"{esito}{voce:{larghezza}}{_n(assoavis):>10}{_n(nuovo_db):>10}  {nota}")

        self.stdout.write("")
        for riga in righe_anomalie(report.anomalie):
            self.stdout.write(riga)

        if report.coincide:
            self.stdout.write(self.style.SUCCESS("\nImport completato: i conteggi coincidono."))
        else:
            self.stdout.write(self.style.ERROR("\nImport completato, ma alcuni conteggi non coincidono: vedi le righe NO."))
