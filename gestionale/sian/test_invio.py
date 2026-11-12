"""Job dell'invio al SIAN e pagine del libro soci, con il web service sostituito da un SIAN finto."""

import datetime
from io import BytesIO

import pytest
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from gestionale.core.models import Esecuzione, Evento, Sezione, StatoEsecuzione, TipoEsecuzione
from gestionale.core.worker import esegui
from gestionale.portali import sian
from gestionale.soci.models import Donazione, Fonte, Socio, StatoSocio, TipoDonazione

from . import invio
from .models import EsitoSian, InvioSian, ModalitaSian, OperazioneSian
from .test_libro_soci import cf

ANNO = timezone.localdate().year


class SianFinto:
    """Al posto delle funzioni di portali/sian.py: registra le chiamate e risponde come il SIAN."""

    def __init__(self, monkeypatch, anno_registro=ANNO, esiti=()):
        self.anno_registro, self.esiti, self.chiamate = anno_registro, list(esiti), []
        for nome in ("is_alive", "invia_lista", "log_importazione", "invia_singolo"):
            monkeypatch.setattr(sian, nome, getattr(self, nome))
        monkeypatch.setattr(invio.time, "sleep", lambda secondi: None)

    def is_alive(self, sede, token, *, url, timeout=None):
        self.chiamate.append(("is_alive", sede))
        return self.anno_registro, None

    def invia_lista(self, sede, anno, token, pacchetti, *, url, avanza=None):
        self.chiamate.append(("invia_lista", sede, anno, pacchetti))
        return "ID-1"

    def log_importazione(self, sede, id_invio, token, *, url):
        self.chiamate.append(("log_importazione", id_invio))
        return self.esiti.pop(0)

    def invia_singolo(self, sede, anno, token, xml, *, url):
        self.chiamate.append(("invia_singolo", sede, anno, xml))
        return self.esiti.pop(0)


def esito(*record, pronto=True, errore=""):
    return sian.EsitoImportazione(errore=errore, totale_errori=len(record),
                                  data_analisi=timezone.now() if pronto else None, record=list(record))


def segnalazione(socio, *errori, critico=True):
    xml = f"<ListaTesserati><Tesserato><UNIVOCOAVIS>{socio.pk}</UNIVOCOAVIS></Tesserato></ListaTesserati>"
    return sian.ErroreRecord(critico=critico, errori=list(errori), xml=xml)


@pytest.fixture
def soci(db, settings):
    settings.SIAN_TOKEN = "token-di-prova"
    sezione = Sezione.objects.get(codice="1529")
    si = TipoDonazione.objects.get(codice="SI")

    def socio(cognome, numero, **campi):
        valori = {"codice_fiscale": cf(numero), "data_iscrizione": datetime.date(2010, 1, 1)} | campi
        s = Socio.objects.create(sezione=sezione, cognome=cognome, nome="X", stato=StatoSocio.DONATORE, **valori)
        Donazione.objects.create(socio=s, data=datetime.date(ANNO, 1, 10), tipo=si, fonte=Fonte.ASSOWEB)
        return s

    return {"anna": socio("ANNA", 1, sesso="F"), "bruno": socio("BRUNO", 2), "carla": socio("CARLA", 3),
            "senza_cf": socio("SENZA_CF", 4, codice_fiscale=None)}


def esecuzione(utente=None, **parametri):
    return Esecuzione.objects.create(tipo=TipoEsecuzione.INVIO_SIAN, utente=utente, parametri={
        "anno": ANNO, "sezione": "1529", "modalita": ModalitaSian.TOTALE} | parametri)


def test_invio_totale_con_esito(soci, monkeypatch, amministratore):
    finto = SianFinto(monkeypatch, esiti=[esito(pronto=False), esito(
        segnalazione(soci["bruno"], "Codice fiscale non congruente"),
        segnalazione(soci["carla"], "Cellulare mancante", critico=False))])
    e = esecuzione(amministratore)

    esegui(e)

    e.refresh_from_db()
    assert e.stato == StatoEsecuzione.COMPLETATA, e.log
    _, sede, anno, pacchetti = finto.chiamate[1]
    assert (sede, anno, len(pacchetti)) == ("1529", ANNO, 1)
    # il socio senza codice fiscale non parte
    assert pacchetti[0].count("<Tesserato>") == 3 and f"<UNIVOCOAVIS>{soci['senza_cf'].pk}<" not in pacchetti[0]
    assert "<GENERE>" not in pacchetti[0] and pacchetti[0].count("<TIPORECORD>1</TIPORECORD>") == 3
    invio_sian = InvioSian.objects.get()
    assert (invio_sian.esecuzione, invio_sian.id_invio, invio_sian.sede) == (e, "ID-1", "1529")
    assert invio_sian.esito_il is not None
    esiti = {r.socio.cognome: (r.esito, r.messaggio) for r in invio_sian.righe.all()}
    assert esiti == {
        "ANNA": (EsitoSian.OK, ""),
        "BRUNO": (EsitoSian.ERRORE, "Codice fiscale non congruente"),
        "CARLA": (EsitoSian.OK, "Cellulare mancante"),
        "SENZA_CF": (EsitoSian.SCARTATO, "Codice fiscale mancante"),
    }
    assert invio_sian.righe.filter(operazione=OperazioneSian.INSERIMENTO, tipo_socio=1).count() == 4
    # nel log solo conteggi, nessuna segnalazione del SIAN
    assert "Esito del SIAN: 2 soci acquisiti, di cui 1 con avvisi; 1 con errori" in e.log
    assert "congruente" not in e.log and "BRUNO" not in e.log
    evento = Evento.objects.get(azione="invio_sian")
    assert evento.utente == amministratore and evento.dettagli["inviati"] == 3 and evento.dettagli["scartati"] == 1


