"""Test del client AssoWeb sulla copia finta del portale (assoweb_finto.html): niente rete né credenziali vere."""

import datetime
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from gestionale.core.models import Evento
from gestionale.portali import assoweb
from gestionale.portali.assoweb import ErrorePortale, invia_sms, scarica, sessione, sms_convocazione

SEZIONE = "AVIS Comunale Altopiano di Asiago"
TESTO = "AVVISO AI DONATORI\n\nPosti liberi lunedì 9 novembre"
GIORNATA = datetime.date(2026, 11, 9)


@pytest.fixture(autouse=True)
def portale_finto(monkeypatch):
    monkeypatch.setattr(assoweb, "URL_LOGIN", Path(__file__).with_name("assoweb_finto.html").as_uri())


def sms_sul_portale_finto(filtro, **opzioni):
    """Esegue sms_convocazione e restituisce i donatori selezionati, gli SMS inviati, le finestre annullate e i messaggi."""
    messaggi = []
    with sessione("volontario", "giusta", timeout_ms=5000) as pagina:
        selezionati = sms_convocazione(pagina, filtro, TESTO, avanza=messaggi.append, **opzioni)
        # il banner "Messaggio inviato" compare dopo; qui la funzione è già tornata
        return selezionati, pagina.evaluate("window.inviati"), pagina.evaluate("window.annullati"), messaggi


def test_scarica_i_due_csv_filtrati_per_sezione_e_anno(tmp_path):
    messaggi = []
    donatori, donazioni = scarica("volontario", "giusta", SEZIONE, tmp_path, anno=2025, avanza=messaggi.append)

    assert (donatori, donazioni) == (tmp_path / "DONATORI_TOT.csv", tmp_path / "TOT_DONAZIONI.csv")
    assert donatori.read_text() == f"SEZIONE\n{SEZIONE}\n"
    assert donazioni.read_text() == f"DA;A;SEZIONE\n01/01/2025;31/12/2025;{SEZIONE}\n"
    assert messaggi == ["Login su AssoWeb effettuato", "Scaricato DONATORI_TOT.csv",
                        "Scaricato TOT_DONAZIONI.csv del 2025"]


def test_credenziali_mancanti(tmp_path):
    with pytest.raises(ErrorePortale, match="Credenziali di AssoWeb mancanti"):
        scarica("volontario", "", SEZIONE, tmp_path)


def test_pagina_bloccata_dal_firewall(tmp_path, monkeypatch):
    monkeypatch.setattr(assoweb, "URL_LOGIN", assoweb.URL_LOGIN + "?bloccato")
    with pytest.raises(ErrorePortale, match="firewall di AssoWeb") as errore:
        scarica("volontario", "giusta", SEZIONE, tmp_path, cartella_errori=tmp_path / "errori", timeout_ms=2000)
    assert errore.value.screenshot.exists()


def test_login_fallito_con_screenshot(tmp_path):
    with pytest.raises(ErrorePortale, match="Login su AssoWeb non riuscito") as errore:
        scarica("volontario", "sbagliata", SEZIONE, tmp_path, cartella_errori=tmp_path / "errori", timeout_ms=2000)
    assert errore.value.screenshot.parent == tmp_path / "errori"
    assert errore.value.screenshot.exists()


def test_errore_durante_il_download_con_screenshot(tmp_path):
    with pytest.raises(ErrorePortale, match="Timeout 2000ms") as errore:
        scarica("volontario", "giusta", "Sezione che non esiste", tmp_path, cartella_errori=tmp_path / "errori",
                timeout_ms=2000)
    assert errore.value.screenshot.exists()
    assert not (tmp_path / "DONATORI_TOT.csv").exists()


def test_sms_a_tutte_le_pagine_dei_risultati():
    selezionati, inviati, annullati, messaggi = sms_sul_portale_finto("AvisAltopianoMessaggio", giornata=GIORNATA)

    assert selezionati == 230
    # il filtro salvato ha già Sangue intero: il client non lo toglie
    assert inviati == [{"filtro": "AvisAltopianoMessaggio", "tipi": "Sangue intero", "data": "09/11/2026",
                        "testo": TESTO, "destinatari": 230}]
    assert annullati == 0
    assert messaggi == [
        "Ricerca e convocazione con il filtro AvisAltopianoMessaggio e la data prevista donazione 09/11/2026",
        "Pagina 1: 100 donatori selezionati su 100",
        "Pagina 2: 100 donatori selezionati su 100",
        "Pagina 3: 30 donatori selezionati su 30",
        "AssoWeb ha risposto «Messaggio inviato»",
    ]


