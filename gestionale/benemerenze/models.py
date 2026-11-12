from django.db import models

from gestionale.soci.models import Fonte, Socio


class Benemerenza(models.Model):
    """Catalogo delle benemerenze; i codici sono quelli di AssoAVIS (TIPOONOREFICIENZA)."""

    codice = models.PositiveSmallIntegerField(unique=True)
    nome = models.CharField(max_length=60)
    per_collaboratori = models.BooleanField(default=False)
    ordine = models.PositiveSmallIntegerField(help_text="ordine crescente di importanza")
    # colonne flag/data della tabella DONATORE di AssoAVIS (nomi storici, non corrispondono al metallo)
    colonna_flag_assoavis = models.CharField(max_length=30, blank=True)
    colonna_data_assoavis = models.CharField(max_length=30, blank=True)

    class Meta:
        verbose_name_plural = "benemerenze"
        ordering = ["ordine"]

    def __str__(self):
        return self.nome


class BenemerenzaAssegnata(models.Model):
    """Benemerenza posseduta da un socio: sostituisce le 12 coppie flag/data di AssoAVIS."""

    socio = models.ForeignKey(Socio, on_delete=models.CASCADE, related_name="benemerenze")
    benemerenza = models.ForeignKey(Benemerenza, on_delete=models.PROTECT)
    # nello storico ci sono benemerenze con il flag ma senza data
    data = models.DateField(null=True, blank=True)
    fonte = models.CharField(max_length=10, choices=Fonte.choices)

    class Meta:
        verbose_name = "benemerenza assegnata"
        verbose_name_plural = "benemerenze assegnate"
        ordering = ["socio", "benemerenza__ordine"]
        constraints = [
            models.UniqueConstraint(fields=["socio", "benemerenza"], name="benemerenza_unica_per_socio"),
        ]

    def __str__(self):
        return f"{self.socio} - {self.benemerenza}"


class StatoPremiazione(models.TextChoices):
    BOZZA = "bozza", "Bozza"
    CALCOLATA = "calcolata", "Calcolata"
    ASSEGNATA = "assegnata", "Assegnata"


class Premiazione(models.Model):
    """Una cerimonia di consegna (AssoAVIS: PREMIAZIONI). Le regole di calcolo sono nel codice, non qui."""

    descrizione = models.CharField(max_length=120)
    data_consegna = models.DateField(help_text="data della cerimonia: diventa la data delle benemerenze assegnate")
    data_riferimento = models.DateField(null=True, blank=True, help_text="data a cui si contano le donazioni")
    # AssoAVIS: "Premia 'Ex soci' cancellati dopo il" (DATAEXSOCI); vuota = nessun ex socio
    ex_soci_cessati_dal = models.DateField("premia gli ex soci cessati dopo il", null=True, blank=True)
    stato = models.CharField(max_length=10, choices=StatoPremiazione.choices, default=StatoPremiazione.BOZZA)
    codice_assoavis = models.PositiveIntegerField(unique=True, null=True, blank=True, help_text="ID di PREMIAZIONI")
    creata_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "premiazioni"
        ordering = ["-data_consegna"]
        # preparare una premiazione spetta anche ai volontari, assegnarla solo agli amministratori (README §2)
        permissions = [("assegna_premiazione", "Può assegnare le benemerenze di una premiazione")]

    def __str__(self):
        return f"{self.descrizione} ({self.data_consegna:%d/%m/%Y})"


class Premiato(models.Model):
    """Avente diritto in una premiazione (AssoAVIS: PREMIAZIONIPREMIATI)."""

    premiazione = models.ForeignKey(Premiazione, on_delete=models.CASCADE, related_name="premiati")
    socio = models.ForeignKey(Socio, on_delete=models.PROTECT, related_name="premiazioni")
    benemerenza = models.ForeignKey(Benemerenza, on_delete=models.PROTECT)
    # donazioni valide dopo "Donne x2" e i limiti annui (NUMDONAZIONI) ed effettive (NUMDONAZEFF)
    donazioni_valide = models.PositiveIntegerField()
    donazioni_effettive = models.PositiveIntegerField()
    # revisione della lista: un escluso resta nella premiazione ma non riceve la benemerenza
    escluso = models.BooleanField(default=False)
    aggiunto_a_mano = models.BooleanField(default=False)
    motivo = models.CharField(max_length=200, blank=True, help_text="perché è stato escluso o aggiunto a mano")

    class Meta:
        verbose_name_plural = "premiati"
        ordering = ["premiazione", "benemerenza__ordine", "socio"]
        constraints = [
            # lo stesso socio può maturare più benemerenze nella stessa premiazione
            models.UniqueConstraint(fields=["premiazione", "socio", "benemerenza"], name="premiato_unico"),
        ]

    def __str__(self):
        return f"{self.socio} - {self.benemerenza}"
