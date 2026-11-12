"""Estrazione del libro soci per il SIAN e controlli prima dell'invio (README M5; ANALISI §5.5, §5.6, §5.9).

Soci del registro dell'anno A:
- i donatori con almeno una donazione dal 01/01/A-2 al 31/12/A: con la regola dei 2 anni (README §3) sono
  soci per tutto l'anno A. Per il 2026, le donazioni dal 01/01/2024;
- tutti i collaboratori (`Socio.collaboratore`): tipo socio 1 se hanno donato in quel periodo, altrimenti 2;
- i cessati nell'anno, con data e causa: le cessazioni a mano dell'anno e, una volta finito l'anno, i donatori
  con l'ultima donazione nell'anno A-2, che cessano il 31/12/A. Il SIAN non accetta cessazioni di anni
  precedenti, quindi chi è cessato prima del 01/01/A non si invia.

Lo stato del socio non serve: la selezione si ricostruisce dalle donazioni, così vale anche a gennaio, quando sul
SIAN è ancora aperto il registro dell'anno prima.
"""

import datetime
import re
from collections import Counter
from dataclasses import dataclass, field

from django.db.models import Max, Q

from gestionale.soci.models import CausaCessazione, Socio

# le 24 colonne del tracciato, nell'ordine e con i nomi dell'Uploader SIAN (ANALISI §5.5)
COLONNE = [
    "CODICESEDE", "ANNORIFERIMENTO", "UNIVOCOAVIS", "COGNOME", "NOME", "DATANASCITA", "GENERE", "CODICEFISCALE",
    "LUOGONASCITA", "PROVNASCITA", "INDIRIZZO", "COMUNE", "PROVINCIA", "CAP", "CELLULARENONDISP", "CELLULARE",
    "EMAILNONDISP", "EMAIL", "PEC", "DATAISCRIZIONE", "TESSERA", "TIPOSOCIO", "DATACESSAZIONE", "CAUSACESSAZIONE",
]
DONATORE, COLLABORATORE = 1, 2
TIPI_SOCIO = {DONATORE: "Socio donatore", COLLABORATORE: "Socio collaboratore"}
# codifica del sesso nel web service; nel file dell'Uploader (GENERE) resta M/F
SESSO_SIAN = {"M": 1, "F": 2}

CF_FORMATO = re.compile(r"[A-Z]{6}[0-9LMNPQRSTUV]{2}[ABCDEHLMPRST][0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{3}[A-Z]")
# valori dei caratteri in posizione dispari (1ª, 3ª, ...) per il carattere di controllo
_DISPARI = dict(zip("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
                    [1, 0, 5, 7, 9, 13, 15, 17, 19, 21] * 2 + [2, 4, 18, 20, 11, 3, 6, 8, 12, 14, 16, 10, 22, 25, 24, 23]))


@dataclass
class Record:
    """Un socio nel tracciato: `campi` ha le 24 colonne con date e numeri come valori Python.

    `scarti` sono gli errori che il SIAN rifiuta: il record non si invia. `avvisi` non bloccano l'invio.
    """

    socio: Socio
    campi: dict
    scarti: list[str] = field(default_factory=list)
    avvisi: list[str] = field(default_factory=list)

    @property
    def tipo_socio(self):
        return self.campi["TIPOSOCIO"]

    @property
    def tipo_socio_nome(self):
        return TIPI_SOCIO[self.tipo_socio]

    @property
    def cessato(self):
        return self.campi["DATACESSAZIONE"] is not None

    @property
    def inviabile(self):
        return not self.scarti


