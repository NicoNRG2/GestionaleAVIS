"""Test del worker dei job (core/worker.py) e della pagina di un'esecuzione, con job di prova."""

import datetime
import re
import threading
from io import StringIO

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from gestionale.core import worker
from gestionale.core.models import Esecuzione, StatoEsecuzione, TipoEsecuzione
from gestionale.core.worker import annulla_in_coda, esegui, esegui_in_thread, prossima, recupera_interrotte

TIPO = TipoEsecuzione.POSTI_LIBERI
CF = "RSSNNA80A41A001X"


def job_riuscito(esecuzione, log):
    log("primo passo", "  dettaglio")
    log(f"giornata {esecuzione.parametri['giornata']}")


def job_fallito(esecuzione, log):
    log("primo passo")
    # come gli errori di PostgreSQL: i valori dei campi stanno nelle righe dopo la prima
    dettaglio = f"DETAIL: Key (codice_fiscale)=({CF}) already exists."
    raise ValueError("valore duplicato\n" + dettaglio)


@pytest.fixture
def job(monkeypatch):
    """Sostituisce il job dei posti liberi con la funzione di prova `nome` di questo modulo."""
    def imposta(nome):
        monkeypatch.setitem(worker.JOB, TIPO, f"{__name__}.{nome}")
    return imposta


def nuova(**campi):
    return Esecuzione.objects.create(tipo=TIPO, parametri={"giornata": "2026-11-15"}, **campi)


def test_esecuzione_completata_con_il_log(db, job):
    job("job_riuscito")
    esecuzione = nuova()

    assert prossima() == esecuzione
    esecuzione.refresh_from_db()
    assert esecuzione.stato == StatoEsecuzione.IN_CORSO and esecuzione.iniziata_il
    esegui(esecuzione)

    esecuzione.refresh_from_db()
    assert esecuzione.stato == StatoEsecuzione.COMPLETATA and esecuzione.terminata_il
    righe = esecuzione.log.splitlines()
    assert [riga[9:] for riga in righe] == ["primo passo", "  dettaglio", "giornata 2026-11-15"]
    # l'ora solo sulla prima riga di ogni chiamata
    assert re.match(r"\d\d:\d\d:\d\d primo passo", righe[0]) and righe[1].startswith(" " * 9)
    assert re.fullmatch(r"\d+:\d\d", esecuzione.durata)


def test_esecuzione_fallita_senza_i_dati_dell_errore(db, job, caplog):
    job("job_fallito")
    esecuzione = nuova()
    esegui(prossima())

    esecuzione.refresh_from_db()
    assert esecuzione.stato == StatoEsecuzione.FALLITA and esecuzione.terminata_il
    assert esecuzione.log.splitlines()[-1].endswith("Errore: valore duplicato")
    assert CF not in esecuzione.log
    assert "valore duplicato" in caplog.text and CF not in caplog.text


def test_coda_in_ordine_di_arrivo(db):
    vecchia, recente = nuova(), nuova()
    nuova(stato=StatoEsecuzione.COMPLETATA)
    assert [prossima(), prossima(), prossima()] == [vecchia, recente, None]


def test_comando_worker(db, job):
    job("job_riuscito")
    interrotta = nuova(stato=StatoEsecuzione.IN_CORSO)
    in_coda = [nuova(), nuova()]

    uscita = StringIO()
    call_command("worker", una_volta=True, stdout=uscita)

    interrotta.refresh_from_db()
    assert interrotta.stato == StatoEsecuzione.FALLITA and "Interrotta" in interrotta.log
    assert {e.stato for e in Esecuzione.objects.filter(pk__in=[e.pk for e in in_coda])} == {StatoEsecuzione.COMPLETATA}
    assert "Esecuzioni interrotte segnate come fallite: 1" in uscita.getvalue()


def test_recupera_interrotte_non_tocca_le_altre(db):
    nuova()
    nuova(stato=StatoEsecuzione.COMPLETATA)
    assert recupera_interrotte() == 0
    assert not Esecuzione.objects.filter(stato=StatoEsecuzione.FALLITA).exists()


def test_annulla_in_coda_solo_quelle_in_coda(db):
    in_coda = nuova()
    in_corso = nuova(stato=StatoEsecuzione.IN_CORSO)
    nuova(stato=StatoEsecuzione.COMPLETATA)

    assert annulla_in_coda() == 1
    in_coda.refresh_from_db()
    in_corso.refresh_from_db()
    assert in_coda.stato == StatoEsecuzione.FALLITA and in_coda.terminata_il
    assert "sessione precedente" in in_coda.log
    assert in_corso.stato == StatoEsecuzione.IN_CORSO


def test_esegui_in_thread_passa_i_messaggi_e_il_risultato():
    def lavoro(a, b, avanza):
        avanza("inizio")
        avanza("fine")
        return threading.get_ident(), a + b

    righe = []
    thread, somma = esegui_in_thread(righe.append, lavoro, 2, b=3)
    assert thread != threading.get_ident()
    assert (somma, righe) == (5, ["inizio", "fine"])


def test_esegui_in_thread_rilancia_l_errore():
    def lavoro(avanza):
        avanza("prima")
        raise ValueError("rotto")

    righe = []
    with pytest.raises(ValueError, match="rotto"):
        esegui_in_thread(righe.append, lavoro)
    assert righe == ["prima"]


def test_riquadro_si_aggiorna_finche_l_esecuzione_e_attiva(client, volontario):
    esecuzione = nuova()
    client.force_login(volontario)
    url = reverse("esecuzione", args=[esecuzione.pk])

    risposta = client.get(url, headers={"HX-Request": "true"})
    assert 'hx-trigger="every 2s"' in risposta.text
    assert "HX-Refresh" not in risposta.headers

    Esecuzione.objects.filter(pk=esecuzione.pk).update(stato=StatoEsecuzione.COMPLETATA)
    risposta = client.get(url, headers={"HX-Request": "true"})
    assert risposta.headers["HX-Refresh"] == "true"
    assert "hx-trigger" not in risposta.text

    assert client.get(url).status_code == 200


def test_avviso_se_il_worker_non_prende_l_esecuzione(client, volontario):
    esecuzione = nuova()
    client.force_login(volontario)
    url = reverse("esecuzione", args=[esecuzione.pk])
    assert "forse non è avviato" not in client.get(url).text

    Esecuzione.objects.filter(pk=esecuzione.pk).update(creata_il=timezone.now() - datetime.timedelta(minutes=1))
    assert "forse non è avviato" in client.get(url).text


def test_esecuzione_solo_con_il_permesso(client, db):
    client.force_login(User.objects.create_user("senza_ruolo"))
    assert client.get(reverse("esecuzione", args=[nuova().pk])).status_code == 403
