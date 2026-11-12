from django.db import models

from gestionale.core.models import Esecuzione
from gestionale.soci.models import CausaCessazione, Socio


class ModalitaSian(models.TextChoices):
    # InvioListaTesserati con Tipo 1: sostituisce l'intero registro della sede sul SIAN
    TOTALE = "totale", "Registro completo"
    # InvioSingoloTesserato: aggiorna un socio, per provare il collegamento
    SINGOLO = "singolo", "Un socio"


class InvioSian(models.Model):
    """Un invio del libro soci di un anno al SIAN."""

    anno = models.PositiveSmallIntegerField()
    # codice della sede sul SIAN (Sezione.codice), che accompagna ogni richiesta
    sede = models.CharField(max_length=10)
    modalita =models.CharField(max_length=10, choices=ModalitaSian.choices, default=ModalitaSian.TOTALE)
    esecuzione = models.OneToOneField(Esecuzione, null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="invio_sian")
    # IDUnivocoInvio restituito dal SIAN: serve per leggere l'esito con GetLogImportazione
    id_invio = models.CharField(max_length=100, blank=True)
    # DataAnalisi del SIAN: vuota finché il SIAN non ha elaborato l'invio
    esito_il = models.DateTimeField("esito del SIAN del", null=True, blank=True)
    creato_il = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "invio SIAN"
        verbose_name_plural = "invii SIAN"
        ordering = ["-creato_il"]

    def __str__(self):
        return f"Libro soci {self.anno} del {self.creato_il:%d/%m/%Y}"


class OperazioneSian(models.TextChoices):
    INSERIMENTO = "inserimento", "Inserimento"
    CESSAZIONE = "cessazione", "Cessazione"
    CANCELLAZIONE = "cancellazione", "Cancellazione"


class EsitoSian(models.TextChoices):
    OK = "ok", "Riuscito"
    ERRORE = "errore", "Errore"
    # inviato, ma il SIAN non ha ancora elaborato il registro
    INVIATO = "inviato", "In attesa dell'esito"
    # non superava i controlli prima dell'invio: non è stato inviato
    SCARTATO = "scartato", "Non inviato"


class InvioSianRiga(models.Model):
    """Esito di un'operazione su un socio durante un invio."""

    invio = models.ForeignKey(InvioSian, on_delete=models.CASCADE, related_name="righe")
    # le cancellazioni leggono i record dal SIAN: possono non avere un socio nel DB
    socio = models.ForeignKey(Socio, null=True, blank=True, on_delete=models.PROTECT, related_name="invii_sian")
    codice_fiscale = models.CharField(max_length=16)
    operazione = models.CharField(max_length=15, choices=OperazioneSian.choices)
    tipo_socio = models.PositiveSmallIntegerField(null=True, blank=True, help_text="1 donatore, 2 collaboratore")
    data_cessazione = models.DateField(null=True, blank=True)
    causa_cessazione = models.IntegerField(choices=CausaCessazione.choices, null=True, blank=True)
    esito = models.CharField(max_length=10, choices=EsitoSian.choices)
    # motivo dello scarto o segnalazioni del SIAN, che possono citare i dati del socio: non va nel log del job
    messaggio = models.TextField(blank=True)

    class Meta:
        verbose_name = "riga di invio SIAN"
        verbose_name_plural = "righe di invio SIAN"
        ordering = ["invio", "id"]

    def __str__(self):
        return f"{self.get_operazione_display()} {self.codice_fiscale}: {self.get_esito_display()}"
