import datetime

import pytest

from gestionale.benemerenze.models import Benemerenza, BenemerenzaAssegnata, Premiato, Premiazione, StatoPremiazione
from gestionale.importazioni.assoavis import ImportazioneError, Storico, causa_cessazione, importa, socio_da_riga
from gestionale.soci.models import CausaCessazione, Donazione, Fonte, Socio, StatoSocio

OGGI = datetime.date(2026, 10, 29)
COLONNE_SOCIO = ["CODICEFISCALE", "TESSERA", "COGNOMEENOME", "DATADINASCITA", "LUOGONASCITA", "PROVDINASCITA",
                 "RESINDIRIZZO", "RESCITTA", "RESCAP", "RESPROV", "CELLULARE", "RESTEL", "EMAIL", "DATAPRIMAISCR",
                 "CANCELLADEFIN", "MOTIVOCANCELL", "DONAZIONIPREGR", "AFERESIPREGRES", "DONAZIONIPREGRDOPO2004",
                 "NOBENEM"]
A, B, C, D = "0000011529", "0000021529", "0000031529", "0000041529"


def dt(testo):
    """pyodbc restituisce le date come datetime."""
    return datetime.datetime.strptime(testo, "%d/%m/%Y")


def riga_socio(codice, benemerenze=None, **valori):
    """Riga di DONATORE; `benemerenze` è {codice benemerenza: data o None} e imposta le coppie flag/data."""
    riga = dict.fromkeys(COLONNE_SOCIO)
    riga.update(CODINTERNODONAT=codice, SOLOCOGNOME="Rossi", SOLONOME="Anna", SESSO="F")
    for b in Benemerenza.objects.all():
        riga[b.colonna_flag_assoavis] = 1 if b.codice in (benemerenze or {}) else 0
        riga[b.colonna_data_assoavis] = (benemerenze or {}).get(b.codice)
    riga.update(valori)
    return riga


def donazione(codice, data, tipo=1):
    return {"CODINTERNO": codice, "DATADON": dt(data), "TIPO": tipo, "NOMEPUNTOPREL": "Ospedale Asiago"}


def premiazione(id, consegna, assegnate):
    return {"ID": id, "DESCRIZIONE": f"Premiazione {id}", "DATAPREMIAZIONE": dt(consegna),
            "DATARIFERIMENTO": dt(consegna), "ASSEGNATE": assegnate}


def premiato(id_premiazione, codice, benemerenza):
    return {"IDPREMIAZIONE": id_premiazione, "CODINTERNODONAT": codice, "TIPOONOREFICIENZA": benemerenza,
            "NUMDONAZIONI": 10, "NUMDONAZEFF": 9}


@pytest.fixture
def storico(db):
    # C e D sono la stessa persona (stesso CF): resta D, che ha la donazione più recente
    soci = [
        riga_socio(A, CODICEFISCALE="rssnna80a41a001x", benemerenze={1: dt("01/01/2010"), 2: None}),
        riga_socio(B),
        riga_socio(C, CODICEFISCALE="BNCLCU70A01A001X", DATAPRIMAISCR=dt("01/03/2010"),
                   CANCELLADEFIN=dt("31/12/2020"), MOTIVOCANCELL="NON DONA DA 2 ANNI", benemerenze={1: dt("01/06/2015")}),
        riga_socio(D, CODICEFISCALE="BNCLCU70A01A001X", DATAPRIMAISCR=dt("01/03/2018"),
                   RESCITTA="Asiago", benemerenze={1: dt("01/06/2019")}),
    ]
    donazioni = [
        donazione(A, "10/02/2024"), donazione(A, "10/02/2025", tipo=2), donazione(B, "05/05/1990"),
        donazione(B, "05/05/1991", tipo=99), donazione(C, "01/04/2019"), donazione(D, "01/04/2025"),
    ]
    premiazioni = [premiazione(100, "15/10/2023", 1), premiazione(117, "15/10/2024", 0),
                   premiazione(118, "03/11/2025", 0)]
    premiati = [premiato(100, A, 1), premiato(100, B, 1), premiato(117, B, 2), premiato(118, A, 3),
                premiato(118, C, 2)]
    conteggi = {
        "anagrafiche": 4,
        "donazioni_per_anno": {1990: 1, 1991: 1, 2019: 1, 2024: 1, 2025: 2},
        "donazioni_per_tipo": {1: 4, 2: 1, 99: 1},
        "benemerenze": {1: 3, 2: 1},
        "premiazioni": 3,
        "premiati": 5,
    }
    return Storico(soci, donazioni, premiazioni, premiati, conteggi)


