import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from gestionale.core.eventi import registra
from gestionale.core.models import Sezione
from gestionale.portali.assoweb import ErrorePortale, scarica


class Command(BaseCommand):
    help = "Scarica da AssoWeb i CSV dei donatori e delle donazioni dell'anno, da importare con importa_assoweb."

    def add_arguments(self, parser):
        parser.add_argument("--anno", type=int, default=datetime.date.today().year,
                            help="anno delle donazioni (default: anno corrente)")
        parser.add_argument("--sezione", default="1529", help="codice della sezione (default: 1529)")
        parser.add_argument("--cartella", default=settings.CARTELLA_DOWNLOAD,
                            help="dove salvare i due CSV (default: download/ nella root del progetto)")
        parser.add_argument("--mostra-browser", action="store_true",
                            help="apre il browser in una finestra anche con PORTALI_HEADLESS=1")

    def handle(self, *args, **opzioni):
        try:
            sezione = Sezione.objects.get(codice=opzioni["sezione"])
        except Sezione.DoesNotExist:
            raise CommandError(f"Sezione {opzioni['sezione']} non trovata")
        if not sezione.nome_assoweb:
            raise CommandError(f"La sezione {sezione.codice} non ha il nome con cui compare in AssoWeb")

        # niente ORM fino alla fine del download: dentro Playwright Django lo rifiuta
        cartella, anno = Path(opzioni["cartella"]), opzioni["anno"]
        try:
            percorsi = scarica(settings.ASSOWEB_USERNAME, settings.ASSOWEB_PASSWORD, sezione.nome_assoweb, cartella,
                               anno=anno, headless=settings.PORTALI_HEADLESS and not opzioni["mostra_browser"],
                               cartella_errori=settings.PORTALI_CARTELLA_ERRORI, avanza=self.stdout.write)
        except ErrorePortale as errore:
            dettaglio = f"\nScreenshot della pagina: {errore.screenshot}" if errore.screenshot else ""
            raise CommandError(f"Download da AssoWeb fallito: {errore}{dettaglio}")

        registra("download_assoweb", oggetto=str(cartella), nome_utente="manage.py scarica_assoweb",
                 sezione=sezione.codice, anno=anno)
        for percorso in percorsi:
            self.stdout.write(f"  {percorso}")
        self.stdout.write(self.style.SUCCESS("Download completato. Per caricarli nel DB: manage.py importa_assoweb"))
