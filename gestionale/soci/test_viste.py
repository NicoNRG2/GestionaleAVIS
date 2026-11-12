"""Test dell'elenco e della scheda dei soci (README M2)."""

import datetime

import pytest
from django.contrib.auth.models import User
from django.template import Context, Template
from django.urls import reverse

from gestionale.benemerenze.models import Benemerenza, BenemerenzaAssegnata
from gestionale.core.models import Evento, Sezione
from gestionale.soci.models import CausaCessazione, Donazione, Fonte, Idoneita, Socio, StatoSocio, TipoDonazione

data = datetime.date
CF_ROSSI = "RSSNNA80A41A001X"


@pytest.fixture
def soci(db):
    sezione = Sezione.objects.get(codice="1529")
    si, presentazione = TipoDonazione.objects.get(codice="SI"), TipoDonazione.objects.get(codice="XX")

    def socio(cognome, nome, stato, donazioni=(), **campi):
        s = Socio.objects.create(sezione=sezione, cognome=cognome, nome=nome, stato=stato, **campi)
        for giorno in donazioni:
            Donazione.objects.create(socio=s, data=giorno, tipo=si, fonte=Fonte.ASSOAVIS)
        return s

    rossi = socio("ROSSI", "ANNA", StatoSocio.DONATORE, [data(2025, 3, 1), data(2026, 2, 1)],
                  codice_fiscale=CF_ROSSI, idoneita=Idoneita.IDONEO, donazioni_pregresse_si=10, gruppo_sanguigno="A",
                  rh="+", email="anna@esempio.it")
    # "Solo presentazione" non conta tra le donazioni né come ultima donazione
    Donazione.objects.create(socio=rossi, data=data(2026, 6, 1), tipo=presentazione, fonte=Fonte.ASSOWEB)
    return {
        "rossi": rossi,
        "rossini": socio("ROSSINI", "MARCO", StatoSocio.COLLABORATORE, collaboratore=True),
        "bianchi": socio("BIANCHI", "LUCA", StatoSocio.EX_SOCIO, [data(2019, 5, 1)], idoneita=Idoneita.TEMP_SOSPESO,
                         data_cessazione=data(2021, 12, 31), causa_cessazione=CausaCessazione.CESSAZIONE),
        "verdi": socio("VERDI", "PAOLA", ""),
    }


def nomi(risposta):
    return [s.cognome for s in risposta.context["pagina"]]


def test_elenco_parte_dai_soci_attivi(client, volontario, soci):
    client.force_login(volontario)
    risposta = client.get(reverse("soci"))
    assert nomi(risposta) == ["ROSSI", "ROSSINI"]
    rossi = risposta.context["pagina"][0]
    # 2 donazioni registrate + 10 pregresse; la presentazione del 01/06/2026 non conta
    assert (rossi.totale, rossi.ultima) == (12, data(2026, 2, 1))
    assert "<html" in risposta.text


@pytest.mark.parametrize(("parametri", "attesi"), [
    ({"stato": ""}, ["BIANCHI", "ROSSI", "ROSSINI", "VERDI"]),
    ({"stato": "ex_socio"}, ["BIANCHI"]),
    ({"stato": "non_socio"}, ["VERDI"]),
    ({"stato": "", "q": "ross"}, ["ROSSI", "ROSSINI"]),
    ({"stato": "", "q": "rossi anna"}, ["ROSSI"]),
    ({"stato": "", "q": "rssnna"}, ["ROSSI"]),
    ({"stato": "", "idoneita": "temp_sospeso"}, ["BIANCHI"]),
    ({"stato": "", "ultima_dal": "2026-01-01"}, ["ROSSI"]),
    ({"stato": "", "ultima_al": "2020-12-31"}, ["BIANCHI"]),
])
def test_filtri_dell_elenco(client, volontario, soci, parametri, attesi):
    client.force_login(volontario)
    assert nomi(client.get(reverse("soci"), parametri)) == attesi


def test_filtro_collaboratori_comprende_chi_dona(client, volontario, soci):
    Socio.objects.filter(pk=soci["rossi"].pk).update(collaboratore=True)
    client.force_login(volontario)
    risposta = client.get(reverse("soci"), {"stato": "collaboratori"})
    assert nomi(risposta) == ["ROSSI", "ROSSINI"]
    # il donatore che è anche collaboratore ha il secondo badge
    assert risposta.text.count(">collaboratore</span>") == 1


