"""Test del flusso delle premiazioni (calcolo.py) e delle pagine del modulo Benemerenze."""

import datetime
from io import BytesIO

import pytest
from django.urls import reverse
from openpyxl import load_workbook

from gestionale.benemerenze import calcolo
from gestionale.benemerenze.models import (Benemerenza, BenemerenzaAssegnata, Premiato, Premiazione,
                                           StatoPremiazione)
from gestionale.benemerenze.verifica import verifica
from gestionale.core.models import Evento, Sezione
from gestionale.soci.models import Donazione, Fonte, Socio, StatoSocio, TipoDonazione

data = datetime.date
ULTIMA_CONSEGNA = data(2025, 11, 3)


@pytest.fixture
def soci(db):
    """Soci con 8 donazioni (Rame), salvo dove indicato; e l'ultima premiazione assegnata il 03/11/2025."""
    sezione = Sezione.objects.get(codice="1529")
    si = TipoDonazione.objects.get(codice="SI")
    Premiazione.objects.create(descrizione="Premiazione 2025", data_consegna=ULTIMA_CONSEGNA,
                               data_riferimento=ULTIMA_CONSEGNA, stato=StatoPremiazione.ASSEGNATA)

    def socio(cognome, stato, n=8, **campi):
        s = Socio.objects.create(sezione=sezione, cognome=cognome, nome="X", stato=stato, **campi)
        Donazione.objects.bulk_create(Donazione(socio=s, data=data(2020 + i // 4, 1 + i % 4 * 3, 1), tipo=si,
                                                fonte=Fonte.ASSOAVIS) for i in range(n))
        return s

    return {
        "rossi": socio("ROSSI", StatoSocio.DONATORE),
        "verdi": socio("VERDI", StatoSocio.DONATORE, n=16),  # Rame e Argento insieme
        "neri": socio("NERI", StatoSocio.DONATORE, n=3),  # non matura nulla
        "gialli": socio("GIALLI", StatoSocio.DONATORE, no_benemerenze=True),
        "blu": socio("BLU", StatoSocio.COLLABORATORE, collaboratore=True),
        "rosa": socio("ROSA", StatoSocio.EX_SOCIO, data_cessazione=data(2025, 12, 31)),
        "grigi": socio("GRIGI", StatoSocio.EX_SOCIO, data_cessazione=data(2024, 12, 31)),
    }


def nuova_premiazione(**campi):
    campi = {"descrizione": "Premiazione 2026", "data_consegna": data(2026, 12, 8),
             "data_riferimento": data(2026, 10, 1), "ex_soci_cessati_dal": ULTIMA_CONSEGNA, **campi}
    return Premiazione.objects.create(**campi)


def premiati(premiazione, **filtri):
    return sorted(premiazione.premiati.filter(**filtri).values_list("socio__cognome", "benemerenza__codice"))


def test_calcola_gli_aventi_diritto(soci):
    premiazione = nuova_premiazione()
    assert calcolo.calcola(premiazione) == 3
    # esclusi: chi non matura, chi non le desidera, i collaboratori e gli ex soci cessati prima dell'ultima
    assert premiati(premiazione) == [("ROSA", 1), ("ROSSI", 1), ("VERDI", 1), ("VERDI", 2)]
    assert premiazione.stato == StatoPremiazione.CALCOLATA
    verdi = premiazione.premiati.get(socio=soci["verdi"], benemerenza__codice=2)
    assert (verdi.donazioni_valide, verdi.donazioni_effettive) == (16, 16)


def test_chi_ha_gia_la_benemerenza_non_la_riceve(soci):
    BenemerenzaAssegnata.objects.create(socio=soci["rossi"], benemerenza=Benemerenza.objects.get(codice=2),
                                        data=ULTIMA_CONSEGNA, fonte=Fonte.ASSOAVIS)
    premiazione = nuova_premiazione()
    calcolo.calcola(premiazione)
    assert ("ROSSI", 1) not in premiati(premiazione)


def test_il_ricalcolo_conserva_la_revisione(soci):
    premiazione = nuova_premiazione()
    calcolo.calcola(premiazione)
    calcolo.escludi(premiazione, soci["verdi"].pk, "trasferito")
    calcolo.aggiungi(premiazione, soci["neri"], Benemerenza.objects.get(codice=1), "donazioni di un'altra sede")
    calcolo.calcola(premiazione)
    assert premiati(premiazione, escluso=True) == [("VERDI", 1), ("VERDI", 2)]
    assert set(premiazione.premiati.filter(escluso=True).values_list("motivo", flat=True)) == {"trasferito"}
    neri = premiazione.premiati.get(socio=soci["neri"])
    assert (neri.aggiunto_a_mano, neri.donazioni_effettive) == (True, 3)
    # riammesso con il motivo vuoto
    calcolo.escludi(premiazione, soci["verdi"].pk, "")
    assert not premiazione.premiati.filter(escluso=True).exists()


def test_aggiunta_rifiutata(soci):
    premiazione = nuova_premiazione()
    calcolo.calcola(premiazione)
    rame = Benemerenza.objects.get(codice=1)
    with pytest.raises(calcolo.PremiazioneError, match="ha già questa benemerenza"):
        calcolo.aggiungi(premiazione, soci["rossi"], rame, "doppione")
    BenemerenzaAssegnata.objects.create(socio=soci["neri"], benemerenza=rame, fonte=Fonte.ASSOAVIS)
    with pytest.raises(calcolo.PremiazioneError, match="possiede già"):
        calcolo.aggiungi(premiazione, soci["neri"], rame, "già consegnata")


def test_assegna(soci):
    premiazione = nuova_premiazione()
    calcolo.calcola(premiazione)
    calcolo.escludi(premiazione, soci["rosa"].pk, "non più reperibile")
    assert calcolo.assegna(premiazione) == 3
    premiazione.refresh_from_db()
    assert premiazione.stato == StatoPremiazione.ASSEGNATA
    assegnate = BenemerenzaAssegnata.objects.filter(fonte=Fonte.GESTIONALE)
    assert sorted(assegnate.values_list("socio__cognome", "benemerenza__codice")) == [
        ("ROSSI", 1), ("VERDI", 1), ("VERDI", 2)]
    assert set(assegnate.values_list("data", flat=True)) == {data(2026, 12, 8)}
    # è irreversibile: non si ricalcola, non si rivede e non si riassegna
    for azione in (lambda: calcolo.calcola(premiazione), lambda: calcolo.assegna(premiazione),
                   lambda: calcolo.escludi(premiazione, soci["rossi"].pk, "x")):
        with pytest.raises(calcolo.PremiazioneError):
            azione()


def test_si_assegna_solo_una_premiazione_calcolata(soci):
    with pytest.raises(calcolo.PremiazioneError, match="calcolata"):
        calcolo.assegna(nuova_premiazione())


def test_premiati_per_socio(soci):
    premiazione = nuova_premiazione()
    calcolo.calcola(premiazione)
    calcolo.escludi(premiazione, soci["rosa"].pk, "motivo")
    da_premiare, esclusi = calcolo.premiati_per_socio(premiazione)
    # Verdi riceve l'Argento, che matura insieme al Rame
    assert [(g.socio.cognome, g.principale.benemerenza.codice, [b.codice for b in g.insieme_a])
            for g in da_premiare] == [("ROSSI", 1, []), ("VERDI", 2, [1])]
    assert [g.socio.cognome for g in esclusi] == ["ROSA"]


def test_situazione_del_socio(soci):
    situazione = calcolo.situazione(soci["neri"], data(2026, 11, 1))
    assert situazione["conteggio"].valide == 3
    assert situazione["maturate"] == []
    assert (situazione["prossima_benemerenza"].codice, situazione["prossima"].mancano) == (1, 5)
    assert situazione["premiabile"]
    assert calcolo.situazione(soci["verdi"], data(2026, 11, 1))["maturate"] == list(
        Benemerenza.objects.filter(codice__in=[1, 2]).order_by("ordine"))
    assert not calcolo.situazione(soci["blu"], data(2026, 11, 1))["premiabile"]


def test_verifica_sulle_premiazioni_storiche(soci):
    storica = Premiazione.objects.create(descrizione="AssoAVIS", data_consegna=data(2025, 11, 3), codice_assoavis=118,
                                         data_riferimento=data(2025, 11, 3), stato=StatoPremiazione.ASSEGNATA)
    rame, argento, collab = (Benemerenza.objects.get(codice=c) for c in (1, 2, 7))
    Premiato.objects.create(premiazione=storica, socio=soci["rossi"], benemerenza=rame, donazioni_valide=8,
                            donazioni_effettive=8)
    Premiato.objects.create(premiazione=storica, socio=soci["verdi"], benemerenza=argento, donazioni_valide=17,
                            donazioni_effettive=16)
    Premiato.objects.create(premiazione=storica, socio=soci["neri"], benemerenza=rame, donazioni_valide=8,
                            donazioni_effettive=9)
    Premiato.objects.create(premiazione=storica, socio=soci["blu"], benemerenza=collab, donazioni_valide=8,
                            donazioni_effettive=8)
    [riga] = verifica()
    assert (riga.premiati, riga.collaboratori, riga.effettive_uguali, riga.valide_uguali, riga.maturate) == (
        3, 1, 2, 1, 2)
    assert riga.valide_diverse == [soci["verdi"].pk]


# --- pagine ---

def test_un_volontario_prepara_la_premiazione(client, volontario, soci):
    client.force_login(volontario)
    assert client.get(reverse("benemerenze")).status_code == 200
    risposta = client.get(reverse("premiazione_nuova"))
    assert risposta.context["form"].initial["ex_soci_cessati_dal"] == ULTIMA_CONSEGNA
    risposta = client.post(reverse("premiazione_nuova"), {
        "descrizione": "Premiazione 2026", "data_consegna": "2026-12-08", "data_riferimento": "2026-10-01",
        "ex_soci_cessati_dal": "2025-11-03"})
    premiazione = Premiazione.objects.get(descrizione="Premiazione 2026")
    assert risposta.url == reverse("premiazione", args=[premiazione.pk])
    client.post(reverse("premiazione_calcola", args=[premiazione.pk]))
    client.post(reverse("premiazione_escludi", args=[premiazione.pk, soci["rosa"].pk]), {"motivo": "trasferita"})
    client.post(reverse("premiazione_aggiungi", args=[premiazione.pk]),
                {"socio": soci["neri"].pk, "benemerenza": Benemerenza.objects.get(codice=1).pk, "motivo": "altra sede"})
    risposta = client.get(reverse("premiazione", args=[premiazione.pk]))
    assert [g.socio.cognome for g in risposta.context["premiati"]] == ["NERI", "ROSSI", "VERDI"]
    assert [g.socio.cognome for g in risposta.context["esclusi"]] == ["ROSA"]
    # il volontario non assegna
    assert client.post(reverse("premiazione_assegna", args=[premiazione.pk]), {"conferma": "1"}).status_code == 403
    azioni = list(Evento.objects.filter(oggetto=f"premiazione {premiazione.pk}").values_list("azione", flat=True))
    assert sorted(azioni) == ["aggiungi_premiato", "calcola_premiazione", "escludi_premiato", "nuova_premiazione"]
    # il motivo è testo libero e non va nel registro
    assert Evento.objects.get(azione="escludi_premiato").dettagli == {"socio": soci["rosa"].pk}


def test_una_premiazione_aperta_alla_volta(client, volontario, soci):
    aperta = nuova_premiazione()
    client.force_login(volontario)
    assert client.get(reverse("premiazione_nuova")).url == reverse("premiazione", args=[aperta.pk])


def test_escludere_richiede_il_motivo(client, volontario, soci):
    premiazione = nuova_premiazione()
    calcolo.calcola(premiazione)
    client.force_login(volontario)
    client.post(reverse("premiazione_escludi", args=[premiazione.pk, soci["rossi"].pk]), {"motivo": " "})
    assert not premiazione.premiati.filter(escluso=True).exists()


def test_cambiare_le_date_richiede_il_ricalcolo(client, volontario, soci):
    premiazione = nuova_premiazione()
    calcolo.calcola(premiazione)
    client.force_login(volontario)
    client.post(reverse("premiazione_modifica", args=[premiazione.pk]), {
        "descrizione": "Premiazione 2026", "data_consegna": "2026-12-08", "data_riferimento": "2026-11-01",
        "ex_soci_cessati_dal": "2025-11-03"})
    premiazione.refresh_from_db()
    assert (premiazione.stato, premiazione.data_riferimento) == (StatoPremiazione.BOZZA, data(2026, 11, 1))


def test_l_amministratore_assegna_con_la_conferma(client, amministratore, soci):
    premiazione = nuova_premiazione()
    calcolo.calcola(premiazione)
    client.force_login(amministratore)
    client.post(reverse("premiazione_assegna", args=[premiazione.pk]))
    premiazione.refresh_from_db()
    assert premiazione.stato == StatoPremiazione.CALCOLATA
    client.post(reverse("premiazione_assegna", args=[premiazione.pk]), {"conferma": "1"})
    premiazione.refresh_from_db()
    assert premiazione.stato == StatoPremiazione.ASSEGNATA
    assert Evento.objects.get(azione="assegna_premiazione").dettagli == {"benemerenze": 4}
    # assegnata non si elimina
    client.post(reverse("premiazione_elimina", args=[premiazione.pk]))
    assert Premiazione.objects.filter(pk=premiazione.pk).exists()


def test_elimina_una_premiazione_non_assegnata(client, volontario, soci):
    premiazione = nuova_premiazione()
    calcolo.calcola(premiazione)
    client.force_login(volontario)
    assert client.post(reverse("premiazione_elimina", args=[premiazione.pk])).url == reverse("benemerenze")
    assert not Premiazione.objects.filter(pk=premiazione.pk).exists()


def test_export_excel(client, volontario, soci):
    premiazione = nuova_premiazione()
    calcolo.calcola(premiazione)
    calcolo.escludi(premiazione, soci["rosa"].pk, "trasferita")
    client.force_login(volontario)
    risposta = client.get(reverse("premiazione_esporta", args=[premiazione.pk]))
    libro = load_workbook(BytesIO(risposta.content))
    assert libro.sheetnames == ["Riepilogo", "Premiati", "Esclusi"]
    righe = list(libro["Premiati"].values)
    assert [r[:3] for r in righe[1:]] == [("Rame", None, "ROSSI"), ("Argento", "Rame", "VERDI")]
    assert Evento.objects.filter(azione="export_premiazione").count() == 1


def test_scheda_socio_con_la_prossima_benemerenza(client, volontario, soci):
    client.force_login(volontario)
    testo = " ".join(client.get(reverse("socio", args=[soci["neri"].pk])).text.split())
    assert "A oggi 3 donazioni valide (3 effettive)." in testo
    assert "Prossima:</span> Rame, mancano 5 donazioni valide." in testo
    testo = " ".join(client.get(reverse("socio", args=[soci["blu"].pk])).text.split())
    assert "I collaboratori non ricevono benemerenze." in testo
