from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from gestionale.core.eventi import registra
from gestionale.importazioni.unione import UnioneError, unisci_coppie
from gestionale.soci.stati import descrivi_passaggi


class AnnullaProva(Exception):
    pass


def _coppia(testo):
    try:
        storico, doppione = (int(pk) for pk in testo.split(":"))
    except ValueError:
        raise CommandError(f"Coppia non valida: {testo!r}. Il formato è <id storico>:<id doppione>.")
    return storico, doppione


class Command(BaseCommand):
    help = ("Unisce i doppioni creati dall'import AssoWeb alla scheda dello storico AssoAVIS: sposta le donazioni, "
            "prende i dati di AssoWeb e cancella il doppione.")

    def add_arguments(self, parser):
        parser.add_argument("coppie", nargs="+", metavar="STORICO:DOPPIONE", help="ID dei due soci nel DB")
        parser.add_argument("--prova", action="store_true", help="mostra cosa cambierebbe senza salvare")

    def handle(self, *args, **opzioni):
        coppie = [_coppia(testo) for testo in opzioni["coppie"]]
        try:
            with transaction.atomic():
                risultati, passaggi = unisci_coppie(coppie)
                if opzioni["prova"]:
                    raise AnnullaProva
                registra("unione_soci", oggetto=f"{len(coppie)} doppioni", nome_utente="manage.py unisci_soci",
                         coppie=[f"socio {s} <- socio {d}" for s, d in coppie],
                         donazioni_spostate=sum(r["donazioni spostate"] for r in risultati.values()))
        except UnioneError as errore:
            raise CommandError(f"{errore}. Nessuna modifica salvata.")
        except AnnullaProva:
            pass

        for (storico, doppione), r in risultati.items():
            self.stdout.write(f"socio {storico} <- socio {doppione}: {r['donazioni spostate']} donazioni spostate, "
                              f"{r['donazioni già presenti']} già presenti; campi: {', '.join(r['campi']) or '-'}")
        self.stdout.write("\nStato dei soci")
        for riga in descrivi_passaggi(passaggi) or ["nessun cambiamento"]:
            self.stdout.write(f"  {riga}")
        if opzioni["prova"]:
            self.stdout.write(self.style.WARNING("\nProva: nessuna modifica salvata."))
        else:
            self.stdout.write(self.style.SUCCESS(f"\n{len(coppie)} doppioni uniti."))