def test_ricerca_per_id_del_db(client, volontario, soci):
    client.force_login(volontario)
    assert nomi(client.get(reverse("soci"), {"stato": "", "q": str(soci["verdi"].pk)})) == ["VERDI"]


def test_data_non_valida_mostra_l_errore_e_applica_gli_altri_filtri(client, volontario, soci):
    client.force_login(volontario)
    risposta = client.get(reverse("soci"), {"stato": "ex_socio", "ultima_dal": "ieri"})
    assert nomi(risposta) == ["BIANCHI"]
    assert risposta.context["form"].errors["ultima_dal"]


def test_filtri_htmx_restituiscono_solo_la_tabella(client, volontario, soci):
    client.force_login(volontario)
    risposta = client.get(reverse("soci"), {"stato": "", "q": "verdi"}, headers={"HX-Request": "true"})
    assert "<html" not in risposta.text and "VERDI PAOLA" in risposta.text


def test_paginazione_conserva_i_filtri(client, volontario, soci, monkeypatch):
    monkeypatch.setattr("gestionale.soci.views.PER_PAGINA", 1)
    client.force_login(volontario)
    risposta = client.get(reverse("soci"), {"stato": "", "q": "ross"})
    assert "?stato=&amp;q=ross&amp;pagina=2" in risposta.text
    assert nomi(client.get(reverse("soci"), {"stato": "", "q": "ross", "pagina": 2})) == ["ROSSINI"]


def test_scheda_registra_la_consultazione_senza_dati_personali(client, volontario, soci):
    rossi = soci["rossi"]
    BenemerenzaAssegnata.objects.create(socio=rossi, benemerenza=Benemerenza.objects.get(codice=1),
                                        data=data(2020, 11, 1), fonte=Fonte.ASSOAVIS)
    client.force_login(volontario)
    risposta = client.get(reverse("socio", args=[rossi.pk]))

    assert risposta.status_code == 200
    for testo in [CF_ROSSI, "anna@esempio.it", "A Positivo", Benemerenza.objects.get(codice=1).nome, "01/11/2020"]:
        assert testo in risposta.text
    assert (risposta.context["registrate"], risposta.context["pregresse"], risposta.context["totale"]) == (2, 10, 12)
    # il volontario non vede il riquadro di gestione
    assert risposta.context["form"] is None and "Gestione (amministratori)" not in risposta.text
    evento = Evento.objects.get(azione="scheda_socio")
    assert (evento.utente, evento.oggetto) == (volontario, f"socio {rossi.pk}")
    assert CF_ROSSI not in str(evento.dettagli)


def test_scheda_mostra_l_iscrizione_di_assoweb_solo_se_non_usata(client, volontario, soci):
    client.force_login(volontario)
    Socio.objects.filter(pk=soci["rossi"].pk).update(data_iscrizione=data(2004, 3, 1),
                                                     data_iscrizione_assoweb=data(2018, 6, 1))
    assert "Iscrizione su AssoWeb" in client.get(reverse("socio", args=[soci["rossi"].pk])).text
    Socio.objects.filter(pk=soci["rossi"].pk).update(data_iscrizione_assoweb=data(2004, 3, 1))
    assert "Iscrizione su AssoWeb" not in client.get(reverse("socio", args=[soci["rossi"].pk])).text


def test_volontario_non_modifica(client, volontario, soci):
    client.force_login(volontario)
    risposta = client.post(reverse("socio", args=[soci["verdi"].pk]), {"stato": StatoSocio.COLLABORATORE})
    assert risposta.status_code == 403
    soci["verdi"].refresh_from_db()
    assert soci["verdi"].stato == ""


