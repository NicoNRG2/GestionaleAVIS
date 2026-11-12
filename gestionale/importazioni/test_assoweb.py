import datetime

import pytest

from gestionale.core.models import Sezione
from gestionale.importazioni.assoavis import ImportazioneError
from gestionale.importazioni.assoweb import (SEGNAPOSTO_ISCRIZIONE, campi_da_riga, importa, iscrizione_da_assoweb,
                                             leggi_csv, tessera_da_assoweb)
from gestionale.soci.models import Donazione, Fonte, Idoneita, Socio, StatoSocio, TipoDonazione

OGGI = datetime.date(2026, 10, 29)
data = datetime.date
INTESTAZIONE_DONATORI = (
    "CODICEREGIONALE;COGNOME;NOME;SESSO;DATA_NASCITA;LUOGO_NASCITA;PROVINCIA_NASCITA;SIGLAP_NASCIATA;CODICEFISCALE;"
    "TESSERA_ASSO;DATA_ISCRIZIONE;ASSOCIAZIONE;SEZIONE;TELEFONO_CELLULARE;TEL_ABITAZIONE;CAP_DOMICILIO;LUOGO_DOMICILO;"
    "LUOGO_RESIDENZA;CAP_RESIDENZA;PROVINCIA_RESIDENZA;SIGLAP_RESIDENZA;E_MAIL;GR_SANG;FATTORE;DATA_ULTIMA_DONAZIONE;"
    "ACCESSI;IDONEITA;DATA_INIZIO;DATA_FINE;STATO;FENRH;KELL").split(";")
ROSSI, BIANCHI, VERDI, GIALLI, NERI = ("RSSNNA80A41A001X", "BNCLCU70B02A001X", "VRDGPP60A01A001X",
                                       "GLLPLA95C03A001X", "NRIMRA90A41A001X")


def riga_donatore(cf, cognome, nome, nascita, **valori):
    riga = dict.fromkeys(INTESTAZIONE_DONATORI, "")
    riga.update(CODICEFISCALE=cf, COGNOME=cognome, NOME=nome, DATA_NASCITA=nascita, SESSO="M", STATO="Periodico",
                SEZIONE="AVIS Comunale Altopiano di Asiago", CODICEREGIONALE="1234567", IDONEITA="Idoneo")
    riga.update(valori)
    return riga


def riga_donazione(cf, data_don, tipo="SI", tot_don="1"):
    return {"CODICE_FISCALE": cf, "DATA_DON": data_don, "TIPO": tipo, "CENTRO_PRELIEVO": "Centro di Raccolta di Asiago",
            "TOT_DON": tot_don}


@pytest.fixture
def storico(db):
    """Soci come li lascia l'import dello storico AssoAVIS."""
    sezione = Sezione.objects.get(codice="1529")
    si = TipoDonazione.objects.get(codice="SI")

    def socio(cf, cognome, nome, nascita, stato=StatoSocio.EX_SOCIO, donazione=None, **campi):
        s = Socio.objects.create(sezione=sezione, codice_fiscale=cf, cognome=cognome, nome=nome, data_nascita=nascita,
                                 stato=stato, **campi)
        if donazione:
            Donazione.objects.create(socio=s, data=donazione, tipo=si, fonte=Fonte.ASSOAVIS)
        return s

    return {
        "rossi": socio(ROSSI, "ROSSI", "ANNA", data(1980, 1, 1), donazione=data(2023, 5, 1), comune_residenza="ASIAGO",
                       cap_residenza="36012", indirizzo_residenza="VIA ROMA 1", email="anna@esempio.it",
                       data_iscrizione=SEGNAPOSTO_ISCRIZIONE, tessera="152900042"),
        "bianchi": socio(None, "BIANCHI", "LUCA", data(1970, 2, 2)),
        # CF sbagliato nello storico
        "verdi": socio("VRDGPP60A01A001Z", "VERDI", "GIUSEPPE", data(1960, 1, 1), tessera="152900100"),
        "neri": socio(NERI, "NERI", "MARIA", data(1990, 1, 1), stato=StatoSocio.DONATORE, donazione=data(2025, 3, 1)),
    }


