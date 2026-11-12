from django.contrib import admin

from .models import Esecuzione, Evento, Sezione

admin.site.site_header = "Gestionale AVIS - amministrazione"
admin.site.site_title = "Gestionale AVIS"
admin.site.index_title = "Amministrazione"


@admin.register(Sezione)
class SezioneAdmin(admin.ModelAdmin):
    list_display = ["codice", "nome", "nome_assoweb"]


@admin.register(Evento)
class EventoAdmin(admin.ModelAdmin):
    """Il registro eventi si consulta soltanto: nessuno può modificarlo o cancellarlo."""

    list_display = ["data_ora", "nome_utente", "azione", "oggetto", "indirizzo_ip"]
    list_filter = ["azione"]
    search_fields = ["nome_utente", "oggetto"]
    date_hierarchy = "data_ora"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Esecuzione)
class EsecuzioneAdmin(admin.ModelAdmin):
    list_display = ["creata_il", "tipo", "stato", "utente", "iniziata_il", "terminata_il"]
    list_filter = ["tipo", "stato"]
    date_hierarchy = "creata_il"
    readonly_fields = ["creata_il", "iniziata_il", "terminata_il"]
