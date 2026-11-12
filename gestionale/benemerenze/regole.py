"""Regole delle benemerenze e motore di calcolo (README M4, ANALISI §6.3-6.4).

Funzioni pure, senza accesso al DB: ricevono i dati del socio già letti (`DatiSocio`). Le regole sono nel codice e
non in tabella perché le fissa lo statuto. Il calcolo ricalcola identica la premiazione del 03/11/2025 di AssoAVIS
ed è verificato sulle premiazioni storiche con `manage.py verifica_premiazioni`.

Si premiano solo i donatori, quindi le benemerenze dei collaboratori non si calcolano; l'Oro con Diamante segue le
regole delle altre benemerenze, anche per i donatori attivi.
"""

import datetime
from dataclasses import dataclass, field

from gestionale.soci.models import StatoSocio

# dal 17/05/2004 valgono il tetto annuo e "Donne x2"; prima nessun limite
INIZIO_STATUTO_2004 = datetime.date(2004, 5, 17)
# dal 2021 il tetto annuo sale a 6 e arrivano i criteri "donazioni + anni"
PRIMO_ANNO_STATUTO_2021 = 2021
# per "Donne x2" le donazioni con emazie valgono doppio solo prima dei 50 anni
ETA_FINE_X2 = 50

ORO_CON_DIAMANTE = 10


@dataclass(frozen=True)
class Regola:
    """Criteri di una benemerenza: basta uno dei due."""

    benemerenza: int  # codice del catalogo (Benemerenza.codice)
    solo_donazioni: int  # criterio B: almeno N donazioni valide
    donazioni_con_anni: int = 0  # criterio A: almeno N donazioni valide ...
    anni: int = 0  # ... e almeno N anni di iscrizione

    def maturata(self, valide, anni_iscrizione):
        criterio_a = self.donazioni_con_anni > 0 and valide >= self.donazioni_con_anni and anni_iscrizione >= self.anni
        return criterio_a or valide >= self.solo_donazioni


@dataclass(frozen=True)
class Statuto:
    nome: str
    regole: tuple  # in ordine crescente di importanza
    donne_x2: bool
    tetto_annuo: bool


# premiazioni fino al 2019 (ANALISI §6.3): solo il numero di donazioni. Senza tetto annuo, come le aveva calcolate
# AssoAVIS ("Considera massimo donazioni anno" spento): così le donazioni valide coincidono con lo storico
STATUTO_2004 = Statuto("Statuto 2004", (
    Regola(1, 8), Regola(2, 16), Regola(3, 24), Regola(4, 50), Regola(5, 75), Regola(6, 100),
), donne_x2=False, tetto_annuo=False)

# premiazioni dal 2022 (ANALISI §6.3, configurazione delle premiazioni 107-118)
STATUTO_2021 = Statuto("Statuto 2021", (
    Regola(1, 8, 6, 3),
    Regola(2, 16, 12, 5),
    Regola(3, 36, 24, 10),
    Regola(4, 50, 40, 20),
    Regola(5, 75, 60, 30),
    Regola(6, 100, 80, 40),
    Regola(ORO_CON_DIAMANTE, 120),
), donne_x2=True, tetto_annuo=True)

# livello delle benemerenze dei donatori: chi ne ha una superiore ha implicitamente anche le inferiori
LIVELLO = {regola.benemerenza: livello for livello, regola in enumerate(STATUTO_2021.regole, start=1)}


def statuto(data_riferimento):
    return STATUTO_2021 if data_riferimento.year >= PRIMO_ANNO_STATUTO_2021 else STATUTO_2004


def limite_annuo(anno):
    """Massimo di donazioni valide in un anno solare, dal 17/05/2004."""
    return 6 if anno >= PRIMO_ANNO_STATUTO_2021 else 4


@dataclass
class DatiSocio:
    sesso: str = ""
    data_nascita: datetime.date | None = None
    data_iscrizione: datetime.date | None = None
    pregresse_si: int = 0
    pregresse_aferesi: int = 0
    # di cui dopo il 17/05/2004: per le donne valgono doppio
    pregresse_dopo_2004: int = 0
    # [(data, contiene emazie)] in ordine di data, senza le "Solo presentazione"
    donazioni: list = field(default_factory=list)
    # codici delle benemerenze già possedute
    possedute: set = field(default_factory=set)


@dataclass(frozen=True)
class Conteggio:
    valide: int
    effettive: int
    anni_iscrizione: int
    ultima_donazione: datetime.date | None


def anni_compiuti(dal, al):
    if dal is None:
        return 0
    return al.year - dal.year - ((al.month, al.day) < (dal.month, dal.day))


