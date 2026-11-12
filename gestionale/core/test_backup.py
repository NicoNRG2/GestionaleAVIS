"""Test di backup e ripristino (core/backup.py) con un docker compose finto al posto di subprocess.run."""

import datetime
import subprocess
from io import StringIO

import pytest
from django.core.management import call_command

from gestionale.core import backup
from gestionale.core.models import Evento

ADESSO = datetime.datetime(2026, 11, 3, 18, 30, 5)
CF = "RSSNNA80A41A001X"


class DockerFinto:
    """Registra i programmi lanciati nel container e risponde come PostgreSQL.

    `errori` associa il nome di un programma al suo stderr: quel programma fallisce.
    """

    def __init__(self, connessioni=0, errori=None):
        self.programmi = []
        self.connessioni = connessioni
        self.errori = errori or {}

    def __call__(self, comando, stdin=None, stdout=None, stderr=None):
        assert comando[:3] == ["docker", "compose", "--project-directory"]
        assert comando[4:7] == ["exec", "-T", backup.SERVIZIO]
        argomenti = comando[7:]
        self.programmi.append(argomenti)
        programma = argomenti[0]
        if programma in self.errori:
            return subprocess.CompletedProcess(comando, 1, b"", self.errori[programma].encode())
        uscita = b""
        if programma == "pg_dump":
            stdout.write(b"PGDMP dati")
        elif programma == "psql" and "pg_stat_activity" in argomenti[-1]:
            uscita = f"{self.connessioni}\n".encode()
        elif programma == "psql":
            uscita = b"soci_socio;3\ncore_evento;7\n"
        return subprocess.CompletedProcess(comando, 0, uscita, b"")

    def lanciati(self):
        return [argomenti[0] for argomenti in self.programmi]


@pytest.fixture
def docker(monkeypatch, settings, tmp_path):
    settings.CARTELLA_BACKUP = tmp_path / "backup"
    settings.BACKUP_DA_TENERE = 3

    def imposta(**opzioni):
        finto = DockerFinto(**opzioni)
        monkeypatch.setattr(backup.subprocess, "run", finto)
        return finto
    return imposta


def crea_backup_vecchi(cartella, n):
    cartella.mkdir(parents=True, exist_ok=True)
    for giorno in range(1, n + 1):
        (cartella / backup.nome_backup(datetime.datetime(2026, 1, giorno))).write_bytes(b"x")


def test_nome_del_backup_con_data_e_ora():
    assert backup.nome_backup(ADESSO) == "gestionale_20261103_183005.dump"


def test_pulisci_tiene_gli_ultimi(tmp_path):
    crea_backup_vecchi(tmp_path, 5)
    (tmp_path / "appunti.txt").write_text("non è un backup")

    cancellati = backup.pulisci(tmp_path, 2)

    assert [f.name for f in cancellati] == ["gestionale_20260101_000000.dump", "gestionale_20260102_000000.dump",
                                            "gestionale_20260103_000000.dump"]
    assert [f.name for f in backup.presenti(tmp_path)] == ["gestionale_20260104_000000.dump",
                                                           "gestionale_20260105_000000.dump"]
    assert (tmp_path / "appunti.txt").exists()


def test_pulisci_con_zero_li_tiene_tutti(tmp_path):
    crea_backup_vecchi(tmp_path, 4)
    assert backup.pulisci(tmp_path, 0) == []
    assert len(backup.presenti(tmp_path)) == 4


def test_crea_salva_il_dump_e_cancella_i_vecchi(docker, settings):
    finto = docker()
    crea_backup_vecchi(settings.CARTELLA_BACKUP, 3)

    file, cancellati = backup.crea(adesso=ADESSO)

    assert file == settings.CARTELLA_BACKUP / "gestionale_20261103_183005.dump"
    assert file.read_bytes() == b"PGDMP dati"
    assert len(cancellati) == 1 and len(backup.presenti(settings.CARTELLA_BACKUP)) == 3
    assert finto.programmi == [["pg_dump", "-U", settings.DATABASES["default"]["USER"], "-d",
                                settings.DATABASES["default"]["NAME"], "--format=custom"]]