@dataclass
class LibroSoci:
    anno: int
    sede: str
    record: list[Record]

    @property
    def inviabili(self):
        return [r for r in self.record if r.inviabile]

    @property
    def scartati(self):
        return [r for r in self.record if not r.inviabile]

    def riepilogo(self):
        """Conteggi come nella barra del registro sul SIAN: i cessati non sono contati tra i soci."""
        attivi = [r for r in self.record if not r.cessato]
        tipi = Counter(r.tipo_socio for r in attivi)
        return {
            "donatori": tipi[DONATORE],
            "collaboratori": tipi[COLLABORATORE],
            "soci": len(attivi),
            "nuovi": sum(1 for r in attivi if (r.campi["DATAISCRIZIONE"] or datetime.date.min).year == self.anno),
            "cessati": sum(1 for r in self.record if r.cessato),
            "totale": len(self.record),
            "inviabili": len(self.inviabili),
            "scartati": len(self.scartati),
            "con_avvisi": sum(1 for r in self.record if r.avvisi),
        }

    def motivi(self, attributo):
        """Motivi di scarto ("scarti") o avvisi ("avvisi") con i record che li hanno, dal più frequente."""
        per_motivo = {}
        for record in self.record:
            for motivo in getattr(record, attributo):
                per_motivo.setdefault(motivo, []).append(record)
        return sorted(per_motivo.items(), key=lambda m: (-len(m[1]), m[0]))


def estrai(anno, sezione, oggi):
    """Libro soci dell'anno per la sezione, con i controlli già fatti; `oggi` decide le cessazioni di fine anno."""
    inizio, fine = datetime.date(anno - 2, 1, 1), datetime.date(anno, 12, 31)
    nel_periodo = Q(donazioni__tipo__e_donazione=True, donazioni__data__range=(inizio, fine))
    soci = (Socio.objects.filter(sezione=sezione)
            .annotate(ultima=Max("donazioni__data", filter=nel_periodo))
            .filter(Q(ultima__isnull=False) | Q(collaboratore=True) | Q(data_cessazione__year=anno))
            .exclude(data_cessazione__lt=datetime.date(anno, 1, 1))
            .order_by("pk"))
    record = []
    for socio in soci:
        cessazione, causa = _cessazione(socio, anno, oggi)
        tipo = DONATORE if socio.ultima else COLLABORATORE
        record.append(_controlla(Record(socio, _campi(socio, sezione.codice, anno, tipo, cessazione, causa)), anno,
                                 oggi))
    return LibroSoci(anno, sezione.codice, record)


def _cessazione(socio, anno, oggi):
    fine = datetime.date(anno, 12, 31)
    if socio.data_cessazione and socio.data_cessazione <= fine:
        return socio.data_cessazione, socio.causa_cessazione
    # finito l'anno, chi ha donato l'ultima volta nell'anno A-2 cessa il 31/12/A (README §3); i collaboratori
    # non cessano mai
    if (not socio.collaboratore and socio.ultima and socio.ultima.year == anno - 2 and oggi >= fine
            and not socio.data_cessazione):
        return fine, CausaCessazione.CESSAZIONE
    return None, None


def _campi(socio, sede, anno, tipo, cessazione, causa):
    # l'Uploader vuole il numero senza spazi né altri caratteri
    cellulare = re.sub(r"\D", "", socio.cellulare)
    email = socio.email.strip()
    return {
        "CODICESEDE": sede,
        "ANNORIFERIMENTO": anno,
        # l'ID del socio nel gestionale: torna negli esiti del SIAN e li collega al socio senza dati personali
        "UNIVOCOAVIS": str(socio.pk),
        "COGNOME": socio.cognome.strip(),
        "NOME": socio.nome.strip(),
        "DATANASCITA": socio.data_nascita,
        "GENERE": socio.sesso,
        "CODICEFISCALE": socio.codice_fiscale or "",
        "LUOGONASCITA": socio.luogo_nascita.strip(),
        "PROVNASCITA": socio.provincia_nascita.strip(),
        "INDIRIZZO": socio.indirizzo_residenza.strip(),
        "COMUNE": socio.comune_residenza.strip(),
        "PROVINCIA": socio.provincia_residenza.strip(),
        "CAP": socio.cap_residenza.strip(),
        "CELLULARENONDISP": 0 if cellulare else 1,
        "CELLULARE": cellulare,
        "EMAILNONDISP": 0 if email else 1,
        "EMAIL": email,
        "PEC": "",
        "DATAISCRIZIONE": socio.data_iscrizione,
        "TESSERA": socio.tessera,
        "TIPOSOCIO": tipo,
        "DATACESSAZIONE": cessazione,
        "CAUSACESSAZIONE": causa,
    }