def test_esito_non_ancora_pronto(soci, monkeypatch):
    SianFinto(monkeypatch, esiti=[esito(pronto=False)] * 3)
    monkeypatch.setattr(invio, "ATTESA_ESITO", 0)
    e = esecuzione()

    esegui(e)

    e.refresh_from_db()
    assert e.stato == StatoEsecuzione.COMPLETATA
    assert "con il bottone Aggiorna l'esito" in e.log
    assert InvioSian.objects.get().righe.filter(esito=EsitoSian.INVIATO).count() == 3


def test_registro_aperto_di_un_altro_anno(soci, monkeypatch):
    finto = SianFinto(monkeypatch, anno_registro=ANNO - 1)
    e = esecuzione()

    esegui(e)

    e.refresh_from_db()
    assert e.stato == StatoEsecuzione.FALLITA
    assert f"Sul SIAN è aperto il registro {ANNO - 1}, non quello del {ANNO}" in e.log
    assert [c[0] for c in finto.chiamate] == ["is_alive"] and not InvioSian.objects.exists()


def test_token_mancante(soci, monkeypatch, settings):
    settings.SIAN_TOKEN = ""
    finto = SianFinto(monkeypatch)
    e = esecuzione()

    esegui(e)

    e.refresh_from_db()
    assert e.stato == StatoEsecuzione.FALLITA and "SIAN_TOKEN" in e.log and not finto.chiamate


def test_invio_di_un_socio(soci, monkeypatch):
    finto = SianFinto(monkeypatch, esiti=[esito(segnalazione(soci["anna"], "Email mancante", critico=False))])
    e = esecuzione(modalita=ModalitaSian.SINGOLO, socio=soci["anna"].pk)

    esegui(e)

    e.refresh_from_db()
    assert e.stato == StatoEsecuzione.COMPLETATA, e.log
    _, sede, anno, xml = finto.chiamate[1]
    assert xml.count("<Tesserato>") == 1 and f"<UNIVOCOAVIS>{soci['anna'].pk}</UNIVOCOAVIS>" in xml
    assert "<GENERE>" not in xml and "<SESSO>2</SESSO>" in xml and "<CAUSACESSAZIONE>0</CAUSACESSAZIONE>" in xml
    riga = InvioSian.objects.get(modalita=ModalitaSian.SINGOLO).righe.get()
    assert (riga.socio, riga.esito, riga.messaggio) == (soci["anna"], EsitoSian.OK, "Email mancante")
    assert "Esito: riuscito, 1 segnalazione del SIAN nella pagina dell'invio" in e.log
    assert "Email mancante" not in e.log


def test_eccezione_del_server_non_critica_e_un_errore(soci, monkeypatch):
    # risposta vera del SIAN con SESSO "M": Critico false, ma il socio non è acquisito
    messaggio = "Conversion from string \"M\" to type 'Double' is not valid."
    SianFinto(monkeypatch, esiti=[esito(sian.ErroreRecord(critico=False, errori=[messaggio], xml=""))])
    e = esecuzione(modalita=ModalitaSian.SINGOLO, socio=soci["anna"].pk)

    esegui(e)

    riga = InvioSian.objects.get().righe.get()
    assert (riga.esito, riga.messaggio) == (EsitoSian.ERRORE, messaggio)
    assert "Esito: errore, 1 segnalazione del SIAN" in Esecuzione.objects.get().log


def test_invio_di_un_socio_che_non_supera_i_controlli(soci, monkeypatch):
    finto = SianFinto(monkeypatch)
    e = esecuzione(modalita=ModalitaSian.SINGOLO, socio=soci["senza_cf"].pk)

    esegui(e)

    e.refresh_from_db()
    assert e.stato == StatoEsecuzione.FALLITA
    assert f"Il socio {soci['senza_cf'].pk} non supera i controlli: Codice fiscale mancante" in e.log
    assert [c[0] for c in finto.chiamate] == ["is_alive"]


