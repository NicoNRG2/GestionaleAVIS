from django.core.management.base import BaseCommand, CommandError

from gestionale.core import backup
from gestionale.core.eventi import registra


class Command(BaseCommand):
    help = ("Salva una copia del DB (pg_dump) nella cartella dei backup (CARTELLA_BACKUP) e cancella i più vecchi "
            "oltre BACKUP_DA_TENERE. La fa anche manage.py avvia alla chiusura.")

    def handle(self, *args, **opzioni):
        try:
            file, cancellati = backup.crea()
        except backup.ErroreBackup as errore:
            raise CommandError(f"Backup non riuscito: {errore}")
        registra("backup", oggetto=file.name, nome_utente="manage.py backup")
        self.stdout.write(self.style.SUCCESS(f"Backup salvato: {file} ({file.stat().st_size / 1e6:.1f} MB)"))
        if cancellati:
            self.stdout.write(f"Cancellati i {len(cancellati)} backup più vecchi")
