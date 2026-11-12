from django.apps import AppConfig
from django.db.models.signals import post_migrate


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "gestionale.core"

    def ready(self):
        from .eventi import collega_segnali
        from .ruoli import aggiorna_ruoli

        collega_segnali()
        post_migrate.connect(aggiorna_ruoli, dispatch_uid="aggiorna_ruoli")
