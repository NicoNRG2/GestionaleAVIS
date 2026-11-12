"""Ruoli dell'applicativo come gruppi Django, ricreati a ogni migrate.

Volontario: consulta i dati e prepara le premiazioni. Amministratore: gestisce tutti i dati e gli utenti.
Le autorizzazioni sulle azioni (import, invii, assegnazioni) si aggiungono qui con i rispettivi moduli.
"""

from django.contrib.auth.models import Group, Permission

VOLONTARIO = "Volontario"
AMMINISTRATORE = "Amministratore"

APP_GESTIONALE = ["core", "soci", "benemerenze", "sian"]

PERMESSI_VOLONTARIO = [
    "soci.view_socio",
    "soci.view_donazione",
    "soci.view_tipodonazione",
    "benemerenze.view_benemerenza",
    "benemerenze.view_benemerenzaassegnata",
    "benemerenze.view_premiazione",
    "benemerenze.view_premiato",
    # preparano le premiazioni, ma solo l'Amministratore le assegna (benemerenze.assegna_premiazione)
    "benemerenze.add_premiazione",
    "benemerenze.change_premiazione",
    "benemerenze.delete_premiazione",
    "core.view_sezione",
    "core.view_esecuzione",
    "core.avvia_aggiorna_dati",
    "core.avvia_posti_liberi",
]

PERMESSI_UTENTI = ["auth.view_user", "auth.add_user", "auth.change_user", "auth.view_group"]


def _permessi(codici):
    for codice in codici:
        app_label, codename = codice.split(".")
        permesso = Permission.objects.filter(content_type__app_label=app_label, codename=codename).first()
        if permesso:
            yield permesso


def aggiorna_ruoli(**kwargs):
    """Handler di post_migrate: idempotente, assegna i permessi già creati da Django."""
    volontario, _ = Group.objects.get_or_create(name=VOLONTARIO)
    volontario.permissions.add(*_permessi(PERMESSI_VOLONTARIO))

    amministratore, _ = Group.objects.get_or_create(name=AMMINISTRATORE)
    # il registro eventi si consulta e basta (vedi EventoAdmin)
    permessi_app = Permission.objects.filter(content_type__app_label__in=APP_GESTIONALE).exclude(
        content_type__model="evento", codename__in=["add_evento", "change_evento", "delete_evento"]
    )
    amministratore.permissions.add(*permessi_app, *_permessi(PERMESSI_UTENTI))
