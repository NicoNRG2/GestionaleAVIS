from django.contrib import admin

from .models import Benemerenza, BenemerenzaAssegnata, Premiato, Premiazione


@admin.register(Benemerenza)
class BenemerenzaAdmin(admin.ModelAdmin):
    list_display = ["codice", "nome", "per_collaboratori", "ordine", "colonna_flag_assoavis", "colonna_data_assoavis"]


@admin.register(BenemerenzaAssegnata)
class BenemerenzaAssegnataAdmin(admin.ModelAdmin):
    list_display = ["socio", "benemerenza", "data", "fonte"]
    list_filter = ["benemerenza", "fonte"]
    search_fields = ["socio__cognome", "socio__nome", "socio__codice_fiscale"]
    date_hierarchy = "data"
    list_select_related = ["socio", "benemerenza"]
    raw_id_fields = ["socio"]


class PremiatoInline(admin.TabularInline):
    model = Premiato
    extra = 0
    fields = ["socio", "benemerenza", "donazioni_valide", "donazioni_effettive"]
    raw_id_fields = ["socio"]


@admin.register(Premiazione)
class PremiazioneAdmin(admin.ModelAdmin):
    list_display = ["descrizione", "data_consegna", "data_riferimento", "stato", "codice_assoavis"]
    list_filter = ["stato"]
    date_hierarchy = "data_consegna"
    inlines = [PremiatoInline]
