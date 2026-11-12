"""Impostazioni del gestionale AVIS.

I valori sensibili e quelli che cambiano tra sviluppo e uso (`manage.py avvia`)
si leggono dalle variabili d'ambiente (file .env nella root del progetto). Le variabili già impostate
hanno la precedenza sul file: `avvia` le usa per lanciare web e worker con DJANGO_DEBUG=0.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env(nome, default=None):
    valore = os.environ.get(nome, default)
    if valore is None:
        raise ImproperlyConfigured(f"Variabile d'ambiente mancante: {nome}")
    return valore


DEBUG = env("DJANGO_DEBUG", "0") == "1"
SECRET_KEY = env("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in env("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "gestionale.core",
    "gestionale.soci",
    "gestionale.benemerenze",
    "gestionale.sian",
    "gestionale.importazioni",
    "gestionale.portali",
    "gestionale.statistiche",
    "gestionale.comunicazioni",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # file statici con DJANGO_DEBUG=0 (manage.py avvia)
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # tutte le pagine richiedono il login, salvo quelle marcate con @login_not_required
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "gestionale.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "gestionale" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "gestionale.wsgi.application"


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", "gestionale"),
        "USER": env("POSTGRES_USER", "gestionale"),
        "PASSWORD": env("POSTGRES_PASSWORD"),
        "HOST": env("POSTGRES_HOST", "127.0.0.1"),
        "PORT": env("POSTGRES_PORT", "5432"),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# storico AssoAVIS ripristinato in sola lettura: lo legge solo `manage.py importa_assoavis` sul PC di sviluppo
ASSOAVIS_ODBC = env("ASSOAVIS_ODBC", "") or (
    "DRIVER={ODBC Driver 18 for SQL Server};SERVER=(local);DATABASE=ASSOAVIS_STORICO;"
    "Trusted_Connection=yes;TrustServerCertificate=yes"
)

# CSV scaricati da AssoWeb (dati personali e sanitari): sovrascritti a ogni download
CARTELLA_DOWNLOAD = BASE_DIR / "download"

# backup del DB (pg_dump) fatti da `manage.py backup` e alla chiusura di `manage.py avvia`: contengono dati
# personali e sanitari, meglio una cartella su un disco cifrato o un supporto esterno
CARTELLA_BACKUP = Path(env("CARTELLA_BACKUP", "") or BASE_DIR / "backup")
# i backup più vecchi oltre questo numero si cancellano
BACKUP_DA_TENERE = int(env("BACKUP_DA_TENERE", "10"))

# Portali esterni pilotati con Playwright (gestionale.portali)
ASSOWEB_USERNAME = env("APP_USERNAME_AVIS", "")
ASSOWEB_PASSWORD = env("APP_PASSWORD_AVIS", "")
# filtri salvati su AssoWeb (Ricerca e convocazione) che scelgono chi riceve l'SMS dei posti liberi e della prova
ASSOWEB_FILTRO_POSTI_LIBERI = env("ASSOWEB_FILTRO_POSTI_LIBERI", "AvisAltopianoMessaggio")
ASSOWEB_FILTRO_PROVA = env("ASSOWEB_FILTRO_PROVA", "test")
# browser visibile di default: il firewall di AssoWeb blocca Chromium headless
PORTALI_HEADLESS = env("PORTALI_HEADLESS", "0") == "1"
# screenshot delle automazioni fallite: possono mostrare dati personali, per questo stanno in download/
PORTALI_CARTELLA_ERRORI = CARTELLA_DOWNLOAD / "errori"

# Web service del libro soci SIAN (gestionale.portali.sian). Il token di cooperazione applicativa vale solo in
# produzione: l'ambiente di test (https://sian.avis.it/test/sianwstest/RegistroSoci.asmx) lo rifiuta
SIAN_URL = env("SIAN_URL", "https://sian.avis.it/sianws/RegistroSoci.asmx")
SIAN_TOKEN = env("SIAN_TOKEN", "")


# Autenticazione e sessione
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# la sessione scade dopo 30 minuti di inattività e alla chiusura del browser
SESSION_COOKIE_AGE = 30 * 60
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"

# con HTTPS (reverse proxy) impostare DJANGO_HTTPS=1
if env("DJANGO_HTTPS", "0") == "1":
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
else:
    # manage.py avvia ascolta solo su 127.0.0.1: il traffico non esce dal PC, quindi niente HTTPS (README §7)
    SILENCED_SYSTEM_CHECKS = ["security.W004", "security.W008", "security.W012", "security.W016"]


LANGUAGE_CODE = "it"
TIME_ZONE = "Europe/Rome"
USE_I18N = True
USE_TZ = True


STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "gestionale" / "static"]
# con DJANGO_DEBUG=0 WhiteNoise serve i file direttamente dalle cartelle static: niente collectstatic
WHITENOISE_USE_FINDERS = True


# I log sono per eventi applicativi ed errori: non scriverci mai dati personali
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
