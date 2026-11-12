import datetime

import pytest
from django.db import IntegrityError

from gestionale.core.models import Sezione
from gestionale.soci.models import CausaCessazione, Donazione, Fonte, Socio, StatoSocio, TipoDonazione
from gestionale.soci.stati import aggiorna_stati, nuovo_stato

data = datetime.date


@pytest.fixture
def sezione(db):
    return Sezione.objects.get(codice="1529")


def test_chiavi_vuote_diventano_null(sezione):
    # due soci senza codice fiscale non violano il vincolo di unicità
    a = Socio.objects.create(sezione=sezione, cognome="Rossi", nome="Anna", codice_fiscale="  ")
    b = Socio.objects.create(sezione=sezione, cognome="Bianchi", nome="Luca", codice_fiscale="")
    assert a.codice_fiscale is None and b.codice_fiscale is None


def test_codice_fiscale_maiuscolo_e_univoco(sezione):
    socio = Socio.objects.create(sezione=sezione, cognome="Rossi", nome="Anna", codice_fiscale=" rssnna80a41a001x ")
    assert socio.codice_fiscale == "RSSNNA80A41A001X"
    with pytest.raises(IntegrityError):
        Socio.objects.create(sezione=sezione, cognome="Rossi", nome="Anna", codice_fiscale="RSSNNA80A41A001X")


def test_stato_vuoto_finche_non_e_socio(sezione):
    socio = Socio.objects.create(sezione=sezione, cognome="Rossi", nome="Anna")
    assert socio.stato == ""
    socio.stato = StatoSocio.EX_SOCIO
    socio.save()
    assert Socio.objects.get().get_stato_display() == "Ex socio"


def test_una_donazione_al_giorno(sezione):
    socio = Socio.objects.create(sezione=sezione, cognome="Rossi", nome="Anna")
    si = TipoDonazione.objects.get(codice="SI")
    Donazione.objects.create(socio=socio, data=datetime.date(2026, 2, 1), tipo=si, fonte=Fonte.ASSOWEB)
    with pytest.raises(IntegrityError):
        Donazione.objects.create(socio=socio, data=datetime.date(2026, 2, 1), tipo=si, fonte=Fonte.ASSOAVIS)


def test_tipi_donazione(db):
    assert TipoDonazione.objects.count() == 11
    assert set(TipoDonazione.objects.filter(contiene_emazie=True).values_list("codice", flat=True)) == {"SI", "EA", "EP"}
    assert not TipoDonazione.objects.get(codice="XX").e_donazione


OGGI = data(2026, 10, 29)
DONATORE, EX, COLLABORATORE = StatoSocio.DONATORE, StatoSocio.EX_SOCIO, StatoSocio.COLLABORATORE


@pytest.mark.parametrize("stato, ultima, cessazione, oggi, atteso", [
    # nell'anno N si resta donatori con l'ultima donazione dal N-2 in poi
    (DONATORE, data(2024, 1, 1), None, OGGI, None),
    (DONATORE, data(2023, 12, 31), None, OGGI, (EX, data(2025, 12, 31))),
    # al primo aggiornamento del nuovo anno cessa chi ha donato l'ultima volta nel N-3, al 31/12 dell'anno prima
    (DONATORE, data(2024, 6, 1), None, data(2027, 1, 2), (EX, data(2026, 12, 31))),
    (DONATORE, None, None, OGGI, (EX, data(2025, 12, 31))),
    # chi dona entra o rientra
    ("", data(2026, 9, 1), None, OGGI, (DONATORE, None)),
    (EX, data(2024, 3, 1), None, OGGI, (DONATORE, None)),
    # una cessazione decisa a mano si supera solo con una donazione successiva
    (EX, data(2026, 5, 1), data(2026, 7, 3), OGGI, None),
    (EX, data(2026, 9, 1), data(2026, 7, 3), OGGI, (DONATORE, None)),
    # restano come sono
    (EX, data(2023, 5, 1), None, OGGI, None),
    ("", None, None, OGGI, None),
    (COLLABORATORE, None, None, OGGI, None),
])
def test_nuovo_stato(stato, ultima, cessazione, oggi, atteso):
    campi = nuovo_stato(stato, ultima, cessazione, oggi)
    assert (campi and (campi["stato"], campi["data_cessazione"])) == atteso
    if campi and campi["stato"] == EX:
        assert campi["causa_cessazione"] == CausaCessazione.CESSAZIONE