def _compleanno(data_nascita, anni):
    try:
        return data_nascita.replace(year=data_nascita.year + anni)
    except ValueError:  # nato il 29 febbraio
        return datetime.date(data_nascita.year + anni, 3, 1)


def conta(dati, data_riferimento, regole=STATUTO_2021):
    """Donazioni effettive e valide alla data di riferimento.

    - Effettive: pregresse più le donazioni registrate fino alla data di riferimento.
    - Valide: con "Donne x2" le donazioni con emazie delle donne dopo il 17/05/2004 e prima dei 50 anni valgono
      doppio; tra le pregresse vale doppio solo la quota dopo il 2004. Con il tetto annuo, dal 17/05/2004 un anno
      vale al massimo 4 (6 dal 2021): il tetto vale per tutti i tipi di donazione e si applica al valore già
      raddoppiato, come in AssoAVIS (verificato sulle premiazioni dal 2022). Nessun tetto prima del 17/05/2004 né
      sulle pregresse.
    """
    donna = regole.donne_x2 and dati.sesso == "F"
    fine_x2 = _compleanno(dati.data_nascita, ETA_FINE_X2) if dati.data_nascita else None
    pregresse = dati.pregresse_si + dati.pregresse_aferesi
    valide = effettive = pregresse
    if donna:
        valide += dati.pregresse_dopo_2004
    per_anno, ultima = {}, None
    for giorno, emazie in dati.donazioni:
        if giorno > data_riferimento:
            break
        effettive += 1
        ultima = giorno
        if giorno <= INIZIO_STATUTO_2004:
            valide += 1
            continue
        doppia = donna and emazie and (fine_x2 is None or giorno < fine_x2)
        per_anno[giorno.year] = per_anno.get(giorno.year, 0) + (2 if doppia else 1)
    valide += sum(min(n, limite_annuo(anno)) if regole.tetto_annuo else n for anno, n in per_anno.items())
    return Conteggio(valide, effettive, anni_compiuti(dati.data_iscrizione, data_riferimento), ultima)


def possiede(possedute, benemerenza):
    """Già posseduta, anche implicitamente: chi ha l'Oro ha anche Rame, Argento e Argento dorato."""
    return benemerenza in possedute or any(LIVELLO.get(p, 0) > LIVELLO[benemerenza] for p in possedute)


def _maturata(regola, conteggio):
    if not regola.maturata(conteggio.valide, conteggio.anni_iscrizione):
        return False
    if regola.benemerenza == ORO_CON_DIAMANTE:
        # come AssoAVIS: solo con l'ultima donazione dopo il 17/05/2004
        return conteggio.ultima_donazione is not None and conteggio.ultima_donazione > INIZIO_STATUTO_2004
    return True


def maturate(dati, data_riferimento, regole=None):
    """Conteggio e codici delle benemerenze maturate alla data e non ancora possedute."""
    regole = regole or statuto(data_riferimento)
    conteggio = conta(dati, data_riferimento, regole)
    nuove = [r.benemerenza for r in regole.regole
             if not possiede(dati.possedute, r.benemerenza) and _maturata(r, conteggio)]
    return conteggio, nuove


@dataclass(frozen=True)
class Prossima:
    """La prima benemerenza non ancora maturata e quanto manca."""

    regola: Regola
    mancano: int  # donazioni valide che mancano per il criterio "solo donazioni"
    mancano_con_anni: int  # ... per il criterio "donazioni + anni" (0 se le donazioni bastano già)
    anni_dal: datetime.date | None  # da quando ci sono gli anni di iscrizione del criterio "donazioni + anni"


def prossima(dati, data_riferimento, regole=None):
    regole = regole or statuto(data_riferimento)
    conteggio, nuove = maturate(dati, data_riferimento, regole)
    for regola in regole.regole:
        if possiede(dati.possedute, regola.benemerenza) or regola.benemerenza in nuove:
            continue
        anni_dal = None
        if regola.donazioni_con_anni and dati.data_iscrizione:
            anni_dal = _compleanno(dati.data_iscrizione, regola.anni)
        return Prossima(regola, max(regola.solo_donazioni - conteggio.valide, 0),
                        max(regola.donazioni_con_anni - conteggio.valide, 0), anni_dal)
    return None


def premiabile(stato, no_benemerenze, data_cessazione, ex_soci_cessati_dal):
    """Si premiano i soci donatori e gli ex soci cessati dopo la data indicata nella premiazione.

    I collaboratori non ricevono benemerenze.
    """
    if no_benemerenze:
        return False
    if stato == StatoSocio.DONATORE:
        return True
    return (stato == StatoSocio.EX_SOCIO and ex_soci_cessati_dal is not None and data_cessazione is not None
            and data_cessazione > ex_soci_cessati_dal)
