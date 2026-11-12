"""Test delle statistiche (README M3)."""

import datetime
from io import BytesIO

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from openpyxl import load_workbook

from gestionale.core.models import Evento, Sezione
from gestionale.soci.models import CausaCessazione, Donazione, Fonte, Socio, StatoSocio, TipoDonazione

from . import calcolo

data = datetime.date
OGGI = data(2026, 10, 30)


@pytest.fixture
def soci(db):
    """Soci con donazioni in anni diversi; lo stato è quello che darebbe aggiorna_stati il 30/10/2026."""
    sezione = Sezione.objects.get(codice="1529")
    tipi = {t.codice: t for t in TipoDonazione.objects.all()}

    def socio(nome, stato, donazioni=(), **campi):
        s = Socio.objects.create(sezione=sezione, cognome=nome, nome="X", stato=stato, **campi)
        for giorno, tipo in donazioni:
            Donazione.objects.create(socio=s, data=giorno, tipo=tipi[tipo], fonte=Fonte.ASSOAVIS)
        return s

    return {
        # dona dal 2023: socio a fine 2023, 2024, 2025 e oggi
        "anna": socio("ANNA", StatoSocio.DONATORE, [(data(2023, 3, 10), "SI"), (data(2024, 3, 10), "PL")],
                      sesso="F", data_nascita=data(2000, 12, 31), gruppo_sanguigno="A", rh="+"),
        # ultima donazione nel 2023: cessa il 31/12/2025, quindi non è socio a fine 2025
        "bruno": socio("BRUNO", StatoSocio.EX_SOCIO, [(data(2023, 5, 1), "SI")], sesso="M",
                       data_nascita=data(1960, 1, 1), gruppo_sanguigno="0", rh="-",
                       data_cessazione=data(2025, 12, 31), causa_cessazione=CausaCessazione.CESSAZIONE),
        # dimissioni segnate a mano nel 2025: non è socio a fine 2025 anche se ha donato
        "carla": socio("CARLA", StatoSocio.EX_SOCIO, [(data(2024, 2, 1), "SI"), (data(2025, 2, 1), "SI")],
                       sesso="F", data_cessazione=data(2025, 6, 1), causa_cessazione=CausaCessazione.DIMISSIONI),
        # prima donazione nel 2025: nuovo socio; la presentazione del 2024 non è una donazione
        "dario": socio("DARIO", StatoSocio.DONATORE, [(data(2024, 11, 5), "XX"), (data(2025, 1, 20), "SI"),
                                                      (data(2026, 4, 2), "SI")],
                       sesso="M", data_nascita=data(1999, 12, 31), gruppo_sanguigno="AB", rh="+"),
        # cessato nel 2025 (ultima donazione 2023) e riammesso nel 2026
        "elena": socio("ELENA", StatoSocio.DONATORE, [(data(2023, 7, 7), "SI"), (data(2026, 1, 15), "SI")],
                       sesso="F", data_nascita=data(1990, 6, 15)),
        "franco": socio("FRANCO", StatoSocio.COLLABORATORE, sesso="M"),
        "gina": socio("GINA", "", sesso="F"),
    }


def nomi(queryset):
    return sorted(queryset.values_list("cognome", flat=True))


@pytest.mark.parametrize(("anno", "attesi"), [
    (2023, ["ANNA", "BRUNO", "ELENA"]),
    (2024, ["ANNA", "BRUNO", "CARLA", "ELENA"]),
    # BRUNO cessa il 31/12/2025, CARLA si è dimessa, ELENA non dona dal 2023
    (2025, ["ANNA", "DARIO"]),
    # l'anno in corso è lo stato attuale
    (2026, ["ANNA", "DARIO", "ELENA"]),
])
def test_soci_donatori_a_fine_anno(soci, anno, attesi):
    assert nomi(calcolo.soci_donatori(anno, OGGI)) == attesi


def test_fascia_eta():
    assert calcolo.fascia_eta(18) == "18/25"
    assert calcolo.fascia_eta(17) == calcolo.ETA_NON_INDICATA


def test_fascia_eta_e_sesso_al_31_12(soci):
    tabella = calcolo.per_fascia_e_sesso(calcolo.soci_donatori(2024, OGGI), data(2024, 12, 31))
    righe = {riga[0]: riga[1:] for riga in tabella.righe}
    # al 31/12/2024 ANNA ha 24 anni, ELENA 34, BRUNO 64; CARLA non ha la data di nascita
    assert righe["18/25"] == [0, 1, 1]
    assert righe["26/35"] == [0, 1, 1]
    assert righe["56/65"] == [1, 0, 1]
    assert righe[calcolo.ETA_NON_INDICATA] == [0, 1, 1]
    assert tabella.totale == ["Totale", 1, 3, 4]


def test_fascia_eta_non_indicata_solo_se_serve(soci):
    tabella = calcolo.per_fascia_e_sesso(calcolo.soci_donatori(2025, OGGI), data(2025, 12, 31))
    # DARIO compie 26 anni il 31/12/2025
    assert [riga[0] for riga in tabella.righe] == ["18/25", "26/35", "36/45", "46/55", "56/65", "Oltre 65"]
    assert {riga[0]: riga[3] for riga in tabella.righe}["26/35"] == 1


def test_gruppi_sanguigni(soci):
    tabella = calcolo.per_gruppo_sanguigno(calcolo.soci_donatori(2024, OGGI))
    # CARLA ed ELENA senza gruppo
    assert tabella.righe == [["0", 0, 1, 1, 25.0], ["A", 1, 0, 1, 25.0], ["B", 0, 0, 0, 0.0], ["AB", 0, 0, 0, 0.0],
                             ["Non indicato", 0, 0, 2, 50.0]]
    assert tabella.totale == ["Totale", 1, 1, 4, 100.0]


