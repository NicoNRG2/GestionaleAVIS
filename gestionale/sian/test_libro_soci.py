"""Estrazione del libro soci e controlli prima dell'invio (README M5)."""

import datetime
from io import BytesIO

import pytest
from openpyxl import load_workbook

from gestionale.core.models import Sezione
from gestionale.soci.models import CausaCessazione, Donazione, Fonte, Socio, StatoSocio, TipoDonazione

from . import excel, libro_soci

data = datetime.date
OGGI = data(2026, 11, 2)


def cf(numero):
    """Codice fiscale formalmente valido, diverso per ogni numero da 0 a 99."""
    primi = f"RSSMRA80A01H5{numero:02}"
    return primi + libro_soci.carattere_di_controllo(primi)


@pytest.fixture
def crea(db):
    sezione = Sezione.objects.get(codice="1529")
    tipi = {t.codice: t for t in TipoDonazione.objects.all()}
    contatore = iter(range(100))

    def socio(cognome, donazioni=(), stato=StatoSocio.DONATORE, **campi):
        valori = {"nome": "X", "codice_fiscale": cf(next(contatore)), "data_iscrizione": data(2010, 5, 1), "sesso": "M",
                  "data_nascita": data(1980, 1, 1), "luogo_nascita": "ASIAGO", "provincia_nascita": "VI",
                  "indirizzo_residenza": "VIA ROMA 1", "comune_residenza": "ASIAGO", "provincia_residenza": "VI",
                  "cap_residenza": "36012", "cellulare": "333 123 4567", "email": "socio@example.com"}
        s = Socio.objects.create(sezione=sezione, cognome=cognome, stato=stato, **(valori | campi))
        for giorno, *tipo in donazioni:
            Donazione.objects.create(socio=s, data=giorno, tipo=tipi[tipo[0] if tipo else "SI"], fonte=Fonte.ASSOWEB)
        return s

    return socio


def estrai(anno=2026, oggi=OGGI):
    libro = libro_soci.estrai(anno, Sezione.objects.get(codice="1529"), oggi)
    return libro, {r.socio.cognome: r for r in libro.record}


def test_soci_con_donazioni_degli_ultimi_due_anni_e_collaboratori(crea):
    crea("DONA_2024", [(data(2024, 6, 1),)])
    crea("DONA_2026", [(data(2026, 3, 1), "PL")])
    crea("DONA_2023", [(data(2023, 12, 30),)], stato=StatoSocio.EX_SOCIO, data_cessazione=data(2025, 12, 31),
         causa_cessazione=CausaCessazione.CESSAZIONE)
    crea("SOLO_PRESENTAZIONE", [(data(2026, 2, 1), "XX")], stato="")
    crea("COLLABORATORE", stato=StatoSocio.COLLABORATORE, collaboratore=True)
    crea("COLLABORATORE_DONA", [(data(2025, 4, 1),)], collaboratore=True)
    crea("NON_SOCIO", stato="")

    libro, record = estrai()

    assert sorted(record) == ["COLLABORATORE", "COLLABORATORE_DONA", "DONA_2024", "DONA_2026"]
    assert {nome: r.tipo_socio for nome, r in record.items()} == {
        "COLLABORATORE": 2, "COLLABORATORE_DONA": 1, "DONA_2024": 1, "DONA_2026": 1}
    # durante l'anno chi ha donato l'ultima volta nel 2024 è ancora socio
    assert not any(r.cessato for r in libro.record)
    assert libro.riepilogo() == {"donatori": 3, "collaboratori": 1, "soci": 4, "nuovi": 0, "cessati": 0,
                                       "totale": 4, "inviabili": 4, "scartati": 0, "con_avvisi": 0}


