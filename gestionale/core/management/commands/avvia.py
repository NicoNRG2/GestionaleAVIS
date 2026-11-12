"""Avvia il gestionale su questo PC quando serve (README §5).

Avvia PostgreSQL con docker compose, applica le migrazioni, lancia web (waitress, solo su 127.0.0.1) e worker con
DJANGO_DEBUG=0 e apre il browser. Con Ctrl+C aspetta che finiscano i job attivi, ferma web e worker, fa il backup
del DB e, se l'aveva avviato lui, ferma PostgreSQL.

Web e worker stanno in un gruppo di processi proprio, così Ctrl+C arriva solo a questo comando: il worker non si
ferma mai a metà di un invio al SIAN o di un SMS, a meno di un secondo Ctrl+C.
"""

import os
import signal
import socket
import subprocess
import sys
import time
import webbrowser

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from gestionale.core.backup import SERVIZIO, compose
from gestionale.core.models import STATI_ATTIVI, Esecuzione
from gestionale.core.worker import annulla_in_coda

INDIRIZZO = "127.0.0.1"
# secondi di attesa perché il web risponda
ATTESA_WEB = 30


def _docker(*argomenti):
    try:
        risultato = subprocess.run(compose(*argomenti), capture_output=True, text=True)
    except FileNotFoundError:
        raise CommandError("Il comando docker non c'è: installa e avvia Docker Desktop")
    if risultato.returncode:
        righe = risultato.stderr.strip().splitlines()
        raise CommandError(f"Docker non risponde: avvia Docker Desktop e riprova ({righe[-1] if righe else ''})")
    return risultato.stdout


def _in_ascolto(porta):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((INDIRIZZO, porta)) == 0


def _avvia(comando):
    gruppo = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
              else {"start_new_session": True})
    return subprocess.Popen(comando, cwd=settings.BASE_DIR, env={**os.environ, "DJANGO_DEBUG": "0"}, **gruppo)


def _ferma(processi):
    for processo in processi.values():
        if processo.poll() is None:
            if os.name == "nt":
                # anche i processi figli: python della .venv è un launcher che avvia l'interprete vero
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(processo.pid)], capture_output=True)
            else:
                processo.terminate()
    for processo in processi.values():
        try:
            processo.wait(timeout=10)
        except subprocess.TimeoutExpired:
            processo.kill()


class Command(BaseCommand):
    help = ("Avvia il gestionale su questo PC (PostgreSQL, web e worker) e apre il browser. Con Ctrl+C lo chiude "
            "e fa il backup del DB.")

    def add_arguments(self, parser):
        parser.add_argument("--porta", type=int, default=8000, help="porta del web (default: 8000)")
        parser.add_argument("--senza-browser", action="store_true", help="non apre il browser")

    def handle(self, *args, porta, senza_browser, **opzioni):
        if hasattr(signal, "SIGBREAK"):
            # su Windows anche Ctrl+Interr chiude in modo ordinato, come Ctrl+C
            signal.signal(signal.SIGBREAK, signal.default_int_handler)
        if _in_ascolto(porta):
            raise CommandError(f"La porta {porta} è già occupata: il gestionale o runserver è già avviato?")
        db_gia_avviato = SERVIZIO in _docker("ps", "--status", "running", "--services").split()
        self.stdout.write("Avvio di PostgreSQL e aggiornamento del DB...")
        _docker("up", "--detach", "--wait", SERVIZIO)
        call_command("migrate", interactive=False, verbosity=0)
        if annullate := annulla_in_coda():
            self.stdout.write(self.style.WARNING(f"Esecuzioni rimaste in coda da una sessione precedente, "
                                                 f"annullate: {annullate}"))

        processi = {}
        try:
            processi["web"] = _avvia([sys.executable, "-m", "waitress", f"--listen={INDIRIZZO}:{porta}",
                                      "gestionale.wsgi:application"])
            processi["worker"] = _avvia([sys.executable, "manage.py", "worker"])
            self._attendi_web(porta, processi["web"])
            url = f"http://{INDIRIZZO}:{porta}/"
            self.stdout.write(self.style.SUCCESS(f"\nGestionale avviato: {url}"))
            self.stdout.write("Per chiuderlo premi Ctrl+C in questa finestra. Non chiudere la finestra con la X: "
                              "salterebbe il backup.\n")
            if not senza_browser:
                webbrowser.open(url)
            self._resta_acceso(processi)
        finally:
            _ferma(processi)

        self.stdout.write("Web e worker fermati. Backup del DB...")
        call_command("backup")
        if not db_gia_avviato:
            _docker("stop", SERVIZIO)
            self.stdout.write("PostgreSQL fermato")
        self.stdout.write(self.style.SUCCESS("Gestionale chiuso"))

    def _attendi_web(self, porta, web):
        fine = time.monotonic() + ATTESA_WEB
        while not _in_ascolto(porta):
            if web.poll() is not None:
                raise CommandError(f"Il web non è partito (codice {web.returncode}): vedi i messaggi sopra")
            if time.monotonic() > fine:
                raise CommandError(f"Il web non risponde sulla porta {porta} dopo {ATTESA_WEB} secondi")
            time.sleep(0.3)

    def _resta_acceso(self, processi):
        try:
            while not (fermi := [nome for nome, processo in processi.items() if processo.poll() is not None]):
                time.sleep(1)
            self.stderr.write(f"Il {fermi[0]} si è fermato (codice {processi[fermi[0]].returncode}): chiudo il "
                              "gestionale")
        except KeyboardInterrupt:
            self.stdout.write("\nChiusura del gestionale...")
        self._attendi_job(processi["worker"])

    def _attendi_job(self, worker):
        """Aspetta che il worker finisca i job in coda e in corso, se è ancora vivo."""
        avvisato = False
        try:
            while worker.poll() is None and Esecuzione.objects.filter(stato__in=STATI_ATTIVI).exists():
                if not avvisato:
                    self.stdout.write(self.style.WARNING("C'è un job attivo: aspetto che finisca. Premi di nuovo "
                                                         "Ctrl+C per chiudere subito, interrompendolo."))
                    avvisato = True
                time.sleep(2)
        except KeyboardInterrupt:
            self.stderr.write("Chiusura forzata: il job risulterà interrotto al prossimo avvio del worker")
