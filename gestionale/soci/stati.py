"""Stato del socio calcolato dalle donazioni con la regola dei 2 anni solari (README §3)."""

import datetime
from collections import Counter

from django.db.models import Max, Min, Q
from django.utils import timezone

from .models import CausaCessazione, Socio, StatoSocio


def nuovo_stato(stato, ultima_donazione, data_cessazione, oggi, collaboratore=False):
    """Campi da cambiare per un socio, oppure None se resta com'è.

    Con l'ultima donazione nell'anno N si resta donatori fino al 31/12/N+2, poi si cessa a quella data.
    Un ex socio torna donatore solo con una donazione successiva alla cessazione: così le cessazioni
    decise a mano (dimissioni, trasferimenti) non vengono annullate. Chi fa parte dei collaboratori
    (`Socio.collaboratore`) non cessa mai: è donatore finché dona, poi collaboratore.
    """
    attivo = ultima_donazione is not None and ultima_donazione.year >= oggi.year - 2
    if collaboratore:
        atteso = StatoSocio.DONATORE if attivo else StatoSocio.COLLABORATORE
        return None if stato == atteso else {"stato": atteso, "data_cessazione": None, "causa_cessazione": None}
    if stato == StatoSocio.COLLABORATORE:
        return None
    if attivo and stato != StatoSocio.DONATORE:
        if data_cessazione and ultima_donazione <= data_cessazione:
            return None
        return {"stato": StatoSocio.DONATORE, "data_cessazione": None, "causa_cessazione": None}
    if not attivo and stato == StatoSocio.DONATORE:
        # un donatore senza nessuna donazione (segnato a mano) cessa alla fine dell'anno scorso
        anno = ultima_donazione.year + 2 if ultima_donazione else oggi.year - 1
        return {"stato": StatoSocio.EX_SOCIO, "data_cessazione": datetime.date(anno, 12, 31),
                "causa_cessazione": CausaCessazione.CESSAZIONE}
    return None


def aggiorna_stati(oggi=None):
    """Applica la regola a tutti i soci; restituisce i passaggi di stato come {(stato prima, stato dopo): n}."""
    oggi = oggi or timezone.localdate()
    # "Solo presentazione" non è una donazione
    valide = Q(donazioni__tipo__e_donazione=True)
    soci = Socio.objects.annotate(
        ultima=Max("donazioni__data", filter=valide), prima=Min("donazioni__data", filter=valide))
    adesso = timezone.now()
    cambiati, passaggi = [], Counter()
    for socio in soci:
        campi = nuovo_stato(socio.stato, socio.ultima, socio.data_cessazione, oggi, socio.collaboratore)
        if campi is None:
            continue
        passaggi[(socio.stato, campi["stato"])] += 1
        for nome, valore in campi.items():
            setattr(socio, nome, valore)
        # si diventa soci con la prima donazione
        if socio.stato == StatoSocio.DONATORE and socio.data_iscrizione is None:
            socio.data_iscrizione = socio.prima
        socio.modificato_il = adesso
        cambiati.append(socio)
    Socio.objects.bulk_update(cambiati, ["stato", "data_cessazione", "causa_cessazione", "data_iscrizione",
                                         "modificato_il"], batch_size=1000)
    return passaggi


def descrivi_passaggi(passaggi):
    """Passaggi di stato in righe leggibili, es. "Ex socio -> Socio donatore: 3"."""
    nomi = dict(StatoSocio.choices) | {"": "Non socio"}
    return [f"{nomi[da]} -> {nomi[a]}: {n}" for (da, a), n in sorted(passaggi.items())]
