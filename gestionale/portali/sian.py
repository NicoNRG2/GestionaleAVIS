"""Client del web service del libro soci SIAN (README M5; ANALISI §5.3).

`RegistroSoci.asmx` di AVIS Nazionale, SOAP 1.2 con namespace `http://sian.mesis.it/`. Ogni richiesta porta la
sede e il token di cooperazione applicativa generato dal Presidente sul portale (`SIAN_TOKEN` in `.env`).
Il client non usa l'ORM: riceve i dati già letti e restituisce le risposte del servizio.

`invia_lista` con Tipo 1 **sostituisce l'intero registro della sede**: si lancia solo dal job dell'invio, mai per
prova. Le risposte di `GetAllarmi` e `GetLogImportazione` contengono l'anagrafica dei soci: chi le usa non le
stampa e non le scrive nel log delle esecuzioni.
"""

import datetime
import re
import ssl
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

NS = "http://sian.mesis.it/"
SOAP = "http://www.w3.org/2003/05/soap-envelope"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
PROGRAMMA = "Gestionale AVIS Altopiano"
TIMEOUT = 60
# le date del tracciato hanno l'ora di mezzanotte con il fuso italiano, come le scriveva AssoAvisPlus
FUSO = ZoneInfo("Europe/Rome")
# AssoAvisPlus divide il registro in pacchetti di circa 1 MB (ANALISI §5.3)
DIMENSIONE_PACCHETTO = 1_000_000
# invio totale: sostituisce il registro; incrementale: aggiorna i record inviati
TIPO_TOTALE = 1
# messaggi delle eccezioni .NET del servizio, che non sono avvisi sui dati
ECCEZIONE_SERVER = re.compile(r"^Conversion from |Exception|Object reference not set|was not recognized as a valid|"
                              r"Input string was not in a correct format")


class ErroreSian(Exception):
    """Il servizio non risponde, risponde con un errore HTTP o SOAP, o segnala un errore nel campo Errore."""


@dataclass
class ErroreRecord:
    """Un record segnalato dal SIAN. `xml` è il record come l'ha ricevuto: contiene dati personali."""

    critico: bool
    errori: list[str]
    xml: str

    @property
    def bloccante(self):
        """Il record non è stato acquisito: errore critico, oppure eccezione del server non segnata come critica.

        Con SESSO "M" il SIAN risponde Critico false e "Conversion from string "M" to type 'Double' is not valid",
        ma il socio non compare nel registro.
        """
        return self.critico or any(ECCEZIONE_SERVER.search(e) for e in self.errori)

    def campo(self, nome):
        """Valore di un campo del record (es. UNIVOCOAVIS); vuoto se manca o l'XML non si legge."""
        try:
            return (ET.fromstring(self.xml).findtext(f".//{nome}") or "").strip()
        except ET.ParseError:
            return ""


@dataclass
class EsitoImportazione:
    """Risposta di GetLogImportazione e di InvioSingoloTesserato. `data_analisi` vuota: analisi non ancora fatta."""

    errore: str
    totale_errori: int | None
    data_analisi: datetime.datetime | None
    record: list[ErroreRecord] = field(default_factory=list)


def is_alive(sede, token, *, url, timeout=TIMEOUT):
    """Verifica servizio e token; restituisce l'anno del registro aperto sul SIAN e la data del server."""
    risultato = _chiama(url, "IsAlive", "Alive", {"Sede": sede, "Token": token}, timeout)
    _controlla_errore(risultato)
    data = risultato.findtext(f"{{{NS}}}DataServer")
    return int(risultato.findtext(f"{{{NS}}}AnnoRegistro")), datetime.datetime.fromisoformat(data) if data else None


def invia_singolo(sede, anno, token, xml, *, url, timeout=TIMEOUT):
    """Invia un socio (`xml` con un solo Tesserato) e restituisce l'esito, che il servizio dà subito."""
    risultato = _chiama(url, "InvioSingoloTesserato", "SingoloSocio",
                        {"Sede": sede, "AnnoRiferimento": anno, "Token": token, "XML": xml, "Programma": PROGRAMMA},
                        timeout)
    return _esito(risultato)


def invia_lista(sede, anno, token, pacchetti, *, url, tipo=TIPO_TOTALE, timeout=TIMEOUT, avanza=None):
    """Invia il registro a pacchetti e restituisce l'IDUnivocoInvio, da passare a `log_importazione`.

    L'elaborazione sul SIAN è asincrona. L'ID restituito dal primo pacchetto accompagna i successivi.
    """
    id_invio = None
    for numero, xml in enumerate(pacchetti, start=1):
        risultato = _chiama(url, "InvioListaTesserati", "Registro",
                            {"Sede": sede, "AnnoRiferimento": anno, "IDInvio": id_invio, "Tipo": tipo,
                             "Token": token, "XML": xml, "Pacchetto": numero, "Totale": len(pacchetti),
                             "Programma": PROGRAMMA}, timeout)
        _controlla_errore(risultato)
        id_invio = id_invio or (risultato.findtext(f"{{{NS}}}IDUnivocoInvio") or "").strip()
        if not id_invio:
            raise ErroreSian("Il SIAN non ha restituito l'identificativo dell'invio")
        if avanza:
            avanza(f"Pacchetto {numero} di {len(pacchetti)} inviato")
    return id_invio


def log_importazione(sede, id_invio, token, *, url, timeout=TIMEOUT):
    """Esito di un invio, record per record (solo quelli con errori o avvisi)."""
    risultato = _chiama(url, "GetLogImportazione", "LogImportazione",
                        {"Sede": sede, "IDInvio": id_invio, "Token": token}, timeout)
    return _esito(risultato)


