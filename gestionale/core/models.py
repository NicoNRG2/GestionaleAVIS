import datetime

from django.conf import settings
from django.db import models
from django.utils import timezone

ATTESA_MASSIMA_IN_CODA = datetime.timedelta(seconds=30)


class Sezione(models.Model):
    """Sede AVIS gestita. Ogni socio appartiene a una sezione (estensione multi-sezione)."""

    codice = models.CharField(max_length=10, unique=True)
    nome = models.CharField(max_length=100)
    # nome esatto con cui la sezione compare nei filtri di AssoWeb
    nome_assoweb = models.CharField(max_length=100, blank=True)

    class Meta:
        verbose_name_plural = "sezioni"
        ordering = ["codice"]

    def __str__(self):
        return f"{self.codice} {self.nome}"


class Evento(models.Model):
    """Registro di accessi e operazioni sensibili (GDPR). Non contiene dati sanitari."""

    data_ora = models.DateTimeField(auto_now_add=True, db_index=True)
    utente = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    # per i login falliti l'utente non esiste: si salva il nome digitato
    nome_utente = models.CharField(max_length=150, blank=True)
    azione = models.CharField(max_length=50, db_index=True)
    oggetto = models.CharField(max_length=200, blank=True)
    dettagli = models.JSONField(default=dict, blank=True)
    indirizzo_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "eventi"
        ordering = ["-data_ora"]

    def __str__(self):
        return f"{self.data_ora:%d/%m/%Y %H:%M} {self.nome_utente} {self.azione}"


class TipoEsecuzione(models.TextChoices):
    AGGIORNA_DATI = "aggiorna_dati", "Aggiorna dati"
    POSTI_LIBERI = "posti_liberi", "Posti liberi"
    INVIO_SIAN = "invio_sian", "Invio libro soci SIAN"


class StatoEsecuzione(models.TextChoices):
    IN_CODA = "in_coda", "In coda"
    IN_CORSO = "in_corso", "In corso"
    COMPLETATA = "completata", "Completata"
    FALLITA = "fallita", "Fallita"


# esecuzioni non ancora terminate
STATI_ATTIVI = [StatoEsecuzione.IN_CODA, StatoEsecuzione.IN_CORSO]


class Esecuzione(models.Model):
    """Job in background eseguito dal worker (core/worker.py); resta come storico.

    I posti liberi vivono solo qui.
    """

    tipo = models.CharField(max_length=20, choices=TipoEsecuzione.choices)
    # es. anno degli import, giornata dei posti liberi, anno e modalità dell'invio SIAN
    parametri = models.JSONField(default=dict, blank=True)
    stato = models.CharField(max_length=10, choices=StatoEsecuzione.choices, default=StatoEsecuzione.IN_CODA,
                             db_index=True)
    log = models.TextField(blank=True)
    utente = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    creata_il = models.DateTimeField(auto_now_add=True)
    iniziata_il = models.DateTimeField(null=True, blank=True)
    terminata_il = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "esecuzioni"
        ordering = ["-creata_il"]
        # chi può lanciare i job; i ruoli che li hanno sono in core/ruoli.py
        permissions = [("avvia_aggiorna_dati", "Può lanciare Aggiorna dati"),
                       ("avvia_invio_sian", "Può inviare il libro soci al SIAN"),
                       ("avvia_posti_liberi", "Può inviare l'SMS dei posti liberi")]

    def __str__(self):
        return f"{self.get_tipo_display()} del {timezone.localtime(self.creata_il):%d/%m/%Y %H:%M}"

    @property
    def attiva(self):
        return self.stato in STATI_ATTIVI

    @property
    def worker_fermo(self):
        """In coda da più di 30 secondi: il worker di solito la prende in carico in pochi secondi."""
        return self.stato == StatoEsecuzione.IN_CODA and timezone.now() - self.creata_il > ATTESA_MASSIMA_IN_CODA

    @property
    def durata(self):
        """Durata come "m:ss", vuota finché l'esecuzione non è terminata."""
        if not (self.iniziata_il and self.terminata_il):
            return ""
        secondi = int((self.terminata_il - self.iniziata_il).total_seconds())
        return f"{secondi // 60}:{secondi % 60:02}"