def test_invio_di_un_socio_rifiutato_dal_sian(soci, monkeypatch):
    SianFinto(monkeypatch, esiti=[esito(errore="Anno di riferimento estrazione non corretto")])
    e = esecuzione(modalita=ModalitaSian.SINGOLO, socio=soci["anna"].pk)

    esegui(e)

    e.refresh_from_db()
    assert e.stato == StatoEsecuzione.FALLITA and "Anno di riferimento estrazione non corretto" in e.log
    assert InvioSian.objects.get().righe.get().esito == EsitoSian.ERRORE


# pagine


def test_pagina_solo_per_gli_amministratori(soci, client, volontario, amministratore):
    client.force_login(volontario)
    assert client.get(reverse("libro_soci")).status_code == 403

    client.force_login(amministratore)
    risposta = client.get(reverse("libro_soci"))

    assert risposta.status_code == 200
    riepilogo = risposta.context["riepilogo"]
    assert (riepilogo["soci"], riepilogo["inviabili"], riepilogo["scartati"]) == (4, 3, 1)
    assert risposta.context["scarti"][0][0] == "Codice fiscale mancante"


def test_stato_del_sian(soci, client, amministratore, monkeypatch):
    SianFinto(monkeypatch, anno_registro=ANNO - 1)
    client.force_login(amministratore)

    risposta = client.get(reverse("libro_soci_stato"), {"anno": ANNO})

    assert f"Sul SIAN è aperto il registro {ANNO - 1}" in risposta.content.decode()


def test_invio_messo_in_coda_solo_con_la_conferma(soci, client, amministratore, volontario):
    client.force_login(volontario)
    assert client.post(reverse("libro_soci_invia"), {"anno": ANNO, "conferma": "1"}).status_code == 403

    client.force_login(amministratore)
    client.post(reverse("libro_soci_invia"), {"anno": ANNO})
    assert not Esecuzione.objects.exists()

    risposta = client.post(reverse("libro_soci_invia"), {"anno": ANNO, "conferma": "1"})

    e = Esecuzione.objects.get()
    assert risposta.url == reverse("esecuzione", args=[e.pk])
    assert (e.tipo, e.utente, e.parametri) == (TipoEsecuzione.INVIO_SIAN, amministratore,
                                               {"anno": ANNO, "sezione": "1529", "modalita": "totale"})
    # un invio alla volta
    client.post(reverse("libro_soci_invia"), {"anno": ANNO, "conferma": "1"})
    assert Esecuzione.objects.count() == 1


def test_prova_con_un_socio(soci, client, amministratore):
    client.force_login(amministratore)

    client.post(reverse("libro_soci_prova"), {"anno": ANNO, "socio": soci["anna"].pk})

    assert Esecuzione.objects.get().parametri == {"anno": ANNO, "sezione": "1529", "modalita": "singolo",
                                                  "socio": soci["anna"].pk}


def test_export_excel(soci, client, amministratore):
    client.force_login(amministratore)

    risposta = client.get(reverse("libro_soci_esporta"), {"anno": ANNO})

    assert risposta["Content-Disposition"] == f'attachment; filename="libro_soci_1529_{ANNO}.xlsx"'
    assert load_workbook(BytesIO(risposta.content)).active.max_row == 4
    assert Evento.objects.get(azione="export_libro_soci").dettagli == {"anno": ANNO, "record": 3}


def test_dettaglio_e_aggiornamento_dell_esito(soci, client, amministratore, monkeypatch):
    SianFinto(monkeypatch, esiti=[esito(pronto=False), esito(pronto=False),
                                  esito(segnalazione(soci["bruno"], "Errore grave"))])
    monkeypatch.setattr(invio, "ATTESA_ESITO", 0)
    e = esecuzione(amministratore)
    esegui(e)
    invio_sian = InvioSian.objects.get()
    client.force_login(amministratore)

    pagina = client.get(reverse("invio_sian", args=[invio_sian.pk]))
    assert pagina.context["in_attesa"]
    assert dict(pagina.context["conteggi"])["In attesa dell'esito"] == 3

    # prima risposta: ancora in elaborazione
    client.post(reverse("invio_sian_esito", args=[invio_sian.pk]))
    assert invio_sian.righe.filter(esito=EsitoSian.INVIATO).count() == 3
    client.post(reverse("invio_sian_esito", args=[invio_sian.pk]))

    pagina = client.get(reverse("invio_sian", args=[invio_sian.pk]))
    assert not pagina.context["in_attesa"]
    assert [r.esito for r in pagina.context["righe"]] == ["errore", "scartato", "ok", "ok"]
    assert Evento.objects.get(azione="esito_invio_sian").dettagli["errori"] == 1