def test_cessati_nell_anno(crea):
    crea("DIMESSO", [(data(2025, 1, 10),)], stato=StatoSocio.EX_SOCIO, data_cessazione=data(2026, 3, 1),
         causa_cessazione=CausaCessazione.DIMISSIONI)
    # cessato nel 2025 dopo una donazione nel 2024: non è nel registro 2026
    crea("CESSATO_2025", [(data(2024, 2, 1),)], stato=StatoSocio.EX_SOCIO, data_cessazione=data(2025, 6, 1),
         causa_cessazione=CausaCessazione.DIMISSIONI)
    # dimissioni nel 2027: nel registro 2026 è ancora socio
    crea("DIMESSO_2027", [(data(2026, 1, 10),)], stato=StatoSocio.EX_SOCIO, data_cessazione=data(2027, 1, 5),
         causa_cessazione=CausaCessazione.DIMISSIONI)

    libro, record = estrai(oggi=data(2027, 1, 10))

    assert sorted(record) == ["DIMESSO", "DIMESSO_2027"]
    assert record["DIMESSO"].campi["DATACESSAZIONE"] == data(2026, 3, 1)
    assert record["DIMESSO"].campi["CAUSACESSAZIONE"] == CausaCessazione.DIMISSIONI
    assert not record["DIMESSO_2027"].cessato
    assert libro.riepilogo()["soci"] == 1 and libro.riepilogo()["cessati"] == 1


def test_a_fine_anno_cessa_chi_ha_donato_l_ultima_volta_due_anni_prima(crea):
    crea("DONA_2024", [(data(2024, 6, 1),)])
    crea("COLLABORATORE_DONA_2024", [(data(2024, 6, 1),)], collaboratore=True)
    crea("DONA_2025", [(data(2025, 6, 1),)])

    _, durante = estrai(oggi=data(2026, 12, 30))
    _, dopo = estrai(oggi=data(2027, 1, 8))

    assert not durante["DONA_2024"].cessato
    assert dopo["DONA_2024"].campi["DATACESSAZIONE"] == data(2026, 12, 31)
    assert dopo["DONA_2024"].campi["CAUSACESSAZIONE"] == CausaCessazione.CESSAZIONE
    # i collaboratori non cessano mai
    assert not dopo["COLLABORATORE_DONA_2024"].cessato
    assert not dopo["DONA_2025"].cessato


def test_campi_del_tracciato(crea):
    socio = crea("ROSSI", [(data(2026, 3, 1),)], cellulare="+39 333-1234567", email=" ", nome="MARIA ",
                 sesso="F", tessera="152900123", data_iscrizione=data(2026, 3, 1))

    libro, record = estrai()
    campi = record["ROSSI"].campi

    assert list(campi) == libro_soci.COLONNE and len(campi) == 24
    assert campi["CODICESEDE"] == "1529" and campi["ANNORIFERIMENTO"] == 2026
    assert campi["UNIVOCOAVIS"] == str(socio.pk)
    assert campi["NOME"] == "MARIA" and campi["GENERE"] == "F"
    assert (campi["CELLULARE"], campi["CELLULARENONDISP"]) == ("393331234567", 0)
    assert (campi["EMAIL"], campi["EMAILNONDISP"]) == ("", 1)
    assert campi["TESSERA"] == "152900123" and campi["PEC"] == ""
    assert campi["DATACESSAZIONE"] is None and campi["CAUSACESSAZIONE"] is None
    assert libro.riepilogo()["nuovi"] == 1
    assert record["ROSSI"].avvisi == ["Email non disponibile"]


@pytest.mark.parametrize(("genere", "sesso"), [("M", 1), ("F", 2), ("", None)])
def test_record_per_il_web_service(genere, sesso):
    campi = dict.fromkeys(libro_soci.COLONNE, "") | {"GENERE": genere, "CAUSACESSAZIONE": None, "TIPOSOCIO": 1}

    xml = libro_soci.record_xml(campi)

    # il SIAN vuole il sesso come numero: con "M" non acquisisce il socio
    assert "GENERE" not in xml and xml["SESSO"] == sesso
    assert xml["CAUSACESSAZIONE"] == 0 and xml["TIPORECORD"] == 1