def csv_assoweb():
    donatori = [
        riga_donatore(ROSSI, "ROSSI", "ANNA", "1980-01-01", LUOGO_RESIDENZA="GALLIO", CAP_RESIDENZA="36032",
                      GR_SANG="A", FATTORE="+", DATA_INIZIO="2026-01-10", DATA_ISCRIZIONE="1999-04-10",
                      TESSERA_ASSO="42"),
        # iscrizione dopo la prima donazione (2026-04-01): non si usa
        riga_donatore(BIANCHI, "BIANCHI", "LUCA", "1970-02-02", DATA_ISCRIZIONE="2026-05-01", TESSERA_ASSO="77"),
        # iscrizione a 10 anni: refuso del portale; tessera diversa da quella dello storico
        riga_donatore(VERDI, "VERDI", "GIUSEPPE", "1960-01-01", IDONEITA="Temporaneamente Sospeso",
                      DATA_ISCRIZIONE="1970-01-01", TESSERA_ASSO="101"),
        # nuova socia: l'iscrizione viene da AssoWeb; la tessera è anche di Rossi
        riga_donatore(GIALLI, "GIALLI", "PAOLA", "1995-03-03", SESSO="F", STATO="Aspirante",
                      DATA_ISCRIZIONE="2025-11-15", TESSERA_ASSO="42"),
    ]
    donazioni = [riga_donazione(ROSSI, "2026-02-01", tot_don="2"), riga_donazione(GIALLI, "2026-03-01", tipo="PL"),
                 # AssoWeb conta più donazioni di quelle nel DB: ne manca qualcuna
                 riga_donazione(BIANCHI, "2026-04-01", tot_don="5")]
    return donatori, donazioni


def test_leggi_csv(tmp_path):
    percorso = tmp_path / "TOT_DONAZIONI.csv"
    percorso.write_bytes(b'"CODICE_FISCALE";"DATA_DON";"TIPO";\r\n"RSSNNA80A41A001X";"2026-01-03";"SI";\r\n')
    assert leggi_csv(percorso) == [{"CODICE_FISCALE": "RSSNNA80A41A001X", "DATA_DON": "2026-01-03", "TIPO": "SI",
                                    "": ""}]


def test_campi_da_riga():
    campi, anomalie = campi_da_riga(riga_donatore(ROSSI, "ROSSI", "ANNA", "01/01/1980", E_MAIL="", IDONEITA="Idoneo",
                                                  DATA_FINE="2026-12-31", TESSERA_ASSO="42"), "1529")
    assert campi["data_nascita"] == data(1980, 1, 1)
    # i vuoti non sovrascrivono, l'idoneità si prende intera
    assert "email" not in campi
    assert (campi["idoneita"], campi["idoneita_dal"], campi["idoneita_al"]) == (Idoneita.IDONEO, None, data(2026, 12, 31))
    # la tessera nel formato di AssoAVIS
    assert campi["tessera"] == "152900042"
    assert anomalie == []

    campi, anomalie = campi_da_riga(riga_donatore(ROSSI, "ROSSI", "ANNA", "1980-01-01", IDONEITA="Boh",
                                                  TESSERA_ASSO="12/3"), "1529")
    assert "idoneita" not in campi and "tessera" not in campi and len(anomalie) == 2

    with pytest.raises(ImportazioneError):
        campi_da_riga(riga_donatore(ROSSI, "ROSSI", "ANNA", "1 gennaio 1980"), "1529")


@pytest.mark.parametrize(("valore", "attesa", "anomalia"), [
    ("", None, False),
    ("42", "152900042", False),
    (" 7 ", "152900007", False),
    ("12345", "152912345", False),
    # già nel formato di AssoAVIS
    ("152900042", "152900042", False),
    # formati che AssoWeb ha in qualche riga
    ("123456", None, True),
    ("12345/1", None, True),
    ("1.234", None, True),
    ("999900042", None, True),
])
def test_tessera_da_assoweb(valore, attesa, anomalia):
    tessera, testo = tessera_da_assoweb(valore, "1529")
    assert (tessera, testo is not None) == (attesa, anomalia)


NASCITA, PRIMA = data(1960, 6, 15), data(1985, 3, 1)


