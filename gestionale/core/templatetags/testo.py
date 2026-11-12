import re

from django import template
from django.urls import reverse
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

RIFERIMENTO_SOCIO = re.compile(r"\bsocio (\d+)\b")


@register.filter
def collega_soci(testo):
    """Trasforma i riferimenti "socio <id>" delle anomalie degli import in link alla scheda del socio."""
    return mark_safe(RIFERIMENTO_SOCIO.sub(
        lambda trovato: f'<a href="{reverse("socio", args=[trovato[1]])}">{trovato[0]}</a>', escape(testo)))
