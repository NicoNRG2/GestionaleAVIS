"""Client del web service SIAN con un server finto: nessuna chiamata al SIAN vero."""

import datetime
import io
import urllib.error
import xml.etree.ElementTree as ET

import pytest

from gestionale.portali import sian

URL = "https://sian.invalid/RegistroSoci.asmx"


def risposta(operazione, corpo):
    return (f'<?xml version="1.0" encoding="utf-8"?><soap:Envelope xmlns:soap="{sian.SOAP}"><soap:Body>'
            f'<{operazione}Response xmlns="{sian.NS}"><{operazione}Result>{corpo}</{operazione}Result>'
            f'</{operazione}Response></soap:Body></soap:Envelope>').encode()


FAULT = (f'<soap:Envelope xmlns:soap="{sian.SOAP}"><soap:Body><soap:Fault><soap:Code><soap:Value>soap:Receiver'
         '</soap:Value></soap:Code><soap:Reason><soap:Text xml:lang="it">Server was unable to process request.\n'
         '   at Stack</soap:Text></soap:Reason></soap:Fault></soap:Body></soap:Envelope>').encode()


class Server:
    """Sostituisce urllib.request.urlopen: restituisce le risposte in ordine e registra le richieste."""

    def __init__(self, monkeypatch, *risposte):
        self.risposte, self.richieste = list(risposte), []
        monkeypatch.setattr(sian.urllib.request, "urlopen", self)

    def __call__(self, richiesta, timeout, context):
        self.richieste.append(richiesta)
        prossima = self.risposte.pop(0)
        if isinstance(prossima, Exception):
            raise prossima
        return io.BytesIO(prossima)

    def campi(self, indice=-1):
        """Campi della richiesta inviata, come {nome: testo}."""
        corpo = ET.fromstring(self.richieste[indice].data)
        contenitore = corpo.find(f"{{{sian.SOAP}}}Body")[0][0]
        return {e.tag.split("}")[1]: e.text for e in contenitore}


def test_is_alive(monkeypatch):
    server = Server(monkeypatch, risposta("IsAlive", "<Errore /><DataServer>2026-11-02T20:32:02.28+01:00</DataServer>"
                                                     "<AnnoRegistro>2026</AnnoRegistro>"))

    anno, data_server = sian.is_alive("1529", "segreto", url=URL)

    assert anno == 2026 and data_server.date() == datetime.date(2026, 11, 2)
    richiesta = server.richieste[0]
    assert richiesta.full_url == URL
    assert richiesta.get_header("Content-type") == ('application/soap+xml; charset=utf-8; '
                                                    'action="http://sian.mesis.it/IsAlive"')
    assert server.campi() == {"Sede": "1529", "Token": "segreto"}


def test_errore_del_servizio(monkeypatch):
    Server(monkeypatch, risposta("IsAlive", "<Errore>Token non valido</Errore><DataServer>2026-11-02T20:32:02</DataServer>"
                                            "<AnnoRegistro>0</AnnoRegistro>"))

    with pytest.raises(sian.ErroreSian, match="Il SIAN ha risposto: Token non valido"):
        sian.is_alive("1529", "scaduto", url=URL)


def test_soap_fault_con_errore_http(monkeypatch):
    Server(monkeypatch, urllib.error.HTTPError(URL, 500, "Internal Server Error", {}, io.BytesIO(FAULT)))

    with pytest.raises(sian.ErroreSian, match=r"rifiutato la richiesta: Server was unable to process request\.$"):
        sian.is_alive("1529", "t", url=URL)


def test_server_non_raggiungibile(monkeypatch):
    Server(monkeypatch, urllib.error.URLError("timed out"))

    with pytest.raises(sian.ErroreSian, match="Server SIAN non raggiungibile"):
        sian.is_alive("1529", "t", url=URL)


def test_xml_dei_tesserati():
    xml = sian.xml_tesserati([
        {"COGNOME": "D'ARIA & C", "DATANASCITA": datetime.date(1980, 1, 1), "PEC": "", "DATACESSAZIONE": None,
         "TIPOSOCIO": 1},
        {"COGNOME": "B", "DATANASCITA": datetime.date(1980, 7, 1)},
    ])

    assert xml == ("<ListaTesserati><Tesserato><COGNOME>D'ARIA &amp; C</COGNOME>"
                   "<DATANASCITA>1980-01-01T00:00:00+01:00</DATANASCITA><TIPOSOCIO>1</TIPOSOCIO></Tesserato>"
                   "<Tesserato><COGNOME>B</COGNOME><DATANASCITA>1980-07-01T00:00:00+02:00</DATANASCITA></Tesserato>"
                   "</ListaTesserati>")


