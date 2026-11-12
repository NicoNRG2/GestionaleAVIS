from django.contrib import admin

from gestionale.benemerenze.models import BenemerenzaAssegnata

from .models import Donazione, Socio, TipoDonazione


class DonazioneInline(admin.TabularInline):
    model = Donazione
    extra = 0
    fields = ["data", "tipo", "centro_prelievo", "fonte"]


class BenemerenzaAssegnataInline(admin.TabularInline):
    model = BenemerenzaAssegnata
    extra = 0
    fields = ["benemerenza", "data", "fonte"]


@admin.register(Socio)
class SocioAdmin(admin.ModelAdmin):
    list_display = ["cognome", "nome", "codice_fiscale", "tessera", "stato", "idoneita", "sezione"]
    list_filter = ["stato", "collaboratore", "idoneita", "sesso", "sezione"]
    search_fields = ["cognome", "nome", "codice_fiscale", "tessera", "codice_interno_assoavis", "codice_regionale"]
    readonly_fields = ["data_iscrizione_assoweb", "aggiornato_assoweb_il", "creato_il", "modificato_il"]
    inlines = [BenemerenzaAssegnataInline, DonazioneInline]
    fieldsets = [
        ("Identificazione", {"fields": ["sezione", "codice_fiscale", "tessera", "codice_interno_assoavis", "codice_regionale"]}),
        ("Anagrafica", {"fields": ["cognome", "nome", "sesso", "data_nascita", "luogo_nascita", "provincia_nascita"]}),
        ("Residenza e contatti", {"fields": ["indirizzo_residenza", "comune_residenza", "cap_residenza",
                                             "provincia_residenza", "cellulare", "telefono", "email"]}),
        ("Dati trasfusionali (da AssoWeb)", {"fields": ["gruppo_sanguigno", "rh", "fenotipo_rh", "kell", "idoneita",
                                                        "idoneita_dal", "idoneita_al", "stato_assoweb"]}),
        ("Dati associativi", {"fields": ["stato", "collaboratore", "data_iscrizione", "data_iscrizione_assoweb", "data_cessazione",
                                         "causa_cessazione", "no_benemerenze"]}),
        ("Donazioni pregresse", {"fields": ["donazioni_pregresse_si", "donazioni_pregresse_aferesi",
                                            "donazioni_pregresse_dopo_2004"]}),
        ("Altro", {"fields": ["note", "aggiornato_assoweb_il", "creato_il", "modificato_il"]}),
    ]


@admin.register(Donazione)
class DonazioneAdmin(admin.ModelAdmin):
    list_display = ["data", "socio", "tipo", "centro_prelievo", "fonte"]
    list_filter = ["tipo", "fonte"]
    search_fields = ["socio__cognome", "socio__nome", "socio__codice_fiscale"]
    date_hierarchy = "data"
    list_select_related = ["socio", "tipo"]
    raw_id_fields = ["socio"]


@admin.register(TipoDonazione)
class TipoDonazioneAdmin(admin.ModelAdmin):
    list_display = ["codice", "descrizione", "codice_assoavis", "contiene_emazie", "e_donazione"]