def test_amministratore_segna_un_collaboratore(client, amministratore, soci):
    verdi = soci["verdi"]
    client.force_login(amministratore)
    assert "Gestione (amministratori)" in client.get(reverse("socio", args=[verdi.pk])).text

    risposta = client.post(reverse("socio", args=[verdi.pk]), {"stato": StatoSocio.COLLABORATORE, "collaboratore": "on",
                                                               "data_iscrizione": "2026-10-30", "note": "consigliere"})
    assert risposta.status_code == 302
    verdi.refresh_from_db()
    assert (verdi.stato, verdi.collaboratore, verdi.data_iscrizione, verdi.note) == (
        StatoSocio.COLLABORATORE, True, data(2026, 10, 30), "consigliere")
    evento = Evento.objects.get(azione="modifica_socio")
    assert evento.dettagli == {"campi": ["stato", "data_iscrizione", "collaboratore", "note"]}


@pytest.mark.parametrize(("dati", "campo_errato"), [
    # lo stato collaboratore va con la spunta
    ({"stato": StatoSocio.COLLABORATORE}, "collaboratore"),
    # un collaboratore non cessa finché ha la spunta
    ({"stato": StatoSocio.EX_SOCIO, "collaboratore": "on", "data_cessazione": "2026-10-30",
      "causa_cessazione": CausaCessazione.DIMISSIONI}, "stato"),
    ({"stato": "", "collaboratore": "on"}, "stato"),
])
def test_stato_e_spunta_dei_collaboratori_coerenti(client, amministratore, soci, dati, campo_errato):
    client.force_login(amministratore)
    risposta = client.post(reverse("socio", args=[soci["verdi"].pk]), dati)
    assert risposta.status_code == 200
    assert set(risposta.context["form"].errors) == {campo_errato}


def test_donatore_segnato_collaboratore_resta_donatore(client, amministratore, soci):
    rossi = soci["rossi"]
    client.force_login(amministratore)
    client.post(reverse("socio", args=[rossi.pk]), {"stato": StatoSocio.DONATORE, "collaboratore": "on"})
    rossi.refresh_from_db()
    assert (rossi.stato, rossi.collaboratore) == (StatoSocio.DONATORE, True)


def test_cessazione_manuale_chiede_data_e_causa(client, amministratore, soci):
    rossi = soci["rossi"]
    client.force_login(amministratore)
    url = reverse("socio", args=[rossi.pk])

    risposta = client.post(url, {"stato": StatoSocio.EX_SOCIO})
    assert risposta.status_code == 200
    assert set(risposta.context["form"].errors) == {"data_cessazione", "causa_cessazione"}
    # il resto della pagina mostra i dati salvati, non quelli del form non valido
    assert risposta.context["socio"].stato == StatoSocio.DONATORE
    rossi.refresh_from_db()
    assert rossi.stato == StatoSocio.DONATORE

    client.post(url, {"stato": StatoSocio.EX_SOCIO, "data_cessazione": "2026-10-30",
                      "causa_cessazione": CausaCessazione.DIMISSIONI, "no_benemerenze": "on"})
    rossi.refresh_from_db()
    assert (rossi.stato, rossi.data_cessazione, rossi.causa_cessazione, rossi.no_benemerenze) == (
        StatoSocio.EX_SOCIO, data(2026, 10, 30), CausaCessazione.DIMISSIONI, True)


def test_riattivare_un_socio_toglie_la_cessazione(client, amministratore, soci):
    bianchi = soci["bianchi"]
    client.force_login(amministratore)
    client.post(reverse("socio", args=[bianchi.pk]), {"stato": StatoSocio.COLLABORATORE, "collaboratore": "on",
                                                      "data_cessazione": "2021-12-31", "causa_cessazione": 12})
    bianchi.refresh_from_db()
    assert (bianchi.stato, bianchi.data_cessazione, bianchi.causa_cessazione) == (StatoSocio.COLLABORATORE, None, None)


def test_soci_solo_con_il_permesso(client, db, soci):
    client.force_login(User.objects.create_user("senza_ruolo"))
    assert client.get(reverse("soci")).status_code == 403
    assert client.get(reverse("socio", args=[soci["rossi"].pk])).status_code == 403
    assert not Evento.objects.filter(azione="scheda_socio").exists()


def test_link_ai_soci_nel_log(db):
    pagina = Template("{% load testo %}{{ log|collega_soci }}").render(
        Context({"log": "Residenza cambiata\n  socio 12: l'indirizzo <da verificare>"}))
    assert f'<a href="{reverse("socio", args=[12])}">socio 12</a>' in pagina
    assert "&lt;da verificare&gt;" in pagina
