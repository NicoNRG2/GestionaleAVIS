"""Calcolo delle benemerenze sui dati del DB: legge i soci per il motore di `regole.py` e ne salva i risultati.

Flusso di una premiazione (README M4): bozza → `calcola` → revisione (`escludi`, `aggiungi`) → `assegna`.
"""

from dataclasses import dataclass

from django.db import transaction

from gestionale.soci.models import Donazione, Fonte, Socio

from . import regole
from .models import Benemerenza, BenemerenzaAssegnata, Premiato, Premiazione, StatoPremiazione


class PremiazioneError(Exception):
    pass


def dati_soci(soci=None, possedute_prima_del=None):
    """{id del socio: DatiSocio} per i soci del queryset (tutti se None).

    `possedute_prima_del` conta solo le benemerenze ricevute prima di quella data, per ricalcolare le premiazioni
    storiche; quelle senza data (nello storico ce ne sono) valgono come già possedute.
    """
    soci = Socio.objects.all() if soci is None else soci
    dati = {
        s["pk"]: regole.DatiSocio(
            sesso=s["sesso"], data_nascita=s["data_nascita"], data_iscrizione=s["data_iscrizione"],
            pregresse_si=s["donazioni_pregresse_si"], pregresse_aferesi=s["donazioni_pregresse_aferesi"],
            pregresse_dopo_2004=s["donazioni_pregresse_dopo_2004"])
        for s in soci.values("pk", "sesso", "data_nascita", "data_iscrizione", "donazioni_pregresse_si",
                             "donazioni_pregresse_aferesi", "donazioni_pregresse_dopo_2004")
    }
    # "Solo presentazione" non è una donazione
    donazioni = (Donazione.objects.filter(socio__in=soci, tipo__e_donazione=True).order_by("socio_id", "data")
                 .values_list("socio_id", "data", "tipo__contiene_emazie"))
    for socio_id, data, emazie in donazioni:
        dati[socio_id].donazioni.append((data, emazie))
    for socio_id, codice, data in (BenemerenzaAssegnata.objects.filter(socio__in=soci)
                                   .values_list("socio_id", "benemerenza__codice", "data")):
        if possedute_prima_del is None or data is None or data < possedute_prima_del:
            dati[socio_id].possedute.add(codice)
    return dati


# --- premiazioni ---

def aperta():
    """La premiazione non ancora assegnata, se c'è: se ne prepara una alla volta, come in AssoAVIS."""
    return Premiazione.objects.exclude(stato=StatoPremiazione.ASSEGNATA).first()


def ultima_consegna():
    """Data di consegna dell'ultima premiazione assegnata: la nuova premia gli ex soci cessati dopo."""
    ultima = Premiazione.objects.filter(stato=StatoPremiazione.ASSEGNATA).order_by("-data_consegna").first()
    return ultima.data_consegna if ultima else None


def aventi_diritto(premiazione):
    """[(id del socio, Conteggio, [codici delle benemerenze maturate])] per i soci premiabili."""
    soci = Socio.objects.all()
    dati = dati_soci(soci)
    risultato = []
    for socio in soci.values("pk", "stato", "no_benemerenze", "data_cessazione"):
        if not regole.premiabile(socio["stato"], socio["no_benemerenze"], socio["data_cessazione"],
                                 premiazione.ex_soci_cessati_dal):
            continue
        conteggio, nuove = regole.maturate(dati[socio["pk"]], premiazione.data_riferimento)
        if nuove:
            risultato.append((socio["pk"], conteggio, nuove))
    return risultato


def _modificabile(premiazione):
    if premiazione.stato == StatoPremiazione.ASSEGNATA:
        raise PremiazioneError("La premiazione è già assegnata: non si può più modificare.")


@transaction.atomic
def calcola(premiazione):
    """Ricalcola gli aventi diritto e restituisce quanti sono i premiati.

    Le modifiche della revisione restano: i soci aggiunti a mano non si toccano e chi era escluso resta escluso,
    con il suo motivo.
    """
    _modificabile(premiazione)
    esclusi = dict(premiazione.premiati.filter(escluso=True).values_list("socio_id", "motivo"))
    premiazione.premiati.filter(aggiunto_a_mano=False).delete()
    manuali = set(premiazione.premiati.values_list("socio_id", "benemerenza__codice"))
    catalogo = {b.codice: b for b in Benemerenza.objects.all()}
    nuovi = [
        Premiato(premiazione=premiazione, socio_id=socio_id, benemerenza=catalogo[codice],
                 donazioni_valide=max(conteggio.valide, 0), donazioni_effettive=max(conteggio.effettive, 0),
                 escluso=socio_id in esclusi, motivo=esclusi.get(socio_id, ""))
        for socio_id, conteggio, codici in aventi_diritto(premiazione)
        for codice in codici if (socio_id, codice) not in manuali
    ]
    Premiato.objects.bulk_create(nuovi)
    premiazione.stato = StatoPremiazione.CALCOLATA
    premiazione.save(update_fields=["stato"])
    return premiazione.premiati.filter(escluso=False).values("socio").distinct().count()


