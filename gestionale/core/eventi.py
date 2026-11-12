"""Scrittura del registro eventi e collegamento ai segnali di login/logout."""

from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed

from .models import Evento


def registra(azione, request=None, utente=None, oggetto="", nome_utente="", **dettagli):
    if utente is None and request is not None and request.user.is_authenticated:
        utente = request.user
    Evento.objects.create(
        utente=utente,
        nome_utente=utente.get_username() if utente else nome_utente,
        azione=azione,
        oggetto=oggetto,
        dettagli=dettagli,
        indirizzo_ip=request.META.get("REMOTE_ADDR") if request is not None else None,
    )


def _login(sender, request, user, **kwargs):
    registra("login", request, user)


def _logout(sender, request, user, **kwargs):
    if user is not None:
        registra("logout", request, user)


def _login_fallito(sender, credentials, request=None, **kwargs):
    # credentials arriva già senza password (Django la oscura)
    registra("login_fallito", request, nome_utente=credentials.get("username", "")[:150])


def collega_segnali():
    user_logged_in.connect(_login, dispatch_uid="evento_login")
    user_logged_out.connect(_logout, dispatch_uid="evento_logout")
    user_login_failed.connect(_login_fallito, dispatch_uid="evento_login_fallito")
