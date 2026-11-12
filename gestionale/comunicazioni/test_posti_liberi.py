"""Test del job Posti liberi (SMS da AssoWeb) e delle sue pagine (README M6)."""

import datetime
import threading
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from gestionale.comunicazioni import posti_liberi
from gestionale.comunicazioni.views import TESTO_INIZIALE
from gestionale.core.models import Esecuzione, Evento, StatoEsecuzione, TipoEsecuzione
from gestionale.core.worker import esegui, prossima
from gestionale.portali import assoweb
from gestionale.portali.assoweb import ErrorePortale

TESTO = "Posti liberi:\n[giornata] (10 posti)"


def in_coda(utente, modalita="invio", filtro="AvisAltopianoMessaggio", giornata="2026-11-09"):
    return Esecuzione.objects.create(tipo=TipoEsecuzione.POSTI_LIBERI, utente=utente,
                                     parametri={"giornata": giornata, "testo": "Posti liberi", "modalita": modalita,
                                                "filtro": filtro})


def esegui_e_ricarica(esecuzione):
    esegui(prossima())
    esecuzione.refresh_from_db()
    return esecuzione


def domani():
    return timezone.localdate() + datetime.timedelta(days=1)


def dati_form(**altri):
    return {"giornata": domani().isoformat(), "testo": TESTO, "modalita": "invio", **altri}


# job

def test_invia_in_un_altro_thread_e_registra(cartelle, volontario, monkeypatch):
    chiamate = []

    def invia_sms_finto(username, password, filtro, testo, *, giornata, invia, avanza, **opzioni):
        chiamate.append((threading.get_ident(), filtro, testo, giornata, invia))
        avanza("Pagina 1: 42 donatori selezionati su 42")
        return 42

    monkeypatch.setattr(posti_liberi, "invia_sms", invia_sms_finto)
    esecuzione = esegui_e_ricarica(in_coda(volontario))

    assert esecuzione.stato == StatoEsecuzione.COMPLETATA, esecuzione.log
    [(thread, filtro, testo, giornata, invia)] = chiamate
    assert thread != threading.get_ident()
    assert (filtro, testo, giornata, invia) == ("AvisAltopianoMessaggio", "Posti liberi", datetime.date(2026, 11, 9),
                                                True)
    assert esecuzione.parametri["destinatari"] == 42
    for testo in ["SMS dei posti liberi del 09/11/2026 ai donatori del filtro AvisAltopianoMessaggio",
                  "Pagina 1: 42 donatori", "SMS inviato a 42 donatori"]:
        assert testo in esecuzione.log
    evento = Evento.objects.get(azione="posti_liberi")
    assert (evento.utente, evento.oggetto) == (volontario, f"esecuzione {esecuzione.pk}")
    assert evento.dettagli == {"giornata": "2026-11-09", "modalita": "invio", "filtro": "AvisAltopianoMessaggio",
                               "destinatari": 42}


def test_simulazione_non_invia(cartelle, volontario, monkeypatch):
    monkeypatch.setattr(posti_liberi, "invia_sms", lambda *args, invia, avanza, **opzioni: 0 if invia else 230)
    esecuzione = esegui_e_ricarica(in_coda(volontario, modalita="simulazione"))
    assert esecuzione.stato == StatoEsecuzione.COMPLETATA
    assert "Simulazione completata: l'SMS andrebbe a 230 donatori. Nessun SMS inviato" in esecuzione.log


def test_invio_fallito_con_screenshot(cartelle, volontario, monkeypatch):
    def invia_sms_finto(*args, avanza, **opzioni):
        raise ErrorePortale("Il filtro «AvisAltopianoMessaggio» non è tra i filtri", cartelle / "errori" / "a.png")

    monkeypatch.setattr(posti_liberi, "invia_sms", invia_sms_finto)
    esecuzione = esegui_e_ricarica(in_coda(volontario))

    assert esecuzione.stato == StatoEsecuzione.FALLITA
    assert "Errore: Il filtro «AvisAltopianoMessaggio» non è tra i filtri" in esecuzione.log
    assert "Screenshot della pagina: " in esecuzione.log
    assert "destinatari" not in esecuzione.parametri
    assert not Evento.objects.filter(azione="posti_liberi").exists()


def test_client_playwright_vero_nel_thread_del_worker(cartelle, volontario, monkeypatch, settings):
    """Il client vero sulla copia finta del portale, con il filtro di prova che trova un solo donatore."""
    monkeypatch.setattr(assoweb, "URL_LOGIN", Path(assoweb.__file__).with_name("assoweb_finto.html").as_uri())
    settings.ASSOWEB_USERNAME, settings.ASSOWEB_PASSWORD, settings.PORTALI_HEADLESS = "volontario", "giusta", True

    esecuzione = esegui_e_ricarica(in_coda(volontario, modalita="prova", filtro="test"))

    assert esecuzione.stato == StatoEsecuzione.COMPLETATA, esecuzione.log
    for testo in ["Login su AssoWeb effettuato", "Pagina 1: 1 donatori selezionati su 1",
                  "AssoWeb ha risposto «Messaggio inviato»", "SMS inviato a 1 donatori"]:
        assert testo in esecuzione.log
    assert esecuzione.parametri["destinatari"] == 1


# pagine

