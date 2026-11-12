from django.db import models

from gestionale.core.models import Sezione


class StatoSocio(models.TextChoices):
    """Stato del socio, aggiornato da una funzione dopo ogni import (README §3). Vuoto = non ancora socio."""

    DONATORE = "donatore", "Socio donatore"
    COLLABORATORE = "collaboratore", "Socio collaboratore"
    EX_SOCIO = "ex_socio", "Ex socio"


class Idoneita(models.TextChoices):
    """Idoneità come la esporta AssoWeb (colonna IDONEITA), che ne è la fonte master."""

    IDONEO = "idoneo", "Idoneo"
    TEMP_SOSPESO = "temp_sospeso", "Temporaneamente sospeso"
    DEF_SOSPESO = "def_sospeso", "Definitivamente sospeso"
    NON_IDONEO = "non_idoneo", "Non idoneo"


class CausaCessazione(models.IntegerChoices):
    """Codici di cessazione del tracciato SIAN."""

    ESPULSIONE = 10, "Espulsione"
    DIMISSIONI = 11, "Dimissioni"
    CESSAZIONE = 12, "Cessazione"
    TRASFERITO = 13, "Trasferito"
    CESSAZIONE_SEDE = 14, "Cessazione sede"


class Fonte(models.TextChoices):
    ASSOAVIS = "assoavis", "Storico AssoAVIS"
    ASSOWEB = "assoweb", "AssoWeb"
    MANUALE = "manuale", "Inserimento manuale"
    # benemerenze assegnate con una premiazione del gestionale
    GESTIONALE = "gestionale", "Gestionale"


