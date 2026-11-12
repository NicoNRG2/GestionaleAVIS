import datetime

import pytest
from django.core.management import CommandError, call_command

from gestionale.benemerenze.models import Benemerenza, BenemerenzaAssegnata
from gestionale.core.models import Evento, Sezione
from gestionale.importazioni.assoweb import SEGNAPOSTO_ISCRIZIONE, importa
from gestionale.importazioni.test_assoweb import riga_donatore
from gestionale.importazioni.unione import UnioneError, unisci, unisci_coppie
from gestionale.soci.models import Donazione, Fonte, Idoneita, Socio, StatoSocio, TipoDonazione

OGGI = datetime.date(2026, 10, 29)
data = datetime.date
CF_PORTALE = "RSSNNA80A41A001X"


@pytest.fixture
def coppia(db):
    """Storico con CF e data di nascita sbagliati e il doppione che l'import AssoWeb ha creato per lo stesso socio."""
    sezione = Sezione.objects.get(codice="1529")
    si = TipoDonazione.objects.get(codice="SI")
    storico = Socio.objects.create(
        sezione=sezione, codice_interno_assoavis="0000011529", codice_fiscale="RSSNNA80A01A001Y", cognome="ROSSI",
        nome="ANNA", sesso="F", data_nascita=data(1980, 1, 1), indirizzo_residenza="VIA ROMA 1",
        comune_residenza="ASIAGO", email="vecchia@esempio.it", data_iscrizione=SEGNAPOSTO_ISCRIZIONE,
        stato=StatoSocio.EX_SOCIO, data_cessazione=data(2024, 12, 31), note="nota dello storico")
    Donazione.objects.create(socio=storico, data=data(2005, 3, 1), tipo=si, fonte=Fonte.ASSOAVIS)
    Donazione.objects.create(socio=storico, data=data(2022, 3, 1), tipo=si, fonte=Fonte.ASSOAVIS)
    doppione = Socio.objects.create(
        sezione=sezione, codice_fiscale=CF_PORTALE, cognome="ROSSI", nome="ANNA", sesso="F",
        data_nascita=data(1980, 1, 21), comune_residenza="GALLIO", email="nuova@esempio.it", gruppo_sanguigno="A",
        idoneita=Idoneita.IDONEO, data_iscrizione=data(2026, 2, 1), data_iscrizione_assoweb=data(2004, 6, 1),
        tessera="152900042", stato=StatoSocio.DONATORE)
    Donazione.objects.create(socio=doppione, data=data(2026, 2, 1), tipo=si, fonte=Fonte.ASSOWEB)
    return storico, doppione


def test_unisci_porta_tutto_sullo_storico(coppia):
    storico, doppione = coppia
    risultato = unisci(storico, doppione, OGGI)

    assert not Socio.objects.filter(pk=doppione.pk).exists()
    socio = Socio.objects.get(pk=storico.pk)
    assert socio.donazioni.count() == 3
    # AssoWeb è la fonte master dell'anagrafica e dei contatti; l'indirizzo, che il portale non ha, resta
    assert (socio.codice_fiscale, socio.data_nascita, socio.email) == (CF_PORTALE, data(1980, 1, 21), "nuova@esempio.it")
    assert (socio.comune_residenza, socio.indirizzo_residenza, socio.gruppo_sanguigno) == ("GALLIO", "VIA ROMA 1", "A")
    assert (socio.codice_interno_assoavis, socio.tessera) == ("0000011529", "152900042")
    # la data di AssoWeb sostituisce il segnaposto, perché è prima della prima donazione
    assert (socio.data_iscrizione, socio.data_iscrizione_assoweb) == (data(2004, 6, 1), data(2004, 6, 1))
    # i dati di AssoAVIS restano nelle note, per correggerli anche lì
    assert socio.note.startswith("nota dello storico\n29/10/2026: unito alla scheda creata da AssoWeb")
    assert "CF RSSNNA80A01A001Y, nascita 01/01/1980" in socio.note
    assert risultato["donazioni spostate"] == 1 and risultato["donazioni già presenti"] == 0
    assert {"codice_fiscale", "data_nascita", "email", "comune_residenza", "tessera", "data_iscrizione"} <= \
           set(risultato["campi"])
    assert "cognome" not in risultato["campi"]


