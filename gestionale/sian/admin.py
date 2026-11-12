from django.contrib import admin

from .models import InvioSian, InvioSianRiga


class InvioSianRigaInline(admin.TabularInline):
    model = InvioSianRiga
    extra = 0
    fields = ["codice_fiscale", "socio", "operazione", "tipo_socio", "data_cessazione", "causa_cessazione", "esito",
              "messaggio"]
    raw_id_fields = ["socio"]


@admin.register(InvioSian)
class InvioSianAdmin(admin.ModelAdmin):
    list_display = ["anno", "creato_il", "esecuzione"]
    list_filter = ["anno"]
    inlines = [InvioSianRigaInline]