def xml_tesserati(record):
    """Campo XML delle richieste: `<ListaTesserati>` con un `<Tesserato>` per record, senza i campi vuoti.

    È il formato di `DataSet.WriteXml` di AssoAvisPlus (ANALISI §5.3): i valori nulli non si scrivono e le date
    sono in ISO con l'ora e il fuso.
    """
    lista = ET.Element("ListaTesserati")
    for campi in record:
        tesserato = ET.SubElement(lista, "Tesserato")
        for nome, valore in campi.items():
            if valore is None or valore == "":
                continue
            ET.SubElement(tesserato, nome).text = _testo(valore)
    return ET.tostring(lista, encoding="unicode")


def pacchetti(record, dimensione=DIMENSIONE_PACCHETTO):
    """Divide i record in XML di al massimo `dimensione` byte (un record da solo può superarla)."""
    risultato, gruppo, misura = [], [], 0
    for campi in record:
        lunghezza = len(xml_tesserati([campi]).encode()) - len("<ListaTesserati></ListaTesserati>")
        if gruppo and misura + lunghezza > dimensione:
            risultato.append(xml_tesserati(gruppo))
            gruppo, misura = [], 0
        gruppo.append(campi)
        misura += lunghezza
    if gruppo:
        risultato.append(xml_tesserati(gruppo))
    return risultato


def _testo(valore):
    if isinstance(valore, datetime.date):
        # come WriteXml: mezzanotte con il fuso di quel giorno (+01:00 o, con l'ora legale, +02:00)
        return datetime.datetime.combine(valore, datetime.time(), tzinfo=FUSO).isoformat()
    return str(valore)


def _chiama(url, operazione, parametro, campi, timeout):
    """Esegue l'operazione e restituisce l'elemento `<operazioneResult>` della risposta."""
    busta = ET.Element(f"{{{SOAP}}}Envelope")
    richiesta = ET.SubElement(ET.SubElement(busta, f"{{{SOAP}}}Body"), f"{{{NS}}}{operazione}")
    contenitore = ET.SubElement(richiesta, f"{{{NS}}}{parametro}")
    for nome, valore in campi.items():
        elemento = ET.SubElement(contenitore, f"{{{NS}}}{nome}")
        if valore is None:
            elemento.set(f"{{{XSI}}}nil", "true")
        else:
            elemento.text = str(valore)
    corpo = ET.tostring(busta, encoding="utf-8", xml_declaration=True)
    intestazioni = {"Content-Type": f'application/soap+xml; charset=utf-8; action="{NS}{operazione}"'}
    # validazione TLS attiva: AssoAvisPlus accettava qualunque certificato (ANALISI §5.12)
    contesto = ssl.create_default_context()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, corpo, intestazioni), timeout=timeout,
                                    context=contesto) as risposta:
            testo = risposta.read()
    except urllib.error.HTTPError as errore:
        # i SOAP Fault arrivano con lo stato 500 e il motivo nel corpo
        testo = errore.read()
        if not _fault(testo):
            raise ErroreSian(f"Il SIAN ha risposto con l'errore HTTP {errore.code}") from errore
    except (urllib.error.URLError, TimeoutError, OSError) as errore:
        raise ErroreSian(f"Server SIAN non raggiungibile ({getattr(errore, 'reason', errore)})") from errore
    if motivo := _fault(testo):
        raise ErroreSian(f"Il SIAN ha rifiutato la richiesta: {motivo}")
    try:
        radice = ET.fromstring(testo)
    except ET.ParseError as errore:
        raise ErroreSian("Risposta del SIAN non leggibile") from errore
    risultato = radice.find(f".//{{{NS}}}{operazione}Result")
    if risultato is None:
        raise ErroreSian(f"Risposta del SIAN senza {operazione}Result")
    return risultato


def _fault(testo):
    """Motivo di un SOAP Fault, oppure "" se la risposta non è un Fault."""
    try:
        radice = ET.fromstring(testo)
    except ET.ParseError:
        return ""
    fault = radice.find(f".//{{{SOAP}}}Fault")
    if fault is None:
        return ""
    motivo = fault.findtext(f".//{{{SOAP}}}Text") or "errore senza descrizione"
    # dei messaggi .NET si tiene la prima riga: le altre sono lo stack del server
    return motivo.strip().splitlines()[0]


def _controlla_errore(risultato):
    errore = (risultato.findtext(f"{{{NS}}}Errore") or "").strip()
    if errore and errore != "0":
        raise ErroreSian(f"Il SIAN ha risposto: {errore.splitlines()[0]}")


def _esito(risultato):
    totale = risultato.findtext(f"{{{NS}}}TotaleErrori")
    data = risultato.findtext(f"{{{NS}}}DataAnalisi")
    record = [
        ErroreRecord(critico=(r.findtext(f"{{{NS}}}Critico") or "").strip().lower() == "true",
                     errori=[(e.text or "").strip() for e in r.findall(f"{{{NS}}}Errori")],
                     xml=r.findtext(f"{{{NS}}}XML") or "")
        for r in risultato.iterfind(f"{{{NS}}}ListaErroriImportazione/{{{NS}}}ErroreImportazione")
    ]
    return EsitoImportazione(errore=(risultato.findtext(f"{{{NS}}}Errore") or "").strip(),
                             totale_errori=int(totale) if totale and totale.strip() else None,
                             data_analisi=datetime.datetime.fromisoformat(data) if data and data.strip() else None,
                             record=record)