def test_causa_cessazione():
    assert causa_cessazione("TRASFERIMENTO ALTRA SEDE") == CausaCessazione.TRASFERITO
    assert causa_cessazione("Trasferimento a Predazzo") == CausaCessazione.TRASFERITO
    assert causa_cessazione("NON DONA DA 2 ANNI") == CausaCessazione.CESSAZIONE
    assert causa_cessazione(None) == CausaCessazione.CESSAZIONE


def test_socio_da_riga(db):
    campi, anomalie = socio_da_riga(riga_socio(A, CODICEFISCALE=" rssnna80a41a001x ", CANCELLADEFIN=dt("31/12/2025"),
                                               MOTIVOCANCELL="TRASFERIMENTO", NOBENEM=1), OGGI)
    assert campi["codice_fiscale"] == "RSSNNA80A41A001X"
    assert campi["stato"] == StatoSocio.EX_SOCIO
    assert campi["data_cessazione"] == datetime.date(2025, 12, 31)
    assert campi["causa_cessazione"] == CausaCessazione.TRASFERITO
    assert campi["no_benemerenze"] and anomalie == []

    campi, anomalie = socio_da_riga(riga_socio(B, CODICEFISCALE="", SOLOCOGNOME="", COGNOMEENOME="Rossi Anna",
                                               CANCELLADEFIN=dt("12/12/2206"), DONAZIONIPREGR=-2), OGGI)
    assert campi["codice_fiscale"] is None
    assert campi["cognome"] == "Rossi Anna"
    # la data nel futuro è un refuso: si scarta, e senza data non c'è nemmeno la causa
    assert campi["data_cessazione"] is None and campi["causa_cessazione"] is None
    # le pregresse negative sono correzioni manuali del totale: si tengono
    assert campi["donazioni_pregresse_si"] == -2
    assert len(anomalie) == 2


def test_importa(storico):
    report = importa(storico, oggi=OGGI)

    assert Socio.objects.count() == 3
    assert set(Socio.objects.values_list("stato", flat=True)) == {StatoSocio.EX_SOCIO}
    assert Socio.objects.get(codice_interno_assoavis=A).codice_fiscale == "RSSNNA80A41A001X"

    # fusione: resta D, con l'iscrizione più vecchia e le donazioni di entrambi, ma non la cessazione di C
    fuso = Socio.objects.get(codice_fiscale="BNCLCU70A01A001X")
    assert fuso.codice_interno_assoavis == D
    assert fuso.data_iscrizione == datetime.date(2010, 3, 1)
    assert fuso.data_cessazione is None
    assert C in fuso.note
    assert fuso.donazioni.count() == 2

    # donazioni: quella con il tipo sconosciuto si scarta
    assert Donazione.objects.count() == 5
    assert set(Donazione.objects.values_list("fonte", flat=True)) == {Fonte.ASSOAVIS}
    assert Donazione.objects.filter(tipo__codice="PL").count() == 1

    # benemerenze: i flag, più quelle della premiazione 118 consegnata ma mai chiusa in AssoAVIS
    def benemerenze(socio):
        return dict(BenemerenzaAssegnata.objects.filter(socio=socio).values_list("benemerenza__codice", "data"))

    consegna = datetime.date(2025, 11, 3)
    assert benemerenze(Socio.objects.get(codice_interno_assoavis=A)) == {
        1: datetime.date(2010, 1, 1), 2: None, 3: consegna}
    assert benemerenze(fuso) == {1: datetime.date(2015, 6, 1), 2: consegna}
    assert not benemerenze(Socio.objects.get(codice_interno_assoavis=B))

    stati = dict(Premiazione.objects.values_list("codice_assoavis", "stato"))
    assert stati == {100: StatoPremiazione.ASSEGNATA, 117: StatoPremiazione.CALCOLATA,
                     118: StatoPremiazione.ASSEGNATA}
    assert Premiato.objects.count() == 5
    assert Premiato.objects.filter(socio=fuso, premiazione__codice_assoavis=118).exists()

    assert report.coincide, report.righe
    tipi = {tipo for tipo, _, _ in report.anomalie}
    assert tipi == {"Codice fiscale duplicato", "Donazione scartata", "Premiato senza benemerenza"}


def test_riconciliazione_segnala_le_differenze(storico):
    storico.conteggi["donazioni_per_anno"][2025] = 3
    report = importa(storico, oggi=OGGI)
    assert not report.coincide
    assert [r for r in report.righe if r[1] + r[3] != r[2]][0][0] == "Donazioni 2025"


def test_importa_solo_su_db_vuoto(storico):
    importa(storico, oggi=OGGI)
    with pytest.raises(ImportazioneError):
        importa(storico, oggi=OGGI)