def test_pagina_con_il_testo_iniziale(client, volontario):
    client.force_login(volontario)
    risposta = client.get(reverse("posti_liberi"))
    assert risposta.status_code == 200
    assert risposta.context["form"]["testo"].value() == TESTO_INIZIALE
    assert "«AvisAltopianoMessaggio»" in risposta.text and "«test»" in risposta.text


def test_primo_invio_mostra_il_riepilogo_senza_mettere_in_coda(client, volontario):
    client.force_login(volontario)
    risposta = client.post(reverse("posti_liberi"), dati_form(giornata="2030-11-11", testo=TESTO.replace("\n", "\r\n")))

    assert risposta.status_code == 200
    assert not Esecuzione.objects.exists()
    assert risposta.context["dati"]["sms"] == "Posti liberi:\nLunedì 11 novembre (10 posti)"
    assert "Lunedì 11 novembre 2030" in risposta.text
    assert 'action="/posti-liberi/invia/"' in risposta.text
    # il testo originale torna nel form nascosto, con il segnaposto
    assert "[giornata] (10 posti)</textarea>" in risposta.text


def test_giornata_passata_rifiutata(client, volontario):
    client.force_login(volontario)
    ieri = timezone.localdate() - datetime.timedelta(days=1)
    risposta = client.post(reverse("posti_liberi"), dati_form(giornata=ieri.isoformat()))
    assert "La giornata è già passata." in risposta.text
    assert "dati" not in risposta.context


def test_modifica_torna_al_form(client, volontario):
    client.force_login(volontario)
    risposta = client.post(reverse("posti_liberi"), dati_form(modifica="1"))
    assert "dati" not in risposta.context
    assert risposta.context["form"]["testo"].value() == TESTO


def test_senza_la_conferma_non_parte(client, volontario):
    client.force_login(volontario)
    risposta = client.post(reverse("posti_liberi_invia"), dati_form())
    assert "Per inviare va spuntata la conferma." in risposta.text
    assert not Esecuzione.objects.exists()


def test_seconda_conferma_mette_in_coda(client, volontario):
    client.force_login(volontario)
    risposta = client.post(reverse("posti_liberi_invia"), dati_form(conferma="1"))

    esecuzione = Esecuzione.objects.get()
    assert risposta.url == reverse("esecuzione", args=[esecuzione.pk])
    assert (esecuzione.tipo, esecuzione.stato, esecuzione.utente) == (TipoEsecuzione.POSTI_LIBERI,
                                                                       StatoEsecuzione.IN_CODA, volontario)
    assert esecuzione.parametri["giornata"] == domani().isoformat()
    assert esecuzione.parametri["testo"].startswith("Posti liberi:\n") and "[giornata]" not in esecuzione.parametri["testo"]
    assert (esecuzione.parametri["modalita"], esecuzione.parametri["filtro"]) == ("invio", "AvisAltopianoMessaggio")


def test_prova_usa_il_filtro_di_prova(client, amministratore, settings):
    settings.ASSOWEB_FILTRO_PROVA = "solo_io"
    client.force_login(amministratore)
    client.post(reverse("posti_liberi_invia"), dati_form(modalita="prova", conferma="1"))
    assert Esecuzione.objects.get().parametri["filtro"] == "solo_io"


def test_un_avviso_alla_volta(client, volontario):
    client.force_login(volontario)
    client.post(reverse("posti_liberi_invia"), dati_form(conferma="1"))
    risposta = client.post(reverse("posti_liberi_invia"), dati_form(conferma="1"), follow=True)
    assert Esecuzione.objects.count() == 1
    assert "già un avviso dei posti liberi in coda o in corso" in risposta.text


def test_riepilogo_avvisa_se_la_giornata_ha_gia_un_avviso(client, volontario):
    client.force_login(volontario)
    precedente = in_coda(volontario, giornata=domani().isoformat())
    precedente.stato = StatoEsecuzione.COMPLETATA
    precedente.parametri["destinatari"] = 42
    precedente.save()
    # una simulazione per la stessa giornata non conta
    simulazione = in_coda(volontario, modalita="simulazione", giornata=domani().isoformat())
    simulazione.stato = StatoEsecuzione.COMPLETATA
    simulazione.save()

    risposta = client.post(reverse("posti_liberi"), dati_form())
    assert risposta.context["gia_inviati"] == [precedente]
    assert "è già partito un avviso" in risposta.text


def test_storico_nella_pagina(client, volontario):
    esecuzione = in_coda(volontario)
    esecuzione.stato = StatoEsecuzione.COMPLETATA
    esecuzione.parametri["destinatari"] = 42
    esecuzione.save()
    client.force_login(volontario)
    risposta = client.get(reverse("posti_liberi"))
    assert "lun 09/11/2026" in risposta.text.lower()
    assert '<td class="text-end">42</td>' in risposta.text


def test_pagine_solo_con_i_permessi(client, db):
    client.force_login(User.objects.create_user("senza_ruolo"))
    assert client.get(reverse("posti_liberi")).status_code == 403
    assert client.post(reverse("posti_liberi"), dati_form()).status_code == 403
    assert client.post(reverse("posti_liberi_invia"), dati_form(conferma="1")).status_code == 403
    assert not Esecuzione.objects.exists()
