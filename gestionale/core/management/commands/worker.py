import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from gestionale.core.worker import esegui, prossima, recupera_interrotte


class Command(BaseCommand):
    help = "Esegue i job in coda (Aggiorna dati, ...). Resta attivo finché non lo si ferma con Ctrl+C."

    def add_arguments(self, parser):
        parser.add_argument("--una-volta", action="store_true", help="esegue i job già in coda ed esce")
        parser.add_argument("--intervallo", type=float, default=3,
                            help="secondi tra due controlli della coda (default: 3)")

    def handle(self, *args, **opzioni):
        if interrotte := recupera_interrotte():
            self.stdout.write(f"Esecuzioni interrotte segnate come fallite: {interrotte}")
        if not opzioni["una_volta"]:
            self.stdout.write("Worker avviato, in attesa di job (Ctrl+C per fermarlo)")
        try:
            while True:
                esecuzione = prossima()
                if esecuzione:
                    self.stdout.write(f"{esecuzione}: avviata")
                    esegui(esecuzione)
                    self.stdout.write(f"{esecuzione}: {esecuzione.get_stato_display().lower()}")
                    continue
                if opzioni["una_volta"]:
                    return
                # il worker resta acceso per giorni: una connessione caduta (es. riavvio di PostgreSQL) si riapre
                close_old_connections()
                time.sleep(opzioni["intervallo"])
        except KeyboardInterrupt:
            self.stdout.write("Worker fermato")
