"""Backup e ripristino del DB (README §7).

PostgreSQL gira nel servizio `db` di docker compose: `pg_dump`, `pg_restore` e `psql` si lanciano dentro il
container con `docker compose exec`, così sul PC non servono i client di PostgreSQL. I backup sono nel formato
custom di pg_dump (compresso) e contengono dati personali e sanitari: non se ne legge mai il contenuto, la prova
di ripristino confronta solo il numero di righe delle tabelle.
"""

import subprocess
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.utils import timezone

SERVIZIO = "db"
PREFISSO = "gestionale_"
ESTENSIONE = ".dump"


class ErroreBackup(Exception):
    pass


def compose(*argomenti):
    """Comando `docker compose` del progetto, qualunque sia la cartella corrente."""
    return ["docker", "compose", "--project-directory", str(settings.BASE_DIR), *argomenti]


def _db():
    db = settings.DATABASES["default"]
    return db["NAME"], db["USER"]


def _esegui(argomenti, *, stdin=None, stdout=subprocess.PIPE):
    """Lancia un programma di PostgreSQL nel container e ne restituisce l'output.

    In caso di errore solleva ErroreBackup con una sola riga: nelle successive PostgreSQL può riportare i valori
    dei campi, cioè dati personali.
    """
    try:
        risultato = subprocess.run(compose("exec", "-T", SERVIZIO, *argomenti), stdin=stdin, stdout=stdout,
                                   stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise ErroreBackup("il comando docker non c'è: Docker Desktop è installato?")
    if risultato.returncode:
        righe = risultato.stderr.decode(errors="replace").strip().splitlines()
        errore = next((r for r in righe if "error" in r.lower()), righe[0] if righe else "")
        raise ErroreBackup(errore.strip() or f"{argomenti[0]} terminato con il codice {risultato.returncode}")
    return risultato.stdout


def nome_backup(adesso):
    return f"{PREFISSO}{adesso:%Y%m%d_%H%M%S}{ESTENSIONE}"


def presenti(cartella):
    """I backup della cartella, dal più vecchio: il nome contiene data e ora."""
    return sorted(Path(cartella).glob(f"{PREFISSO}*{ESTENSIONE}"))


def pulisci(cartella, da_tenere):
    """Cancella i backup più vecchi oltre `da_tenere` (0 = li tiene tutti) e li restituisce."""
    vecchi = presenti(cartella)[:-da_tenere] if da_tenere > 0 else []
    for file in vecchi:
        file.unlink()
    return vecchi


def crea(cartella=None, da_tenere=None, adesso=None):
    """Salva il DB in un nuovo file della cartella dei backup; restituisce il file e i backup vecchi cancellati.

    pg_dump scrive in un file provvisorio, che prende il nome definitivo solo alla fine: un backup interrotto non
    conta tra quelli da tenere.
    """
    cartella = Path(cartella or settings.CARTELLA_BACKUP)
    cartella.mkdir(parents=True, exist_ok=True)
    file = cartella / nome_backup(adesso or timezone.localtime())
    provvisorio = file.with_suffix(".parziale")
    nome, utente = _db()
    try:
        with open(provvisorio, "wb") as uscita:
            _esegui(["pg_dump", "-U", utente, "-d", nome, "--format=custom"], stdout=uscita)
    except BaseException:
        provvisorio.unlink(missing_ok=True)
        raise
    provvisorio.replace(file)
    return file, pulisci(cartella, settings.BACKUP_DA_TENERE if da_tenere is None else da_tenere)


def conteggi(nome_db=None):
    """{tabella: righe} per le tabelle dei modelli Django, nel DB indicato (default: quello del gestionale)."""
    nome, utente = _db()
    tabelle = sorted({m._meta.db_table for m in apps.get_models(include_auto_created=True)})
    sql = " union all ".join(f"select '{t}', count(*) from {t}" for t in tabelle)
    uscita = _esegui(["psql", "-U", utente, "-d", nome_db or nome, "-A", "-t", "-F", ";", "-c", sql])
    return {t: int(n) for t, n in (riga.split(";") for riga in uscita.decode().splitlines() if riga)}


def prova_ripristino(file):
    """Ripristina il backup in un DB temporaneo e restituisce (righe nel backup, righe nel DB attuale).

    Il DB temporaneo si cancella alla fine, anche se il ripristino fallisce.
    """
    nome, utente = _db()
    prova = f"{nome}_prova_ripristino"
    _esegui(["dropdb", "-U", utente, "--if-exists", prova])
    _esegui(["createdb", "-U", utente, prova])
    try:
        with open(file, "rb") as ingresso:
            _esegui(["pg_restore", "-U", utente, "-d", prova, "--no-owner", "--exit-on-error"], stdin=ingresso)
        return conteggi(prova), conteggi()
    finally:
        _esegui(["dropdb", "-U", utente, "--if-exists", prova])


def altre_connessioni():
    """Connessioni al DB del gestionale oltre a quella di psql: web, worker o altri programmi aperti."""
    nome, utente = _db()
    sql = "select count(*) from pg_stat_activity where datname = current_database() and pid <> pg_backend_pid()"
    return int(_esegui(["psql", "-U", utente, "-d", nome, "-A", "-t", "-c", sql]).decode().strip())


def ripristina(file):
    """Sostituisce il contenuto del DB con il backup e restituisce il backup di sicurezza fatto prima.

    Il ripristino è una sola transazione: se fallisce, il DB resta com'era. Il backup di sicurezza non cancella
    i backup vecchi, perché tra quelli potrebbe esserci proprio il file da ripristinare.
    """
    if connessioni := altre_connessioni():
        raise ErroreBackup(f"ci sono {connessioni} connessioni aperte al DB: chiudi il gestionale (web e worker) "
                           "e gli altri programmi collegati, poi riprova")
    sicurezza, _ = crea(da_tenere=0)
    nome, utente = _db()
    with open(file, "rb") as ingresso:
        _esegui(["pg_restore", "-U", utente, "-d", nome, "--clean", "--if-exists", "--no-owner",
                 "--single-transaction", "--exit-on-error"], stdin=ingresso)
    return sicurezza
