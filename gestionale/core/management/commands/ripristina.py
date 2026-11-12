from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from gestionale.core import backup
from gestionale.core.eventi import registra


class Command(BaseCommand):
    help = ("Ripristina un backup fatto con manage.py backup. Con --prova lo ripristina in un DB temporaneo e "
            "confronta il numero di righe con il DB attuale, senza toccarlo.")

    def add_arguments(self, parser):
        parser.add_argument("file", type=Path, help="file .dump della cartella dei backup")
        parser.add_argument("--prova", action="store_true", help="prova il ripristino senza toccare il DB")
        parser.add_argument("--noinput", action="store_true", help="non chiede conferma (solo senza --prova)")

    def handle(self, *args, **opzioni):
        file = opzioni["file"]
        if not file.is_file():
            raise CommandError(f"Il file {file} non esiste")
        try:
            if opzioni["prova"]:
                self._prova(file)
            else:
                self._ripristina(file, opzioni["noinput"])
        except backup.ErroreBackup as errore:
            raise CommandError(f"Ripristino non riuscito: {errore}")

    def _prova(self, file):
        nel_backup, attuali = backup.prova_ripristino(file)
        righe = sum(nel_backup.values())
        self.stdout.write(self.style.SUCCESS(f"Ripristino riuscito in un DB temporaneo, poi cancellato: "
                                             f"{len(nel_backup)} tabelle, {righe} righe"))
        diverse = sorted(t for t in nel_backup.keys() | attuali.keys() if nel_backup.get(t) != attuali.get(t))
        if not diverse:
            self.stdout.write("Il numero di righe di ogni tabella coincide con il DB attuale")
            return
        self.stdout.write("Tabelle con un numero di righe diverso dal DB attuale (cambiato dopo il backup?):")
        for tabella in diverse:
            self.stdout.write(f"  {tabella}: {nel_backup.get(tabella, '-')} nel backup, "
                              f"{attuali.get(tabella, '-')} nel DB")

    def _ripristina(self, file, noinput):
        nome = settings.DATABASES["default"]["NAME"]
        if not noinput:
            self.stdout.write(f"Il contenuto del DB {nome} verrà sostituito con il backup {file.name}.\n"
                              "Prima si salva un backup di sicurezza del DB attuale. Web e worker devono essere "
                              "chiusi.")
            if input(f"Per confermare scrivi il nome del DB ({nome}): ").strip() != nome:
                raise CommandError("Ripristino annullato")
        # la connessione di questo comando conterebbe tra quelle aperte, che bloccano il ripristino
        connection.close()
        sicurezza = backup.ripristina(file)
        registra("ripristino", oggetto=file.name, nome_utente="manage.py ripristina", backup_prima=sicurezza.name)
        self.stdout.write(self.style.SUCCESS(f"DB ripristinato da {file.name}. Il DB di prima è nel backup "
                                             f"{sicurezza}"))
        self.stdout.write("Se il backup viene da una versione precedente del gestionale, lancia manage.py migrate")