def test_sms_simulazione_si_ferma_su_annulla():
    selezionati, inviati, annullati, messaggi = sms_sul_portale_finto("AvisAltopianoMessaggio", giornata=GIORNATA,
                                                                      invia=False)
    assert (selezionati, inviati, annullati) == (230, [], 1)
    assert messaggi[-1] == "Simulazione: finestra Invia SMS chiusa con Annulla, nessun SMS inviato"


def test_sms_senza_giornata_lascia_vuota_la_data():
    # con un filtro che non dipende dalla giornata
    selezionati, inviati, _, messaggi = sms_sul_portale_finto("test")
    assert selezionati == 1
    assert inviati == [{"filtro": "test", "tipi": "", "data": "", "testo": TESTO, "destinatari": 1}]
    assert messaggi[0] == "Ricerca e convocazione con il filtro test"


def test_sms_sceglie_il_filtro_esatto_e_sangue_intero():
    # sul portale finto c'è anche il filtro "test2"; "test" non ha un tipo di donazione salvato
    selezionati, inviati, _, _ = sms_sul_portale_finto("test", giornata=GIORNATA)
    assert selezionati == 1
    assert inviati == [{"filtro": "test", "tipi": "Sangue intero", "data": "09/11/2026", "testo": TESTO,
                        "destinatari": 1}]


def test_sms_filtro_senza_donatori_non_apre_la_finestra():
    selezionati, inviati, annullati, messaggi = sms_sul_portale_finto("vuoto", giornata=GIORNATA)
    assert (selezionati, inviati, annullati) == (0, [], 0)
    assert messaggi[-1] == "Nessun donatore trovato"


def test_sms_filtro_che_non_esiste(tmp_path):
    with pytest.raises(ErrorePortale, match="Il filtro «AvisAltopiano» non è tra i filtri di ricerca salvati") as errore:
        invia_sms("volontario", "giusta", "AvisAltopiano", TESTO, giornata=GIORNATA, cartella_errori=tmp_path,
                  timeout_ms=5000)
    assert errore.value.screenshot.exists()


def test_sms_pagina_coperta_dice_da_cosa(tmp_path, monkeypatch):
    # un flyout che non si chiude copre il filtro: invece di aspettare in silenzio, l'errore dice cosa lo copre
    monkeypatch.setattr(assoweb, "URL_LOGIN", assoweb.URL_LOGIN + "?coperta")
    with pytest.raises(ErrorePortale, match="Il primo elemento della pagina è coperto da div.sidebar-flyout") as errore:
        invia_sms("volontario", "giusta", "test", TESTO, giornata=GIORNATA, cartella_errori=tmp_path, timeout_ms=5000)
    assert errore.value.screenshot.exists()


def test_sms_senza_messaggio_inviato_avvisa_di_controllare(tmp_path, monkeypatch):
    monkeypatch.setattr(assoweb, "URL_LOGIN", assoweb.URL_LOGIN + "?senzaconferma")
    monkeypatch.setattr(assoweb, "ATTESA_INVIO_MS", 2000)
    with pytest.raises(ErrorePortale, match="gli SMS potrebbero essere partiti"):
        invia_sms("volontario", "giusta", "test", TESTO, giornata=GIORNATA, timeout_ms=5000)


@pytest.mark.django_db
def test_comando_scarica_l_anno_corrente_e_registra_l_evento(tmp_path, settings):
    # usa l'ORM prima e dopo il download: dentro sync_playwright() Django lo rifiuterebbe
    settings.ASSOWEB_USERNAME, settings.ASSOWEB_PASSWORD = "volontario", "giusta"
    settings.PORTALI_HEADLESS, settings.PORTALI_CARTELLA_ERRORI = True, tmp_path / "errori"
    anno = datetime.date.today().year

    call_command("scarica_assoweb", cartella=tmp_path, stdout=StringIO())

    assert f"01/01/{anno};31/12/{anno};{SEZIONE}" in (tmp_path / "TOT_DONAZIONI.csv").read_text()
    evento = Evento.objects.get(azione="download_assoweb")
    assert (evento.nome_utente, evento.dettagli) == ("manage.py scarica_assoweb", {"sezione": "1529", "anno": anno})
