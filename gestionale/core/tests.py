import pytest
from django.contrib.auth.models import Group, User
from django.urls import reverse

from gestionale.core.models import Esecuzione, Evento, Sezione, StatoEsecuzione, TipoEsecuzione
from gestionale.core.ruoli import AMMINISTRATORE, VOLONTARIO

PASSWORD = "Prova-test-123"


@pytest.fixture
def volontario(db):
    utente = User.objects.create_user("volontario", password=PASSWORD)
    utente.groups.add(Group.objects.get(name=VOLONTARIO))
    return utente


def test_pagine_protette_dal_login(client, db):
    risposta = client.get(reverse("home"))
    assert risposta.status_code == 302
    assert risposta.url.startswith(reverse("login"))


def test_pagina_di_accesso_pubblica(client, db):
    assert client.get(reverse("login")).status_code == 200


def test_home_dopo_il_login(client, volontario):
    client.force_login(volontario)
    risposta = client.get(reverse("home"))
    assert risposta.status_code == 200
    assert "Soci attivi" in risposta.text
    assert f'href="{reverse("aggiorna_dati")}"' in risposta.text


def test_login_e_logout_registrati(client, volontario):
    client.post(reverse("login"), {"username": "volontario", "password": PASSWORD})
    client.post(reverse("logout"))
    assert list(Evento.objects.order_by("data_ora").values_list("azione", flat=True)) == ["login", "logout"]


def test_login_fallito_registrato_senza_password(client, db):
    client.post(reverse("login"), {"username": "intruso", "password": "sbagliata"})
    evento = Evento.objects.get()
    assert evento.azione == "login_fallito"
    assert evento.nome_utente == "intruso"
    assert "sbagliata" not in str(evento.dettagli)


def test_ruoli(db):
    volontario = set(Group.objects.get(name=VOLONTARIO).permissions.values_list("codename", flat=True))
    amministratore = set(Group.objects.get(name=AMMINISTRATORE).permissions.values_list("codename", flat=True))
    assert {"view_socio", "view_premiazione", "view_esecuzione", "avvia_aggiorna_dati", "avvia_posti_liberi"} <= volontario
    assert "avvia_invio_sian" not in volontario
    assert "change_socio" not in volontario
    assert {"change_socio", "add_donazione", "view_evento", "change_user", "add_premiazione", "view_inviosian"} <= amministratore
    assert "delete_evento" not in amministratore


def test_sezione_iniziale(db):
    assert Sezione.objects.get(codice="1529").nome_assoweb == "AVIS Comunale Altopiano di Asiago"


def test_esecuzione_parte_in_coda(db):
    esecuzione = Esecuzione.objects.create(tipo=TipoEsecuzione.POSTI_LIBERI, parametri={"giornata": "2026-11-15"})
    assert esecuzione.stato == StatoEsecuzione.IN_CODA
    assert esecuzione.parametri["giornata"] == "2026-11-15"


def test_pagine_admin(admin_client):
    from django.contrib import admin

    for modello in admin.site._registry:
        opzioni = modello._meta
        url = reverse(f"admin:{opzioni.app_label}_{opzioni.model_name}_changelist")
        assert admin_client.get(url).status_code == 200, url
