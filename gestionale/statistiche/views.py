"""Pagina delle statistiche ed export Excel (README M3)."""

from django import forms
from django.contrib.auth.decorators import permission_required
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from gestionale.core.eventi import registra
from gestionale.core.forms import FormBootstrap
from gestionale.core.models import Sezione

from . import calcolo, excel

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class SceltaAnno(FormBootstrap, forms.Form):
    anno = forms.TypedChoiceField(coerce=int)

    def __init__(self, *args, anni, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["anno"].choices = [(a, a) for a in anni]


def _anno(request):
    """Form e anno scelto; senza parametri, o con un anno non valido, l'anno in corso."""
    oggi = timezone.localdate()
    form = SceltaAnno(request.GET or {"anno": oggi.year}, anni=calcolo.anni_disponibili(oggi))
    return form, form.cleaned_data["anno"] if form.is_valid() else oggi.year, oggi


@permission_required("soci.view_donazione", raise_exception=True)
def statistiche(request):
    """Le richieste HTMX (anno cambiato) ricevono solo le statistiche dell'anno, senza il selettore."""
    form, anno, oggi = _anno(request)
    contesto = {"form": form, **calcolo.statistiche(anno, oggi)}
    template = "statistiche/_anno.html" if request.headers.get("HX-Request") else "statistiche/statistiche.html"
    return render(request, template, contesto)


@permission_required("soci.view_donazione", raise_exception=True)
def esporta(request):
    _, anno, oggi = _anno(request)
    dati = calcolo.statistiche(anno, oggi)
    sezione = Sezione.objects.order_by("codice").first()
    intestazione = (f"{sezione.nome if sezione else 'AVIS'}, anno {anno}: "
                    f"soci donatori al {dati['riferimento']:%d/%m/%Y}")
    registra("export_statistiche", request, oggetto=f"statistiche {anno}")
    risposta = HttpResponse(excel.cartella(dati["tabelle"].values(), intestazione), content_type=XLSX)
    risposta["Content-Disposition"] = f'attachment; filename="statistiche_{anno}.xlsx"'
    return risposta