@pytest.mark.parametrize(("iscrizione", "attuale", "prima", "attesa", "anomalia"), [
    (None, data(1990, 1, 1), PRIMA, None, False),
    # impossibili: prima del 1900, futura, prima dei 18 anni
    (data(1890, 1, 1), None, PRIMA, None, True),
    (data(2026, 10, 30), None, PRIMA, None, True),
    (data(1978, 6, 14), None, PRIMA, None, True),
    (data(1978, 6, 15), None, PRIMA, data(1978, 6, 15), False),
    # migliora la data attuale: vuota, segnaposto o più recente
    (data(1984, 1, 1), None, PRIMA, data(1984, 1, 1), False),
    (data(1984, 1, 1), SEGNAPOSTO_ISCRIZIONE, PRIMA, data(1984, 1, 1), False),
    (data(1984, 1, 1), data(1985, 1, 1), PRIMA, data(1984, 1, 1), False),
    (data(1985, 2, 1), data(1984, 1, 1), PRIMA, None, False),
    (data(1984, 1, 1), data(1984, 1, 1), PRIMA, None, False),
    (SEGNAPOSTO_ISCRIZIONE, SEGNAPOSTO_ISCRIZIONE, data(1990, 1, 1), None, False),
    # dopo la prima donazione non si usa, anche al posto del segnaposto...
    (data(2018, 5, 1), SEGNAPOSTO_ISCRIZIONE, PRIMA, None, False),
    # ...salvo che lo sia anche la data attuale: vale la più vecchia
    (data(2013, 1, 1), data(2016, 1, 1), data(1999, 1, 1), data(2013, 1, 1), False),
    (data(2016, 1, 1), data(2013, 1, 1), data(1999, 1, 1), None, False),
    # senza donazioni registrate
    (data(1990, 1, 1), None, None, data(1990, 1, 1), False),
])
def test_iscrizione_da_assoweb(iscrizione, attuale, prima, attesa, anomalia):
    nuova, testo = iscrizione_da_assoweb(iscrizione, attuale, prima, NASCITA, OGGI)
    assert (nuova, testo is not None) == (attesa, anomalia)


def test_importa(storico):
    report = importa(*csv_assoweb(), oggi=OGGI)

    assert report.conteggi == {"righe donatori": 4, "soci nuovi": 1, "soci aggiornati": 3,
                               "codici fiscali aggiunti o corretti": 2, "righe donazioni": 3,
                               "donazioni aggiunte": 3, "donazioni già presenti": 0,
                               "date di iscrizione da AssoWeb": 2}
    rossi = Socio.objects.get(codice_fiscale=ROSSI)
    assert (rossi.comune_residenza, rossi.cap_residenza, rossi.gruppo_sanguigno, rossi.rh) == ("GALLIO", "36032", "A", "+")
    # i contatti che AssoWeb non ha restano quelli dello storico
    assert (rossi.email, rossi.indirizzo_residenza) == ("anna@esempio.it", "VIA ROMA 1")
    assert (rossi.idoneita, rossi.idoneita_dal) == (Idoneita.IDONEO, data(2026, 1, 10))
    assert rossi.stato == StatoSocio.DONATORE and rossi.aggiornato_assoweb_il
    # il segnaposto 31/12/1997 lascia il posto alla data di AssoWeb
    assert (rossi.data_iscrizione, rossi.data_iscrizione_assoweb) == (data(1999, 4, 10), data(1999, 4, 10))
    assert rossi.tessera == "152900042"

    # agganciati per cognome, nome e data di nascita
    bianchi = Socio.objects.get(codice_fiscale=BIANCHI)
    assert bianchi.pk == storico["bianchi"].pk
    # la data di AssoWeb è dopo la prima donazione: resta salvata, ma l'iscrizione è la prima donazione
    assert (bianchi.data_iscrizione, bianchi.data_iscrizione_assoweb) == (data(2026, 4, 1), data(2026, 5, 1))
    assert bianchi.tessera == "152900077"
    verdi = Socio.objects.get(codice_fiscale=VERDI)
    assert verdi.pk == storico["verdi"].pk and verdi.tessera == "152900101"
    # senza donazioni recenti resta ex socio
    assert verdi.stato == StatoSocio.EX_SOCIO

    nuova = Socio.objects.get(codice_fiscale=GIALLI)
    assert (nuova.cognome, nuova.stato_assoweb, nuova.stato) == ("GIALLI", "Aspirante", StatoSocio.DONATORE)
    # un socio nuovo prende l'iscrizione di AssoWeb, non la prima donazione (2026-03-01)
    assert nuova.data_iscrizione == data(2025, 11, 15)
    # la tessera ripetuta su AssoWeb resta a Rossi, che l'aveva già
    assert nuova.tessera == ""
    assert nuova.donazioni.get().fonte == Fonte.ASSOWEB

    assert report.passaggi_stato == {(StatoSocio.EX_SOCIO, StatoSocio.DONATORE): 2, ("", StatoSocio.DONATORE): 1}
    tipi = {tipo for tipo, _, _ in report.anomalie}
    assert tipi == {"Residenza cambiata", "Codice fiscale aggiunto", "Codice fiscale corretto", "Tessera cambiata",
                    "Donazioni che mancano nel DB", "Socio donatore assente da AssoWeb", "Dato non importato"}
    assert ("Tessera cambiata", f"socio {storico['verdi'].pk}",
            "AssoWeb ha un numero diverso da quello dello storico AssoAVIS") in report.anomalie
    assert ("Dato non importato", "riga 5", "tessera di AssoWeb già di un altro socio") in report.anomalie
    assert ("Dato non importato", f"socio {storico['verdi'].pk}",
            "data di iscrizione di AssoWeb prima dei 18 anni") in report.anomalie
    assert Socio.objects.get(codice_fiscale=VERDI).data_iscrizione is None
    assert ("Socio donatore assente da AssoWeb", f"socio {storico['neri'].pk}", "") in report.anomalie