def test_controlli_prima_dell_invio(crea):
    crea("SENZA_CF", [(data(2026, 3, 1),)], codice_fiscale=None)
    crea("CF_ERRATO", [(data(2026, 3, 1),)], codice_fiscale="RSSMRA80A01H501V")
    crea("ISCRITTO_DOPO", [(data(2026, 3, 1),)], data_iscrizione=data(2027, 1, 1))
    crea("SENZA_ISCRIZIONE", [(data(2026, 3, 1),)], data_iscrizione=None)
    crea("SENZA_CAUSA", [(data(2025, 3, 1),)], stato=StatoSocio.EX_SOCIO, data_cessazione=data(2026, 2, 1))
    crea("SENZA_RESIDENZA", [(data(2026, 3, 1),)], indirizzo_residenza="", cap_residenza="", luogo_nascita="",
         cellulare="", email="non-valida")
    crea("CAP_CORTO", [(data(2026, 3, 1),)], cap_residenza="3601")

    libro, record = estrai()

    assert record["SENZA_CF"].scarti == ["Codice fiscale mancante"]
    assert record["CF_ERRATO"].scarti == ["Codice fiscale non valido"]
    assert record["ISCRITTO_DOPO"].scarti == ["Data di iscrizione futura o dopo l'anno del registro"]
    assert record["SENZA_ISCRIZIONE"].scarti == ["Data di iscrizione mancante"]
    assert record["SENZA_CAUSA"].scarti == ["Causa di cessazione mancante"]
    assert record["SENZA_RESIDENZA"].inviabile
    assert record["SENZA_RESIDENZA"].avvisi == ["Luogo o provincia di nascita mancante", "Residenza incompleta",
                                                "Cellulare non disponibile", "Email non valida"]
    assert record["CAP_CORTO"].avvisi == ["CAP non di 5 cifre"]
    assert len(libro.inviabili) == 2 and len(libro.scartati) == 5
    assert [(motivo, len(r)) for motivo, r in libro.motivi("scarti")][0] == ("Causa di cessazione mancante", 1)


@pytest.mark.parametrize(("cf", "valido"), [
    ("RSSMRA80A01H501U", True),
    ("MRTMTT25D09F205Z", True),
    # omocodia: le cifre possono diventare lettere, il controllo cambia
    ("RSSMRA80A01H50RU", False),
    ("RSSMRA80A01H501V", False),
    ("RSSMRA80A01H501", False),
    ("", False),
])
def test_codice_fiscale(cf, valido):
    assert libro_soci.codice_fiscale_valido(cf) is valido


def test_export_per_l_uploader(crea):
    crea("ROSSI", [(data(2026, 3, 1),)], cap_residenza="00042", data_nascita=data(1980, 1, 2))
    crea("SENZA_CF", [(data(2026, 3, 1),)], codice_fiscale=None)
    crea("DIMESSO", [(data(2025, 1, 10),)], stato=StatoSocio.EX_SOCIO, data_cessazione=data(2026, 3, 1),
         causa_cessazione=CausaCessazione.DIMISSIONI)
    libro, _ = estrai()

    foglio = load_workbook(BytesIO(excel.cartella(libro))).active
    righe = list(foglio.values)

    assert list(righe[0]) == libro_soci.COLONNE
    # solo i soci che superano i controlli
    assert len(righe) == 3
    rossi = dict(zip(righe[0], righe[1]))
    assert rossi["CAP"] == "00042" and rossi["DATANASCITA"] == "02/01/1980" and rossi["DATAISCRIZIONE"] == "01/05/2010"
    assert rossi["TIPOSOCIO"] == 1 and rossi["CAUSACESSAZIONE"] is None and rossi["DATACESSAZIONE"] is None
    dimesso = dict(zip(righe[0], righe[2]))
    assert dimesso["DATACESSAZIONE"] == "01/03/2026" and dimesso["CAUSACESSAZIONE"] == 11
