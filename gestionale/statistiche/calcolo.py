"""Statistiche della sezione calcolate sul DB, per anno (README M3).

Ogni statistica è una `Tabella`: la stessa struttura si mostra nella pagina e diventa un foglio dell'export Excel.
Contengono solo conteggi, nessun dato personale.
"""

import datetime
from collections import Counter
from dataclasses import dataclass

from django.db.models import Count, Min, Q
from django.db.models.functions import ExtractMonth, ExtractYear

from gestionale.benemerenze.regole import anni_compiuti
from gestionale.soci.models import Donazione, Socio, StatoSocio

# fasce di età (minima, massima, etichetta)
FASCE_ETA = [(18, 25, "18/25"), (26, 35, "26/35"), (36, 45, "36/45"), (46, 55, "46/55"), (56, 65, "56/65"),
             (66, 200, "Oltre 65")]
ETA_NON_INDICATA = "Età non indicata"
SESSI = [("M", "Maschi"), ("F", "Femmine")]
GRUPPI = ["0", "A", "B", "AB"]
MESI = ["Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre", "Ottobre",
        "Novembre", "Dicembre"]
SANGUE_INTERO = "SI"
ANNI_ANDAMENTO = 10


@dataclass
class Tabella:
    titolo: str
    foglio: str  # nome del foglio Excel: al massimo 31 caratteri, senza / \ ? * [ ] :
    colonne: list
    righe: list
    totale: list | None = None
    nota: str = ""
    barra: int | None = None  # indice della colonna mostrata anche come barra orizzontale nella pagina

    @property
    def righe_con_barre(self):
        """Coppie (riga, larghezza della barra in %); la larghezza è None se la tabella non ha barre."""
        if self.barra is None:
            return [(riga, None) for riga in self.righe]
        massimo = max((riga[self.barra] for riga in self.righe), default=0) or 1
        return [(riga, round(riga[self.barra] * 100 / massimo)) for riga in self.righe]


def _totale(righe, etichetta="Totale"):
    """Riga dei totali: somma colonna per colonna, esclusa la prima."""
    return [etichetta, *(sum(colonna) for colonna in list(zip(*righe))[1:])] if righe else None


def _percentuale(n, totale):
    return round(n * 100 / totale, 1) if totale else 0.0


def data_riferimento(anno, oggi):
    """31/12 per gli anni conclusi, oggi per l'anno in corso."""
    return oggi if anno >= oggi.year else datetime.date(anno, 12, 31)


def anni_disponibili(oggi):
    """Dal primo anno con donazioni all'anno in corso, dal più recente."""
    prima = Donazione.objects.aggregate(m=Min("data"))["m"]
    return list(range(oggi.year, (prima.year if prima else oggi.year) - 1, -1))


def donazioni(anno):
    # "Solo presentazione" non è una donazione
    return Donazione.objects.filter(data__year=anno, tipo__e_donazione=True)


def soci_donatori(anno, oggi):
    """Soci donatori al 31/12 dell'anno, oppure a oggi per l'anno in corso (regola dei 2 anni, README §3).

    Per l'anno in corso è lo stato attuale. Lo stato non ha storico, quindi per un anno concluso si ricostruisce
    dalle donazioni: al 31/12 restano soci quelli che hanno donato nell'anno o nel precedente, mentre chi ha donato
    l'ultima volta due anni prima cessa proprio quel giorno. È il conteggio del libro soci SIAN (517 nel 2025).
    Si escludono le cessazioni avvenute entro il 31/12, per esempio le dimissioni segnate a mano.
    """
    if anno >= oggi.year:
        return Socio.objects.filter(stato=StatoSocio.DONATORE)
    fine = datetime.date(anno, 12, 31)
    hanno_donato = Donazione.objects.filter(tipo__e_donazione=True, data__range=(datetime.date(anno - 1, 1, 1), fine))
    return Socio.objects.filter(pk__in=hanno_donato.values("socio")).exclude(data_cessazione__lte=fine)


def fascia_eta(anni):
    for minimo, massimo, nome in FASCE_ETA:
        if minimo <= anni <= massimo:
            return nome
    return ETA_NON_INDICATA