@transaction.atomic
def escludi(premiazione, socio_id, motivo):
    """Esclude il socio da tutte le benemerenze della premiazione; con il motivo vuoto lo riammette."""
    _modificabile(premiazione)
    return premiazione.premiati.filter(socio_id=socio_id).update(escluso=bool(motivo), motivo=motivo)


@transaction.atomic
def aggiungi(premiazione, socio, benemerenza, motivo):
    """Aggiunge a mano un socio con una benemerenza, senza controllare i requisiti (come AssoAVIS)."""
    _modificabile(premiazione)
    if premiazione.premiati.filter(socio=socio, escluso=True).exists():
        raise PremiazioneError(f"Il socio {socio.pk} è escluso dalla premiazione: prima va riammesso.")
    if premiazione.premiati.filter(socio=socio, benemerenza=benemerenza).exists():
        raise PremiazioneError(f"Il socio {socio.pk} ha già questa benemerenza nella premiazione.")
    if socio.benemerenze.filter(benemerenza=benemerenza).exists():
        raise PremiazioneError(f"Il socio {socio.pk} possiede già questa benemerenza.")
    conteggio = regole.conta(dati_soci(Socio.objects.filter(pk=socio.pk))[socio.pk], premiazione.data_riferimento,
                             regole.statuto(premiazione.data_riferimento))
    return Premiato.objects.create(premiazione=premiazione, socio=socio, benemerenza=benemerenza,
                                   donazioni_valide=max(conteggio.valide, 0),
                                   donazioni_effettive=max(conteggio.effettive, 0), aggiunto_a_mano=True,
                                   motivo=motivo)


@transaction.atomic
def assegna(premiazione):
    """Registra le benemerenze dei premiati non esclusi alla data di consegna. È irreversibile.

    Restituisce il numero di benemerenze registrate.
    """
    premiazione = Premiazione.objects.select_for_update().get(pk=premiazione.pk)
    if premiazione.stato != StatoPremiazione.CALCOLATA:
        raise PremiazioneError("Si assegna solo una premiazione calcolata.")
    premiati = premiazione.premiati.filter(escluso=False)
    possedute = set(BenemerenzaAssegnata.objects.filter(socio__in=premiati.values("socio"))
                    .values_list("socio_id", "benemerenza_id"))
    nuove = [BenemerenzaAssegnata(socio_id=p.socio_id, benemerenza_id=p.benemerenza_id,
                                  data=premiazione.data_consegna, fonte=Fonte.GESTIONALE)
             for p in premiati if (p.socio_id, p.benemerenza_id) not in possedute]
    BenemerenzaAssegnata.objects.bulk_create(nuove)
    premiazione.stato = StatoPremiazione.ASSEGNATA
    premiazione.save(update_fields=["stato"])
    return len(nuove)


@dataclass
class PremiatoSocio:
    """I premiati di una premiazione raggruppati per socio: alla cerimonia si consegna la benemerenza più alta."""

    principale: Premiato
    insieme_a: list  # le altre benemerenze maturate nella stessa premiazione, dalla più alta

    @property
    def socio(self):
        return self.principale.socio


def premiati_per_socio(premiazione):
    """(premiati, esclusi) come liste di PremiatoSocio, in ordine di benemerenza e di cognome."""
    gruppi = {}
    for premiato in (premiazione.premiati.select_related("socio", "benemerenza")
                     .order_by("socio_id", "-benemerenza__ordine")):
        if premiato.socio_id in gruppi:
            gruppi[premiato.socio_id].insieme_a.append(premiato.benemerenza)
        else:
            gruppi[premiato.socio_id] = PremiatoSocio(premiato, [])
    ordinati = sorted(gruppi.values(), key=lambda g: (g.principale.benemerenza.ordine, g.socio.cognome,
                                                      g.socio.nome, g.socio.pk))
    return [g for g in ordinati if not g.principale.escluso], [g for g in ordinati if g.principale.escluso]


# --- scheda del socio ---

def situazione(socio, oggi):
    """Donazioni valide ed effettive, benemerenze maturate e non ancora consegnate e prossima benemerenza, a oggi."""
    dati = dati_soci(Socio.objects.filter(pk=socio.pk))[socio.pk]
    statuto = regole.statuto(oggi)
    conteggio, maturate = regole.maturate(dati, oggi, statuto)
    catalogo = {b.codice: b for b in Benemerenza.objects.all()}
    prossima = regole.prossima(dati, oggi, statuto)
    return {
        "oggi": oggi,
        "conteggio": conteggio,
        "maturate": [catalogo[codice] for codice in maturate],
        "prossima": prossima,
        "prossima_benemerenza": catalogo[prossima.regola.benemerenza] if prossima else None,
        "premiabile": regole.premiabile(socio.stato, socio.no_benemerenze, socio.data_cessazione, ultima_consegna()),
    }