def test_pacchetti():
    record = [{"COGNOME": f"SOCIO{i:03}"} for i in range(10)]
    lunghezza = len(sian.xml_tesserati(record[:1]).encode()) - len("<ListaTesserati></ListaTesserati>")

    pacchetti = sian.pacchetti(record, dimensione=lunghezza * 4)

    assert [p.count("<Tesserato>") for p in pacchetti] == [4, 4, 2]
    assert sian.pacchetti(record) == [sian.xml_tesserati(record)]


def test_invio_della_lista_a_pacchetti(monkeypatch):
    server = Server(monkeypatch,
                    risposta("InvioListaTesserati", "<Errore /><IDUnivocoInvio>ABC-1</IDUnivocoInvio>"),
                    risposta("InvioListaTesserati", "<Errore /><IDUnivocoInvio>ABC-1</IDUnivocoInvio>"))
    avanzamento = []

    id_invio = sian.invia_lista("1529", 2026, "t", ["<ListaTesserati>1</ListaTesserati>", "<ListaTesserati>2"
                                                    "</ListaTesserati>"], url=URL, avanza=avanzamento.append)

    assert id_invio == "ABC-1"
    primo, secondo = server.campi(0), server.campi(1)
    assert primo["IDInvio"] is None and secondo["IDInvio"] == "ABC-1"
    assert (primo["Tipo"], primo["Pacchetto"], primo["Totale"]) == ("1", "1", "2")
    assert secondo["Pacchetto"] == "2" and secondo["XML"] == "<ListaTesserati>2</ListaTesserati>"
    assert primo["Programma"] == sian.PROGRAMMA and primo["AnnoRiferimento"] == "2026"
    # il primo IDInvio è nullo (xsi:nil), come vuole il WSDL
    assert b'nil="true"' in server.richieste[0].data
    assert avanzamento == ["Pacchetto 1 di 2 inviato", "Pacchetto 2 di 2 inviato"]


def test_log_importazione(monkeypatch):
    xml_record = "<ListaTesserati><Tesserato><UNIVOCOAVIS>42</UNIVOCOAVIS></Tesserato></ListaTesserati>"
    Server(monkeypatch, risposta("GetLogImportazione", (
        "<Errore /><TotaleErrori>2</TotaleErrori><DataAnalisi>2026-11-02T21:00:00+01:00</DataAnalisi>"
        "<ListaErroriImportazione>"
        f"<ErroreImportazione><Critico>true</Critico><Errori>Codice fiscale non valido</Errori>"
        f"<XML>{xml_record.replace('<', '&lt;')}</XML></ErroreImportazione>"
        "<ErroreImportazione><Critico>false</Critico><Errori>Cellulare mancante</Errori><Errori>Email mancante</Errori>"
        "<XML>non xml</XML></ErroreImportazione>"
        "</ListaErroriImportazione>")))

    esito = sian.log_importazione("1529", "ABC-1", "t", url=URL)

    assert esito.totale_errori == 2 and esito.data_analisi.hour == 21
    critico, avviso = esito.record
    assert critico.critico and critico.errori == ["Codice fiscale non valido"] and critico.campo("UNIVOCOAVIS") == "42"
    assert not avviso.critico and avviso.errori == ["Cellulare mancante", "Email mancante"]
    assert avviso.campo("UNIVOCOAVIS") == ""


def test_log_importazione_non_ancora_elaborato(monkeypatch):
    Server(monkeypatch, risposta("GetLogImportazione", f'<Errore /><TotaleErrori xsi:nil="true" xmlns:xsi="{sian.XSI}" />'
                                                       f'<DataAnalisi xsi:nil="true" xmlns:xsi="{sian.XSI}" />'
                                                       f'<ListaErroriImportazione xsi:nil="true" xmlns:xsi="{sian.XSI}" />'))

    esito = sian.log_importazione("1529", "ABC-1", "t", url=URL)

    assert esito.data_analisi is None and esito.totale_errori is None and esito.record == []


@pytest.mark.parametrize(("critico", "errori", "bloccante"), [
    (True, ["Codice fiscale non valido"], True),
    (False, ["Cellulare non presente"], False),
    (False, ["Conversion from string \"M\" to type 'Double' is not valid."], True),
    (False, ["System.NullReferenceException: Object reference not set to an instance of an object."], True),
    (False, [], False),
])
def test_segnalazioni_bloccanti(critico, errori, bloccante):
    assert sian.ErroreRecord(critico=critico, errori=errori, xml="").bloccante is bloccante
