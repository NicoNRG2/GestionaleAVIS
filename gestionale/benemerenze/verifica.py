"""Regressione del motore sulle premiazioni storiche di AssoAVIS (README §8).

Per ogni premiato si ricalcolano le donazioni alla data di riferimento della sua premiazione e si confrontano con
quelle salvate da AssoAVIS (NUMDONAZIONI e NUMDONAZEFF). Esito atteso: dal 2007 le valide coincidono per tutti i
premiati con le effettive uguali, salvo un socio a cui AssoWeb ha corretto il sesso. Le effettive diverse vengono
da donazioni modificate dopo la premiazione. Nel 2005-2006 AssoAVIS non salvava le effettive, quindi le donazioni
modificate dopo non si distinguono: lì coincidono 131 valide su 146.
"""

from dataclasses import dataclass, field

from gestionale.soci.models import Socio

from . import regole
from .calcolo import dati_soci
from .models import Premiazione


@dataclass
class Riga:
    premiazione: Premiazione
    statuto: str
    premiati: int = 0  # righe con una benemerenza dei donatori, le sole che il motore calcola
    collaboratori: int = 0  # righe con una benemerenza dei collaboratori, non confrontate
    effettive_salvate: bool = True
    effettive_uguali: int = 0
    # tra i premiati con le effettive uguali: le valide dipendono solo dalle regole
    valide_uguali: int = 0
    maturate: int = 0  # la benemerenza risulta maturata con le regole dello statuto
    # ID dei soci con le effettive uguali e le valide diverse, da guardare uno per uno
    valide_diverse: list = field(default_factory=list)


def verifica():
    righe = []
    for premiazione in (Premiazione.objects.filter(codice_assoavis__isnull=False, data_riferimento__isnull=False)
                        .order_by("data_riferimento", "codice_assoavis")):
        statuto = regole.statuto(premiazione.data_riferimento)
        riga = Riga(premiazione, statuto.nome)
        premiati = list(premiazione.premiati.values_list("socio_id", "benemerenza__codice", "donazioni_valide",
                                                         "donazioni_effettive"))
        riga.effettive_salvate = any(effettive for *_, effettive in premiati)
        dati = dati_soci(Socio.objects.filter(pk__in={p[0] for p in premiati}))
        regola = {r.benemerenza: r for r in statuto.regole}
        for socio_id, codice, valide, effettive in premiati:
            if codice not in regole.LIVELLO:
                riga.collaboratori += 1
                continue
            riga.premiati += 1
            conteggio = regole.conta(dati[socio_id], premiazione.data_riferimento, statuto)
            if codice in regola and regola[codice].maturata(conteggio.valide, conteggio.anni_iscrizione):
                riga.maturate += 1
            if riga.effettive_salvate and conteggio.effettive != effettive:
                continue
            riga.effettive_uguali += 1
            if conteggio.valide == valide:
                riga.valide_uguali += 1
            else:
                riga.valide_diverse.append(socio_id)
        righe.append(riga)
    return righe