class Socio(models.Model):
    sezione = models.ForeignKey(Sezione, on_delete=models.PROTECT, related_name="soci")

    # chiavi di aggancio tra le fonti
    codice_fiscale = models.CharField(max_length=16, unique=True, null=True, blank=True)
    codice_interno_assoavis = models.CharField(max_length=10, unique=True, null=True, blank=True,
                                               help_text="CODINTERNODONAT di AssoAVIS")
    codice_regionale = models.CharField(max_length=20, blank=True, db_index=True,
                                        help_text="CODICEREGIONALE di AssoWeb")
    tessera = models.CharField(max_length=20, blank=True)

    # anagrafica
    cognome = models.CharField(max_length=80)
    nome = models.CharField(max_length=80)
    sesso = models.CharField(max_length=1, choices=[("M", "Maschio"), ("F", "Femmina")], blank=True)
    data_nascita = models.DateField(null=True, blank=True)
    luogo_nascita = models.CharField(max_length=80, blank=True)
    provincia_nascita = models.CharField(max_length=5, blank=True)

    # residenza e contatti
    indirizzo_residenza = models.CharField(max_length=120, blank=True)
    comune_residenza = models.CharField(max_length=80, blank=True)
    cap_residenza = models.CharField(max_length=5, blank=True)
    provincia_residenza = models.CharField(max_length=5, blank=True)
    cellulare = models.CharField(max_length=30, blank=True)
    telefono = models.CharField(max_length=30, blank=True)
    email = models.CharField(max_length=120, blank=True)

    # dati trasfusionali (da AssoWeb, in chiaro)
    gruppo_sanguigno = models.CharField(max_length=2, choices=[(g, g) for g in ("0", "A", "B", "AB")], blank=True)
    rh = models.CharField(max_length=1, choices=[("+", "Positivo"), ("-", "Negativo")], blank=True)
    fenotipo_rh = models.CharField(max_length=10, blank=True)
    kell = models.CharField(max_length=2, blank=True)
    idoneita = models.CharField(max_length=20, choices=Idoneita.choices, blank=True)
    idoneita_dal = models.DateField(null=True, blank=True)
    idoneita_al = models.DateField(null=True, blank=True)
    # stato del donatore nel sistema regionale (Periodico, Inattivo, Aspirante, ...): non è lo stato associativo
    stato_assoweb = models.CharField(max_length=30, blank=True)

    # dati associativi: solo lo stato attuale, senza storico
    stato = models.CharField(max_length=20, choices=StatoSocio.choices, blank=True, db_index=True)
    # fa parte dei collaboratori della sezione, anche quando dona: lo stato resta donatore finché dona, poi collaboratore
    collaboratore = models.BooleanField(default=False, help_text="Resta socio anche senza donare: donatore finché "
                                                                 "dona, poi collaboratore.")
    data_iscrizione = models.DateField(null=True, blank=True)
    # l'import la usa per data_iscrizione solo se è utile e la rivaluta solo quando cambia (importazioni/assoweb.py)
    data_iscrizione_assoweb = models.DateField("iscrizione su AssoWeb", null=True, blank=True,
                                               help_text="DATA_ISCRIZIONE letta dall'ultimo import AssoWeb")
    data_cessazione = models.DateField(null=True, blank=True)
    causa_cessazione = models.IntegerField(choices=CausaCessazione.choices, null=True, blank=True)

    # donazioni fatte prima della gestione informatizzata (AssoAVIS: DONAZIONIPREGR, AFERESIPREGRES, ...);
    # possono essere negative: nello storico ci sono correzioni manuali del totale
    donazioni_pregresse_si = models.IntegerField(default=0)
    donazioni_pregresse_aferesi = models.IntegerField(default=0)
    donazioni_pregresse_dopo_2004 = models.IntegerField(default=0)

    no_benemerenze = models.BooleanField("non desidera benemerenze", default=False)
    note = models.TextField(blank=True)

    aggiornato_assoweb_il = models.DateTimeField(null=True, blank=True)
    creato_il = models.DateTimeField(auto_now_add=True)
    modificato_il = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "soci"
        ordering = ["cognome", "nome"]
        indexes = [models.Index(fields=["cognome", "nome"])]

    def __str__(self):
        return f"{self.cognome} {self.nome}"

    def save(self, *args, **kwargs):
        # le chiavi vuote diventano NULL, altrimenti il vincolo di unicità scatterebbe tra le stringhe vuote
        self.codice_fiscale = (self.codice_fiscale or "").strip().upper() or None
        self.codice_interno_assoavis = (self.codice_interno_assoavis or "").strip() or None
        super().save(*args, **kwargs)


class TipoDonazione(models.Model):
    codice = models.CharField(max_length=2, unique=True, help_text="sigla: SI, PL, ...")
    codice_assoavis = models.PositiveSmallIntegerField(unique=True, null=True, blank=True)
    descrizione = models.CharField(max_length=60)
    # le donazioni con emazie contano doppio per le donne ("Donne x2" delle benemerenze)
    contiene_emazie = models.BooleanField(default=False)
    # "Solo presentazione" non è una donazione
    e_donazione = models.BooleanField(default=True)

    class Meta:
        verbose_name = "tipo di donazione"
        verbose_name_plural = "tipi di donazione"
        ordering = ["codice_assoavis"]

    def __str__(self):
        return f"{self.codice} {self.descrizione}"


class Donazione(models.Model):
    socio = models.ForeignKey(Socio, on_delete=models.CASCADE, related_name="donazioni")
    data = models.DateField(db_index=True)
    tipo = models.ForeignKey(TipoDonazione, on_delete=models.PROTECT)
    centro_prelievo = models.CharField(max_length=100, blank=True)
    fonte = models.CharField(max_length=10, choices=Fonte.choices)

    class Meta:
        verbose_name_plural = "donazioni"
        ordering = ["-data"]
        constraints = [
            # come in AssoAVIS: una sola donazione al giorno per socio
            models.UniqueConstraint(fields=["socio", "data"], name="donazione_unica_per_giorno"),
        ]

    def __str__(self):
        return f"{self.data:%d/%m/%Y} {self.tipo.codice}"
