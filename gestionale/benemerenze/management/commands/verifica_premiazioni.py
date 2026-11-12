from django.core.management.base import BaseCommand

from gestionale.benemerenze.verifica import verifica


class Command(BaseCommand):
    help = ("Ricalcola le premiazioni storiche di AssoAVIS e confronta le donazioni valide ed effettive dei premiati "
            "con quelle salvate allora.")

    def handle(self, *args, **opzioni):
        righe = verifica()
        self.stdout.write(f"{'premiazione':<24}{'statuto':<14}{'premiati':>9}{'effettive':>11}{'valide':>9}"
                          f"{'maturate':>10}")
        for r in righe:
            effettive = f"{r.effettive_uguali}" if r.effettive_salvate else "n.d."
            valide = f"{r.valide_uguali}/{r.effettive_uguali}"
            esito = self.style.SUCCESS("ok") if not r.valide_diverse else self.style.WARNING("da guardare")
            nome = f"{r.premiazione.codice_assoavis} del {r.premiazione.data_consegna:%d/%m/%Y}"
            self.stdout.write(f"{nome:<24}{r.statuto:<14}{r.premiati:>9}{effettive:>11}{valide:>9}{r.maturate:>10}"
                              f"  {esito}")
        self.stdout.write("")
        self.stdout.write("effettive: premiati con le donazioni effettive uguali a quelle salvate da AssoAVIS (n.d. = "
                          "non salvate, si confrontano le sole valide)")
        self.stdout.write("valide: tra questi, quelli con le donazioni valide uguali")
        self.stdout.write("maturate: premiati che hanno i requisiti della benemerenza con i dati di oggi")
        collaboratori = sum(r.collaboratori for r in righe)
        if collaboratori:
            self.stdout.write(f"Non confrontate {collaboratori} benemerenze dei collaboratori, che non si calcolano.")
        for r in righe:
            if r.valide_diverse:
                soci = ", ".join(f"socio {s}" for s in r.valide_diverse)
                self.stdout.write(f"Premiazione {r.premiazione.codice_assoavis}, valide diverse: {soci}")