def test_nota_con_il_nome_di_assoavis(coppia):
    # stessa data di nascita, nome diverso: lo storico aveva solo il primo nome
    storico, doppione = coppia
    Socio.objects.filter(pk=doppione.pk).update(nome="ANNA MARIA", data_nascita=storico.data_nascita)
    unisci(storico, Socio.objects.get(pk=doppione.pk), OGGI)
    assert "In AssoAVIS: nome ANNA, CF RSSNNA80A01A001Y, nascita 01/01/1980." in Socio.objects.get().note


def test_stessa_donazione_sulle_due_schede(coppia):
    storico, doppione = coppia
    Donazione.objects.create(socio=doppione, data=data(2022, 3, 1), tipo=TipoDonazione.objects.get(codice="PL"),
                             fonte=Fonte.ASSOWEB)
    risultato = unisci(storico, doppione, OGGI)
    assert (risultato["donazioni spostate"], risultato["donazioni già presenti"]) == (1, 1)
    # resta quella dello storico
    assert Donazione.objects.get(socio=storico, data=data(2022, 3, 1)).fonte == Fonte.ASSOAVIS


def test_stato_ricalcolato_dopo_le_unioni(coppia):
    storico, doppione = coppia
    _, passaggi = unisci_coppie([(storico.pk, doppione.pk)], OGGI)
    socio = Socio.objects.get(pk=storico.pk)
    # la donazione del 2026 è dopo la cessazione: l'ex socio torna donatore
    assert (socio.stato, socio.data_cessazione) == (StatoSocio.DONATORE, None)
    assert passaggi[(StatoSocio.EX_SOCIO, StatoSocio.DONATORE)] == 1


def test_il_prossimo_import_non_ricrea_il_doppione(coppia):
    storico, doppione = coppia
    unisci(storico, doppione, OGGI)
    report = importa([riga_donatore(CF_PORTALE, "ROSSI", "ANNA", "1980-01-21")], [], OGGI)
    assert report.conteggi["soci nuovi"] == 0
    assert Socio.objects.filter(cognome="ROSSI").count() == 1


def test_errori(coppia):
    storico, doppione = coppia
    with pytest.raises(UnioneError, match="stessa scheda"):
        unisci(storico, storico, OGGI)
    # lo storico non è un doppione: ha il codice AssoAVIS
    with pytest.raises(UnioneError, match="codice AssoAVIS"):
        unisci(doppione, storico, OGGI)
    with pytest.raises(UnioneError, match="più di una coppia"):
        unisci_coppie([(storico.pk, doppione.pk), (storico.pk, doppione.pk)], OGGI)
    with pytest.raises(UnioneError, match="inesistenti: 999999"):
        unisci_coppie([(storico.pk, 999999)], OGGI)
    BenemerenzaAssegnata.objects.create(socio=doppione, benemerenza=Benemerenza.objects.first(), fonte=Fonte.GESTIONALE)
    with pytest.raises(UnioneError, match="1 benemerenze assegnate, da sistemare a mano"):
        unisci(storico, doppione, OGGI)


def test_comando(coppia):
    storico, doppione = coppia
    call_command("unisci_soci", f"{storico.pk}:{doppione.pk}", "--prova")
    assert Socio.objects.filter(pk=doppione.pk).exists() and not Evento.objects.filter(azione="unione_soci").exists()

    call_command("unisci_soci", f"{storico.pk}:{doppione.pk}")
    assert not Socio.objects.filter(pk=doppione.pk).exists()
    evento = Evento.objects.get(azione="unione_soci")
    assert evento.dettagli["coppie"] == [f"socio {storico.pk} <- socio {doppione.pk}"]

    with pytest.raises(CommandError, match="formato"):
        call_command("unisci_soci", "1-2")
    with pytest.raises(CommandError, match="Nessuna modifica salvata"):
        call_command("unisci_soci", f"{storico.pk}:{storico.pk}")