def test_secondo_import_non_cambia_nulla(storico):
    importa(*csv_assoweb(), oggi=OGGI)
    soci, donazioni = list(Socio.objects.values()), list(Donazione.objects.values())

    report = importa(*csv_assoweb(), oggi=OGGI)

    assert report.conteggi["soci nuovi"] == report.conteggi["soci aggiornati"] == 0
    assert report.conteggi["donazioni aggiunte"] == 0 and report.conteggi["donazioni già presenti"] == 3
    assert report.conteggi["date di iscrizione da AssoWeb"] == 0
    assert report.passaggi_stato == {}
    escludi = {"aggiornato_assoweb_il"}
    assert [{k: v for k, v in s.items() if k not in escludi} for s in Socio.objects.values()] == \
           [{k: v for k, v in s.items() if k not in escludi} for s in soci]
    assert list(Donazione.objects.values()) == donazioni


def test_correzione_a_mano_dell_iscrizione(storico):
    importa(*csv_assoweb(), oggi=OGGI)
    Socio.objects.filter(codice_fiscale=ROSSI).update(data_iscrizione=data(2001, 1, 1))

    # la data su AssoWeb non è cambiata: la correzione resta
    importa(*csv_assoweb(), oggi=OGGI)
    assert Socio.objects.get(codice_fiscale=ROSSI).data_iscrizione == data(2001, 1, 1)

    # la data su AssoWeb cambia ed è più vecchia: si rivaluta
    donatori, donazioni = csv_assoweb()
    donatori[0]["DATA_ISCRIZIONE"] = "1998-09-01"
    report = importa(donatori, donazioni, oggi=OGGI)
    assert Socio.objects.get(codice_fiscale=ROSSI).data_iscrizione == data(1998, 9, 1)
    assert report.conteggi["date di iscrizione da AssoWeb"] == 1


def test_omonimi_e_tipo_diverso(storico):
    # un secondo Bianchi Luca senza CF con la stessa data di nascita: l'aggancio per nome non è più univoco
    Socio.objects.create(sezione=storico["bianchi"].sezione, cognome="BIANCHI", nome="LUCA", data_nascita=data(1970, 2, 2))
    Donazione.objects.create(socio=storico["rossi"], data=data(2026, 2, 1), tipo=TipoDonazione.objects.get(codice="PL"),
                             fonte=Fonte.ASSOAVIS)

    report = importa(*csv_assoweb(), oggi=OGGI)

    assert report.conteggi["soci nuovi"] == 2
    assert Socio.objects.filter(cognome="BIANCHI").count() == 3
    assert storico["rossi"].donazioni.get(data=data(2026, 2, 1)).tipo.codice == "PL"
    tipi = {tipo for tipo, _, _ in report.anomalie}
    assert {"Omonimi: creato un nuovo socio", "Donazione già presente con un altro tipo"} <= tipi


def test_file_non_validi(storico):
    donatori, donazioni = csv_assoweb()
    with pytest.raises(ImportazioneError, match="vuoto"):
        importa([], donazioni, oggi=OGGI)
    del donatori[0]["KELL"]
    with pytest.raises(ImportazioneError, match="KELL"):
        importa(donatori, donazioni, oggi=OGGI)
