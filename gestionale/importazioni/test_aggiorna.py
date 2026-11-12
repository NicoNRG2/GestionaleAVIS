"""Test del job Aggiorna dati (download da AssoWeb + import) e della sua pagina (README M1)."""

import csv
import threading
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from gestionale.core.models import Esecuzione, Evento, StatoEsecuzione, TipoEsecuzione
from gestionale.core.worker import esegui, prossima
from gestionale.importazioni import aggiorna
from gestionale.importazioni.assoweb import FILE_DONATORI, FILE_DONAZIONI
from gestionale.importazioni.test_assoweb import csv_assoweb
from gestionale.portali import assoweb
from gestionale.portali.assoweb import ErrorePortale
from gestionale.soci.models import Donazione, Socio

SEZIONE = "AVIS Comunale Altopiano di Asiago"


def scrivi_csv(percorso, righe):
    with open(percorso, "w", newline="", encoding="utf-8") as file:
        scrittore = csv.DictWriter(file, fieldnames=list(righe[0]), delimiter=";")
        scrittore.writeheader()
        scrittore.writerows(righe)


def in_coda(utente, anno=2026):
    return Esecuzione.objects.create(tipo=TipoEsecuzione.AGGIORNA_DATI, utente=utente,
                                     parametri={"anno": anno, "sezione": "1529"})


def esegui_e_ricarica(esecuzione):
    esegui(prossima())
    esecuzione.refresh_from_db()
    return esecuzione


def test_scarica_in_un_altro_thread_e_importa(cartelle, volontario, monkeypatch):
    chiamate = []

    def scarica_finto(username, password, nome_sezione, cartella, *, anno, avanza, **opzioni):
        chiamate.append((threading.get_ident(), nome_sezione, anno, cartella))
        donatori, donazioni = csv_assoweb()
        scrivi_csv(cartella / FILE_DONATORI, donatori)
        scrivi_csv(cartella / FILE_DONAZIONI, donazioni)
        avanza("Scaricati i due CSV")

    monkeypatch.setattr(aggiorna, "scarica", scarica_finto)
    esecuzione = esegui_e_ricarica(in_coda(volontario))

    assert esecuzione.stato == StatoEsecuzione.COMPLETATA, esecuzione.log
    [(thread, nome_sezione, anno, cartella)] = chiamate
    assert thread != threading.get_ident()
    assert (nome_sezione, anno, cartella) == (SEZIONE, 2026, cartelle)
    assert (Socio.objects.count(), Donazione.objects.count()) == (4, 3)
    for testo in ["Download da AssoWeb dei donatori e delle donazioni del 2026", "Scaricati i due CSV",
                  "Import nel database", "soci nuovi", "Stato dei soci", "Anomalie"]:
        assert testo in esecuzione.log
    eventi = Evento.objects.filter(oggetto=f"esecuzione {esecuzione.pk}").order_by("azione")
    assert list(eventi.values_list("azione", "utente")) == [("download_assoweb", volontario.pk),
                                                           ("import_assoweb", volontario.pk)]


def test_download_fallito_con_screenshot(cartelle, volontario, monkeypatch):
    def scarica_finto(*args, avanza, **opzioni):
        avanza("Login su AssoWeb effettuato")
        raise ErrorePortale("Timeout 30000ms exceeded.", cartelle / "errori" / "assoweb_20261030_120000.png")

    monkeypatch.setattr(aggiorna, "scarica", scarica_finto)
    esecuzione = esegui_e_ricarica(in_coda(volontario))

    assert esecuzione.stato == StatoEsecuzione.FALLITA
    assert "Login su AssoWeb effettuato" in esecuzione.log
    assert "Errore: Timeout 30000ms exceeded." in esecuzione.log
    assert "Screenshot della pagina: " in esecuzione.log and "assoweb_20261030_120000.png" in esecuzione.log
    assert not Evento.objects.filter(azione__in=["download_assoweb", "import_assoweb"]).exists()


def test_client_playwright_vero_nel_thread_del_worker(cartelle, volontario, monkeypatch, settings):
    """Il client vero sulla copia finta del portale: Playwright nel thread separato, l'ORM in quello del worker.

    La copia finta esporta CSV senza le colonne vere, quindi il download riesce e l'import si ferma.
    """
    monkeypatch.setattr(assoweb, "URL_LOGIN", Path(assoweb.__file__).with_name("assoweb_finto.html").as_uri())
    settings.ASSOWEB_USERNAME, settings.ASSOWEB_PASSWORD, settings.PORTALI_HEADLESS = "volontario", "giusta", True

    esecuzione = esegui_e_ricarica(in_coda(volontario, anno=2025))

    assert esecuzione.stato == StatoEsecuzione.FALLITA
    for testo in ["Login su AssoWeb effettuato", "Scaricato TOT_DONAZIONI.csv del 2025",
                  f"Errore: In {FILE_DONATORI} mancano le colonne"]:
        assert testo in esecuzione.log
    assert f"01/01/2025;31/12/2025;{SEZIONE}" in (cartelle / FILE_DONAZIONI).read_text()
    assert Evento.objects.filter(azione="download_assoweb", utente=volontario).exists()
    assert not Socio.objects.exists()


def test_pagina_mette_in_coda_un_aggiornamento_alla_volta(client, volontario):
    client.force_login(volontario)
    url = reverse("aggiorna_dati")
    anno = timezone.localdate().year
    assert client.get(url).status_code == 200

    client.post(url, {"anno": anno - 1})
    esecuzione = Esecuzione.objects.get()
    assert (esecuzione.tipo, esecuzione.stato, esecuzione.utente) == (TipoEsecuzione.AGGIORNA_DATI,
                                                                       StatoEsecuzione.IN_CODA, volontario)
    assert esecuzione.parametri == {"anno": anno - 1, "sezione": "1529"}

    risposta = client.post(url, {"anno": anno}, follow=True)
    assert Esecuzione.objects.count() == 1
    assert "già un aggiornamento in coda o in corso" in risposta.text
    assert 'hx-trigger="every 2s"' in risposta.text


def test_pagina_rifiuta_un_anno_non_previsto(client, volontario):
    client.force_login(volontario)
    client.post(reverse("aggiorna_dati"), {"anno": 1990})
    assert not Esecuzione.objects.exists()


def test_pagina_solo_con_i_permessi(client, db):
    client.force_login(User.objects.create_user("senza_ruolo"))
    assert client.get(reverse("aggiorna_dati")).status_code == 403
    assert client.post(reverse("aggiorna_dati"), {"anno": timezone.localdate().year}).status_code == 403
    assert not Esecuzione.objects.exists()