def test_donazioni_per_tipo_e_per_mese(soci):
    tipi = calcolo.donazioni_per_tipo(2024)
    # la "Solo presentazione" di DARIO non conta
    assert tipi.righe == [["Sangue intero", 1, 50.0], ["Plasmaferesi", 1, 50.0]]
    assert tipi.totale == ["Totale", 2, 100.0]

    mesi = calcolo.donazioni_per_mese(2024, OGGI)
    assert len(mesi.righe) == 12
    assert mesi.righe[1] == ["Febbraio", 1, 0, 1] and mesi.righe[2] == ["Marzo", 0, 1, 1]
    assert mesi.totale == ["Totale", 1, 1, 2]
    # nell'anno in corso solo fino al mese attuale
    assert [riga[0] for riga in calcolo.donazioni_per_mese(2026, OGGI).righe][-1] == "Ottobre"


def test_movimento_dei_soci(soci):
    movimenti = calcolo.movimenti(range(2025, 2027), OGGI)
    m2025 = movimenti[2025]
    assert (m2025.nuovi, m2025.riammessi) == ({soci["dario"].pk}, set())
    assert m2025.cessati == {soci["bruno"].pk, soci["carla"].pk, soci["elena"].pk}
    m2026 = movimenti[2026]
    assert (m2026.nuovi, m2026.riammessi, m2026.cessati) == (set(), {soci["elena"].pk}, set())

    tabella = calcolo.movimento_soci(m2025, 2025, OGGI)
    # 4 + 1 + 0 - 3 = 2
    assert tabella.righe == [["Soci al 31/12/2024", 1, 3, 4], ["+ nuovi soci", 1, 0, 1], ["+ riammessi", 0, 0, 0],
                             ["− cessati", 1, 2, 3]]
    assert tabella.totale == ["Soci al 31/12/2025", 1, 1, 2]


def test_andamento_parte_dal_primo_anno_con_donazioni(soci):
    tabella = calcolo.statistiche(2026, OGGI)["tabelle"]["andamento"]
    assert [riga[0] for riga in tabella.righe] == [2026, 2025, 2024, 2023]
    # anno, donazioni, sangue intero, aferesi, soci donatori, nuovi, riammessi, cessati
    assert tabella.righe[2] == [2024, 2, 1, 1, 4, 1, 0, 0]
    assert tabella.righe[3] == [2023, 3, 3, 0, 3, 3, 0, 0]


def test_soci_per_stato(soci):
    assert calcolo.soci_per_stato().righe == [["Socio donatore", 3], ["Socio collaboratore", 1], ["Ex socio", 2],
                                              ["Non soci", 1]]


def test_righe_con_barre():
    tabella = calcolo.Tabella("t", "t", ["Mese", "Totale"], [["Gennaio", 5], ["Febbraio", 10], ["Marzo", 0]], barra=1)
    assert [larghezza for _, larghezza in tabella.righe_con_barre] == [50, 100, 0]


@pytest.fixture
def oggi(monkeypatch):
    """Le viste prendono la data da timezone.localdate: nei test è il 30/10/2026."""
    monkeypatch.setattr("django.utils.timezone.localdate", lambda: OGGI)


def test_pagina_delle_statistiche(client, volontario, soci, oggi):
    client.force_login(volontario)
    risposta = client.get(reverse("statistiche"), {"anno": 2025})
    assert risposta.status_code == 200
    assert risposta.context["riepilogo"] == {"soci_donatori": 2, "donazioni": 2, "nuovi": 1, "cessati": 3}
    for testo in ["<html", "Soci donatori per fascia d&#x27;età e sesso", "Soci al 31/12/2025", "Andamento 2023–2025"]:
        assert testo in risposta.text


def test_anno_htmx_restituisce_solo_le_statistiche(client, volontario, soci, oggi):
    client.force_login(volontario)
    risposta = client.get(reverse("statistiche"), {"anno": 2024}, headers={"HX-Request": "true"})
    assert "<html" not in risposta.text and "Soci al 31/12/2024" in risposta.text


@pytest.mark.parametrize("anno", ["", "1900", "2027", "ieri"])
def test_anno_non_valido_mostra_l_anno_in_corso(client, volontario, soci, oggi, anno):
    client.force_login(volontario)
    assert client.get(reverse("statistiche"), {"anno": anno}).context["anno"] == 2026


def test_export_excel(client, volontario, soci, oggi):
    client.force_login(volontario)
    risposta = client.get(reverse("statistiche_esporta"), {"anno": 2025})
    assert risposta["Content-Disposition"] == 'attachment; filename="statistiche_2025.xlsx"'

    libro = load_workbook(BytesIO(risposta.content))
    assert libro.sheetnames == ["Fascia d'età e sesso", "Gruppi sanguigni", "Donazioni per tipo", "Donazioni per mese",
                                "Movimento soci", "Soci per stato", "Andamento"]
    foglio = libro["Movimento soci"]
    righe = [r for r in foglio.iter_rows(values_only=True)]
    assert righe[0][0] == "Movimento dei soci donatori"
    assert righe[1][0] == "AVIS Comunale Altopiano di Asiago, anno 2025: soci donatori al 31/12/2025"
    assert ("Soci al 31/12/2025", 1, 1, 2) in righe

    evento = Evento.objects.get(azione="export_statistiche")
    assert (evento.utente, evento.oggetto) == (volontario, "statistiche 2025")


def test_statistiche_solo_con_il_permesso(client, db):
    client.force_login(User.objects.create_user("senza_ruolo"))
    assert client.get(reverse("statistiche")).status_code == 403
    assert client.get(reverse("statistiche_esporta")).status_code == 403
    assert not Evento.objects.filter(azione="export_statistiche").exists()