def per_fascia_e_sesso(soci, riferimento):
    """Soci per fascia d'età e sesso, con l'età alla data di riferimento."""
    conteggi = Counter()
    for sesso, nascita in soci.values_list("sesso", "data_nascita"):
        conteggi[fascia_eta(anni_compiuti(nascita, riferimento)) if nascita else ETA_NON_INDICATA, sesso] += 1
    righe = []
    for fascia in [nome for _, _, nome in FASCE_ETA] + [ETA_NON_INDICATA]:
        totale = sum(n for (f, _), n in conteggi.items() if f == fascia)
        if fascia != ETA_NON_INDICATA or totale:
            righe.append([fascia, *(conteggi[fascia, sesso] for sesso, _ in SESSI), totale])
    return Tabella("Soci donatori per fascia d'età e sesso", "Fascia d'età e sesso",
                   ["Fascia d'età", *(nome for _, nome in SESSI), "Totale"], righe, _totale(righe),
                   nota="Età alla data di riferimento.")


def per_gruppo_sanguigno(soci):
    conteggi = Counter(soci.values_list("gruppo_sanguigno", "rh"))
    n = sum(conteggi.values())
    righe = []
    for gruppo in GRUPPI + [""]:
        totale = sum(v for (g, _), v in conteggi.items() if g == gruppo)
        if gruppo or totale:
            righe.append([gruppo or "Non indicato", conteggi[gruppo, "+"], conteggi[gruppo, "-"], totale,
                          _percentuale(totale, n)])
    totale = [*_totale([riga[:-1] for riga in righe]), 100.0 if n else 0.0]
    return Tabella("Soci donatori per gruppo sanguigno", "Gruppi sanguigni", ["Gruppo", "Rh+", "Rh−", "Totale", "%"],
                   righe, totale, nota="Gruppo e Rh attuali, da AssoWeb.")


def _sangue_intero_e_totale(queryset, campo):
    """{valore del campo: (sangue intero, totale)}; `campo` è un'annotazione già presente (es. mese o anno)."""
    righe = queryset.values(campo).annotate(si=Count("id", filter=Q(tipo__codice=SANGUE_INTERO)), n=Count("id"))
    return {riga[campo]: (riga["si"], riga["n"]) for riga in righe}


def donazioni_per_tipo(anno):
    """Donazioni dell'anno per tipo, con tutti i tipi di donazione."""
    conteggi = list(donazioni(anno).values_list("tipo__descrizione").annotate(n=Count("id"))
                    .order_by("tipo__codice_assoavis"))
    n = sum(c for _, c in conteggi)
    righe = [[descrizione, c, _percentuale(c, n)] for descrizione, c in conteggi]
    return Tabella("Donazioni per tipo", "Donazioni per tipo", ["Tipo", "Donazioni", "%"], righe,
                   ["Totale", n, 100.0] if righe else None)


def donazioni_per_mese(anno, oggi):
    per_mese = _sangue_intero_e_totale(donazioni(anno).annotate(mese=ExtractMonth("data")), "mese")
    # nell'anno in corso i mesi che devono ancora venire non si mostrano
    ultimo = data_riferimento(anno, oggi).month
    righe = []
    for mese in range(1, ultimo + 1):
        si, n = per_mese.get(mese, (0, 0))
        righe.append([MESI[mese - 1], si, n - si, n])
    return Tabella("Donazioni per mese", "Donazioni per mese", ["Mese", "Sangue intero", "Aferesi", "Totale"], righe,
                   _totale(righe), barra=3)


@dataclass
class Movimento:
    """Soci donatori (ID) a fine anno precedente e a fine anno, con chi è entrato e chi è cessato in mezzo."""

    inizio: set
    fine: set
    nuovi: set
    riammessi: set
    cessati: set


def movimenti(anni, oggi):
    """{anno: Movimento}, ricostruiti con soci_donatori.

    Entrati: soci a fine anno che non lo erano a fine anno precedente; sono nuovi se la prima donazione è dell'anno,
    altrimenti riammessi. Cessati: soci a fine anno precedente che non lo sono più. Le date di cessazione salvate sui
    soci non servono: lo storico non le ha per molti ex soci e si azzerano quando un socio viene riammesso.
    """
    soci = {a: set(soci_donatori(a, oggi).values_list("pk", flat=True)) for a in range(min(anni) - 1, max(anni) + 1)}
    prime = dict(Socio.objects.annotate(prima=Min("donazioni__data", filter=Q(donazioni__tipo__e_donazione=True)))
                 .values_list("pk", "prima"))
    risultato = {}
    for a in anni:
        entrati = soci[a] - soci[a - 1]
        nuovi = {pk for pk in entrati if prime[pk] is None or prime[pk].year >= a}
        risultato[a] = Movimento(soci[a - 1], soci[a], nuovi, entrati - nuovi, soci[a - 1] - soci[a])
    return risultato