@pytest.mark.parametrize("stato, ultima, cessazione, atteso", [
    # chi fa parte dei collaboratori non cessa mai: donatore finché dona, poi collaboratore
    (DONATORE, data(2024, 1, 1), None, None),
    (DONATORE, data(2023, 12, 31), None, COLLABORATORE),
    (COLLABORATORE, None, None, None),
    (COLLABORATORE, data(2026, 3, 1), None, DONATORE),
    (EX, data(2020, 1, 1), data(2022, 12, 31), COLLABORATORE),
    ("", None, None, COLLABORATORE),
])
def test_nuovo_stato_dei_collaboratori(stato, ultima, cessazione, atteso):
    campi = nuovo_stato(stato, ultima, cessazione, OGGI, collaboratore=True)
    assert (campi and campi["stato"]) == atteso
    if campi:
        assert (campi["data_cessazione"], campi["causa_cessazione"]) == (None, None)


def test_aggiorna_stati(sezione):
    si, xx = TipoDonazione.objects.get(codice="SI"), TipoDonazione.objects.get(codice="XX")

    def socio(stato, *donazioni, tipo=si, **campi):
        s = Socio.objects.create(sezione=sezione, cognome="Rossi", nome="Anna", stato=stato, **campi)
        for d in donazioni:
            Donazione.objects.create(socio=s, data=d, tipo=tipo, fonte=Fonte.ASSOWEB)
        return s

    nuovo = socio("", data(2026, 3, 1), data(2026, 9, 1))
    riammesso = socio(EX, data(2025, 4, 1), data_iscrizione=data(2001, 1, 1), data_cessazione=data(2024, 12, 31),
                      causa_cessazione=CausaCessazione.CESSAZIONE)
    cessato = socio(DONATORE, data(2023, 5, 1))
    presentato = socio("", data(2026, 5, 1), tipo=xx)
    collaboratore = socio(COLLABORATORE, collaboratore=True)
    collaboratore_cessato = socio(DONATORE, data(2023, 5, 1), collaboratore=True)
    collaboratore_che_dona = socio(COLLABORATORE, data(2026, 4, 1), collaboratore=True)

    passaggi = aggiorna_stati(OGGI)

    assert passaggi == {("", DONATORE): 1, (EX, DONATORE): 1, (DONATORE, EX): 1, (DONATORE, COLLABORATORE): 1,
                        (COLLABORATORE, DONATORE): 1}
    collaboratore_cessato.refresh_from_db()
    assert (collaboratore_cessato.stato, collaboratore_cessato.data_cessazione) == (COLLABORATORE, None)
    collaboratore_che_dona.refresh_from_db()
    assert (collaboratore_che_dona.stato, collaboratore_che_dona.data_iscrizione) == (DONATORE, data(2026, 4, 1))
    nuovo.refresh_from_db()
    assert (nuovo.stato, nuovo.data_iscrizione) == (DONATORE, data(2026, 3, 1))
    riammesso.refresh_from_db()
    assert (riammesso.stato, riammesso.data_iscrizione, riammesso.data_cessazione) == (DONATORE, data(2001, 1, 1), None)
    cessato.refresh_from_db()
    assert (cessato.stato, cessato.data_cessazione) == (EX, data(2025, 12, 31))
    # la sola presentazione non è una donazione
    presentato.refresh_from_db()
    assert presentato.stato == ""
    collaboratore.refresh_from_db()
    assert collaboratore.stato == COLLABORATORE
    # una seconda esecuzione non cambia nulla
    assert aggiorna_stati(OGGI) == {}
