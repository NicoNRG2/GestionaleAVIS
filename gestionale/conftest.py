import pytest
from django.contrib.auth.models import Group, User

from gestionale.core.ruoli import AMMINISTRATORE, VOLONTARIO


def _utente(nome, ruolo):
    utente = User.objects.create_user(nome)
    utente.groups.add(Group.objects.get(name=ruolo))
    return utente


@pytest.fixture
def volontario(db):
    """Utente con il ruolo Volontario, senza password: nei test si usa client.force_login."""
    return _utente("volontario", VOLONTARIO)


@pytest.fixture
def amministratore(db):
    """Utente con il ruolo Amministratore (non superuser), senza password."""
    return _utente("amministratore", AMMINISTRATORE)


@pytest.fixture
def cartelle(settings, tmp_path):
    """Download e screenshot dei portali in una cartella temporanea: i test non scrivono in download/."""
    settings.CARTELLA_DOWNLOAD = tmp_path
    settings.PORTALI_CARTELLA_ERRORI = tmp_path / "errori"
    return tmp_path
