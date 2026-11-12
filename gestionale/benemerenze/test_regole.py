"""Test del motore di calcolo delle benemerenze (regole.py): funzioni pure, senza DB."""

import datetime

import pytest

from gestionale.benemerenze import regole
from gestionale.benemerenze.regole import DatiSocio, STATUTO_2004, STATUTO_2021

data = datetime.date
RIF = data(2026, 10, 1)


def donazioni(anno, n, emazie=True, mese=6):
    """n donazioni nello stesso anno, a giorni diversi."""
    return [(data(anno, mese, giorno), emazie) for giorno in range(1, n + 1)]


def test_anni_compiuti():
    assert regole.anni_compiuti(data(2000, 12, 31), data(2025, 12, 31)) == 25
    assert regole.anni_compiuti(data(2000, 12, 31), data(2025, 12, 30)) == 24
    assert regole.anni_compiuti(None, data(2025, 12, 31)) == 0


def test_effettive_contano_pregresse_e_donazioni_fino_al_riferimento():
    dati = DatiSocio(pregresse_si=10, pregresse_aferesi=2,
                     donazioni=[(data(2026, 9, 30), True), (data(2026, 10, 1), True), (data(2026, 10, 2), True)])
    conteggio = regole.conta(dati, RIF)
    assert (conteggio.effettive, conteggio.valide, conteggio.ultima_donazione) == (14, 14, data(2026, 10, 1))


@pytest.mark.parametrize(("anno", "n", "valide"), [
    (2010, 5, 4),  # dal 17/05/2004 al 2020 al massimo 4 l'anno
    (2022, 7, 6),  # dal 2021 al massimo 6
])
def test_tetto_annuo(anno, n, valide):
    dati = DatiSocio(donazioni=donazioni(anno, n, emazie=False))
    assert regole.conta(dati, RIF).valide == valide
    assert regole.conta(dati, RIF).effettive == n


def test_nessun_tetto_prima_del_17_maggio_2004():
    dati = DatiSocio(donazioni=donazioni(2004, 5, emazie=False, mese=1) + donazioni(2004, 5, emazie=False, mese=9))
    # le 5 di gennaio contano tutte, le 5 di settembre arrivano al tetto di 4
    assert regole.conta(dati, RIF).valide == 9


def test_donne_x2_con_tetto_dopo_il_raddoppio():
    # 3 sangue intero nel 2010 valgono 6, ridotte dal tetto a 4; 1 plasma non raddoppia
    dati = DatiSocio(sesso="F", data_nascita=data(1980, 1, 1),
                     donazioni=donazioni(2010, 3) + [(data(2011, 3, 1), False)])
    assert regole.conta(dati, RIF).valide == 4 + 1
    assert regole.conta(dati, RIF).effettive == 4


def test_donne_x2_fino_a_50_anni():
    dati = DatiSocio(sesso="F", data_nascita=data(1975, 6, 15),
                     donazioni=[(data(2025, 6, 14), True), (data(2025, 6, 15), True)])
    # il giorno dei 50 anni la donazione vale già una sola
    assert regole.conta(dati, RIF).valide == 3


def test_donne_x2_sulle_pregresse_solo_dopo_il_2004():
    dati = DatiSocio(sesso="F", pregresse_si=10, pregresse_dopo_2004=3)
    assert regole.conta(dati, RIF).valide == 13
    assert regole.conta(dati, RIF).effettive == 10
    assert regole.conta(DatiSocio(sesso="M", pregresse_si=10, pregresse_dopo_2004=3), RIF).valide == 10


def test_statuto_2004_senza_raddoppio_ne_tetto():
    # come AssoAVIS fino al 2019: valide = effettive
    dati = DatiSocio(sesso="F", data_nascita=data(1980, 1, 1), donazioni=donazioni(2010, 5))
    assert regole.statuto(data(2019, 10, 2)) is STATUTO_2004
    assert regole.conta(dati, data(2019, 10, 2), STATUTO_2004).valide == 5
    assert regole.statuto(data(2022, 9, 5)) is STATUTO_2021


@pytest.mark.parametrize(("valide", "anni", "maturata"), [
    (8, 0, True),  # criterio B: 8 donazioni
    (6, 3, True),  # criterio A: 6 donazioni e 3 anni
    (6, 2, False),
    (5, 10, False),
])
def test_criteri_del_rame(valide, anni, maturata):
    assert STATUTO_2021.regole[0].maturata(valide, anni) is maturata


def test_maturate_insieme_e_gia_possedute():
    dati = DatiSocio(pregresse_si=16)
    assert regole.maturate(dati, RIF)[1] == [1, 2]
    dati.possedute = {1}
    assert regole.maturate(dati, RIF)[1] == [2]
    # chi ha già l'Oro ha implicitamente le inferiori; le benemerenze dei collaboratori non contano
    assert regole.maturate(DatiSocio(pregresse_si=50, possedute={4}), RIF)[1] == []
    assert regole.maturate(DatiSocio(pregresse_si=8, possedute={7, 8}), RIF)[1] == [1]


def test_anni_di_iscrizione():
    dati = DatiSocio(data_iscrizione=data(2023, 10, 2), pregresse_si=6)
    assert regole.maturate(dati, RIF)[1] == []
    dati.data_iscrizione = data(2023, 10, 1)
    assert regole.maturate(dati, RIF)[1] == [1]


def test_oro_con_diamante_solo_con_donazioni_dopo_il_2004():
    tutte = {1, 2, 3, 4, 5, 6}
    assert regole.maturate(DatiSocio(pregresse_si=120, possedute=tutte), RIF)[1] == []
    dati = DatiSocio(pregresse_si=119, possedute=tutte, donazioni=[(data(2025, 1, 1), True)])
    assert regole.maturate(dati, RIF)[1] == [regole.ORO_CON_DIAMANTE]


def test_prossima_benemerenza():
    dati = DatiSocio(data_iscrizione=data(2024, 3, 1), pregresse_si=10, possedute={1})
    prossima = regole.prossima(dati, RIF)
    # Argento: 16 donazioni, oppure 12 con 5 anni di iscrizione
    assert prossima.regola.benemerenza == 2
    assert (prossima.mancano, prossima.mancano_con_anni, prossima.anni_dal) == (6, 2, data(2029, 3, 1))
    # chi ha tutto non ha una prossima
    assert regole.prossima(DatiSocio(possedute={10}), RIF) is None


@pytest.mark.parametrize(("stato", "no_benemerenze", "cessazione", "premiabile"), [
    ("donatore", False, None, True),
    ("donatore", True, None, False),
    ("collaboratore", False, None, False),  # si premiano solo i donatori
    ("ex_socio", False, data(2025, 12, 31), True),
    ("ex_socio", False, data(2025, 11, 3), False),
    ("ex_socio", False, None, False),
    ("", False, None, False),
])
def test_premiabile(stato, no_benemerenze, cessazione, premiabile):
    assert regole.premiabile(stato, no_benemerenze, cessazione, data(2025, 11, 3)) is premiabile


def test_ex_soci_esclusi_senza_data():
    assert regole.premiabile("ex_socio", False, data(2025, 12, 31), None) is False