def test_crea_fallito_non_lascia_file_e_non_cancella_i_vecchi(docker, settings):
    docker(errori={"pg_dump": 'service "db" is not running'})
    crea_backup_vecchi(settings.CARTELLA_BACKUP, 3)

    with pytest.raises(backup.ErroreBackup, match='service "db" is not running'):
        backup.crea(adesso=ADESSO)

    assert [f.name for f in settings.CARTELLA_BACKUP.iterdir()] == [f.name for f in
                                                                    backup.presenti(settings.CARTELLA_BACKUP)]
    assert len(backup.presenti(settings.CARTELLA_BACKUP)) == 3


def test_errore_con_una_sola_riga(docker):
    # come pg_restore: la riga con l'errore non è la prima, i valori dei campi stanno in quelle dopo
    docker(errori={"pg_dump": "pg_restore: while PROCESSING TOC:\npg_restore: error: duplicate key value\n"
                              f"DETAIL: Key (codice_fiscale)=({CF}) already exists."})
    with pytest.raises(backup.ErroreBackup) as errore:
        backup.crea(adesso=ADESSO)
    assert str(errore.value) == "pg_restore: error: duplicate key value"


def test_comando_backup_registra_l_evento(db, docker):
    docker()
    uscita = StringIO()
    call_command("backup", stdout=uscita)

    evento = Evento.objects.get(azione="backup")
    assert evento.oggetto.startswith("gestionale_") and evento.oggetto.endswith(".dump")
    assert "Backup salvato" in uscita.getvalue()


def test_prova_ripristino_in_un_db_temporaneo(docker, settings, tmp_path):
    finto = docker()
    file = tmp_path / "gestionale_20261103_183005.dump"
    file.write_bytes(b"PGDMP dati")
    prova = f"{settings.DATABASES['default']['NAME']}_prova_ripristino"

    nel_backup, attuali = backup.prova_ripristino(file)

    assert nel_backup == attuali == {"soci_socio": 3, "core_evento": 7}
    assert finto.lanciati() == ["dropdb", "createdb", "pg_restore", "psql", "psql", "dropdb"]
    assert finto.programmi[2][finto.programmi[2].index("-d") + 1] == prova
    assert finto.programmi[3][finto.programmi[3].index("-d") + 1] == prova
    assert finto.programmi[-1][-1] == prova


def test_prova_ripristino_fallita_cancella_il_db_temporaneo(docker, tmp_path):
    finto = docker(errori={"pg_restore": "pg_restore: error: input file is too short"})
    file = tmp_path / "rotto.dump"
    file.write_bytes(b"")

    with pytest.raises(backup.ErroreBackup, match="too short"):
        backup.prova_ripristino(file)
    assert finto.lanciati()[-1] == "dropdb"


def test_ripristino_rifiutato_con_connessioni_aperte(docker, tmp_path):
    finto = docker(connessioni=2)
    file = tmp_path / "gestionale_20261103_183005.dump"
    file.write_bytes(b"PGDMP dati")

    with pytest.raises(backup.ErroreBackup, match="2 connessioni aperte"):
        backup.ripristina(file)
    assert "pg_restore" not in finto.lanciati() and "pg_dump" not in finto.lanciati()


def test_ripristino_con_backup_di_sicurezza_prima(docker, settings, tmp_path):
    finto = docker()
    # il file da ripristinare è il più vecchio: il backup di sicurezza non deve cancellarlo
    crea_backup_vecchi(settings.CARTELLA_BACKUP, 3)
    file = backup.presenti(settings.CARTELLA_BACKUP)[0]

    sicurezza = backup.ripristina(file)

    assert file.exists() and sicurezza.exists()
    assert finto.lanciati() == ["psql", "pg_dump", "pg_restore"]
    restore = finto.programmi[-1]
    assert {"--clean", "--if-exists", "--single-transaction", "--exit-on-error"} <= set(restore)