def carattere_di_controllo(primi_15):
    somma = sum(_DISPARI[c] if i % 2 == 0 else (int(c) if c.isdigit() else ord(c) - ord("A"))
                for i, c in enumerate(primi_15))
    return chr(ord("A") + somma % 26)


def codice_fiscale_valido(cf):
    """Formato (anche con l'omocodia) e carattere di controllo; la coerenza con i dati anagrafici la verifica il SIAN."""
    return bool(CF_FORMATO.fullmatch(cf or "")) and carattere_di_controllo(cf[:15]) == cf[15]


def _controlla(record, anno, oggi):
    """Controlli di ANALISI §5.9 e del manuale dell'Uploader. I messaggi non contengono dati personali."""
    c, scarti, avvisi = record.campi, record.scarti, record.avvisi
    if not c["COGNOME"] or not c["NOME"]:
        scarti.append("Cognome o nome mancante")
    if not c["CODICEFISCALE"]:
        scarti.append("Codice fiscale mancante")
    elif not codice_fiscale_valido(c["CODICEFISCALE"]):
        scarti.append("Codice fiscale non valido")
    if c["DATAISCRIZIONE"] is None:
        scarti.append("Data di iscrizione mancante")
    elif c["DATAISCRIZIONE"] > oggi or c["DATAISCRIZIONE"].year > anno:
        scarti.append("Data di iscrizione futura o dopo l'anno del registro")
    if c["DATACESSAZIONE"] is not None and c["CAUSACESSAZIONE"] not in CausaCessazione.values:
        scarti.append("Causa di cessazione mancante")
    if c["DATANASCITA"] is None:
        avvisi.append("Data di nascita mancante")
    if c["GENERE"] not in ("M", "F"):
        avvisi.append("Sesso mancante")
    if not c["LUOGONASCITA"] or not c["PROVNASCITA"]:
        avvisi.append("Luogo o provincia di nascita mancante")
    if not (c["INDIRIZZO"] and c["COMUNE"] and c["PROVINCIA"] and c["CAP"]):
        avvisi.append("Residenza incompleta")
    elif not re.fullmatch(r"\d{5}", c["CAP"]):
        avvisi.append("CAP non di 5 cifre")
    if c["CELLULARENONDISP"]:
        avvisi.append("Cellulare non disponibile")
    if c["EMAILNONDISP"]:
        avvisi.append("Email non disponibile")
    elif not re.fullmatch(r"[^@\s;,]+@[^@\s;,]+\.[^@\s;,]+", c["EMAIL"]):
        avvisi.append("Email non valida")
    return record


def record_xml(campi):
    """Campi di un record per il web service: i nomi della query di AssoAvisPlus (ANALISI §5.6).

    Rispetto al file dell'Uploader il sesso si chiama SESSO ed è un numero, 1 maschio e 2 femmina (ANALISI §5.5): con
    "M" il SIAN risponde "Conversion from string "M" to type 'Double' is not valid" e non acquisisce il socio.
    La causa vale 0 per i soci attivi e TIPORECORD 1 vuol dire inserimento o aggiornamento.
    """
    campi = dict(campi)
    campi["SESSO"] = SESSO_SIAN.get(campi.pop("GENERE"))
    campi["CAUSACESSAZIONE"] = campi["CAUSACESSAZIONE"] or 0
    campi["TIPORECORD"] = 1
    return campi