def movimento_soci(movimento, anno, oggi):
    """Bilancio dell'anno: soci a inizio anno + nuovi + riammessi - cessati = soci a fine anno."""
    sessi = dict(Socio.objects.filter(pk__in=movimento.inizio | movimento.fine).values_list("pk", "sesso"))

    def riga(etichetta, soci):
        per_sesso = Counter(sessi[pk] for pk in soci)
        return [etichetta, *(per_sesso[sesso] for sesso, _ in SESSI), len(soci)]

    righe = [riga(f"Soci al 31/12/{anno - 1}", movimento.inizio), riga("+ nuovi soci", movimento.nuovi),
             riga("+ riammessi", movimento.riammessi), riga("− cessati", movimento.cessati)]
    return Tabella("Movimento dei soci donatori", "Movimento soci", ["", *(nome for _, nome in SESSI), "Totale"],
                   righe, riga(f"Soci al {data_riferimento(anno, oggi):%d/%m/%Y}", movimento.fine),
                   nota="Nuovi: prima donazione nell'anno. Riammessi: ex soci tornati a donare. Cessati: per mancata "
                        "donazione da due anni, il 31/12, o per una cessazione segnata a mano.")


def soci_per_stato():
    conteggi = dict(Socio.objects.values_list("stato").annotate(n=Count("id")))
    righe = [[nome, conteggi.get(valore, 0)] for valore, nome in StatoSocio.choices]
    righe.append(["Non soci", conteggi.get("", 0)])
    return Tabella("Soci per stato, a oggi", "Soci per stato", ["Stato", "Soci"], righe, _totale(righe),
                   nota="Non soci: anagrafiche di AssoWeb che non hanno ancora donato.")


def andamento(movimenti_):
    """Riepilogo degli anni di `movimenti_`, dal più recente."""
    primo, anno = min(movimenti_), max(movimenti_)
    per_anno = _sangue_intero_e_totale(
        Donazione.objects.filter(tipo__e_donazione=True, data__year__range=(primo, anno))
        .annotate(anno=ExtractYear("data")), "anno")
    righe = []
    for a in range(anno, primo - 1, -1):
        si, n = per_anno.get(a, (0, 0))
        m = movimenti_[a]
        righe.append([a, n, si, n - si, len(m.fine), len(m.nuovi), len(m.riammessi), len(m.cessati)])
    return Tabella(f"Andamento {primo}–{anno}", "Andamento",
                   ["Anno", "Donazioni", "Sangue intero", "Aferesi", "Soci donatori", "Nuovi", "Riammessi",
                    "Cessati"], righe, barra=1,
                   nota="Soci donatori al 31/12 di ogni anno; per l'anno in corso, a oggi.")


def statistiche(anno, oggi):
    """Riepilogo e tabelle dell'anno; l'ordine delle tabelle è quello dei fogli Excel."""
    soci = soci_donatori(anno, oggi)
    riferimento = data_riferimento(anno, oggi)
    primo = max(anno - ANNI_ANDAMENTO + 1, anni_disponibili(oggi)[-1])
    movimenti_ = movimenti(range(primo, anno + 1), oggi)
    movimento = movimenti_[anno]
    return {
        "anno": anno,
        "riferimento": riferimento,
        "anno_in_corso": anno >= oggi.year,
        "riepilogo": {
            "soci_donatori": len(movimento.fine),
            "donazioni": donazioni(anno).count(),
            "nuovi": len(movimento.nuovi),
            "cessati": len(movimento.cessati),
        },
        "tabelle": {
            "eta": per_fascia_e_sesso(soci, riferimento),
            "gruppi": per_gruppo_sanguigno(soci),
            "tipi": donazioni_per_tipo(anno),
            "mesi": donazioni_per_mese(anno, oggi),
            "movimento": movimento_soci(movimento, anno, oggi),
            "stati": soci_per_stato(),
            "andamento": andamento(movimenti_),
        },
    }
