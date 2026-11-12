from django.contrib.auth.decorators import permission_required
from django.db.models import Count, Max
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from gestionale.soci.models import Donazione, Socio, StatoSocio

from .models import Esecuzione

# moduli dell'applicativo nella home: (titolo, descrizione, nome della pagina)
MODULI = [
    ("Aggiorna dati", "Scarica i dati da AssoWeb e li importa nel database.", "aggiorna_dati"),
    ("Soci", "Elenco e scheda dei soci con donazioni e benemerenze.", "soci"),
    ("Statistiche", "Donatori e donazioni per anno, con export Excel.", "statistiche"),
    ("Benemerenze", "Calcolo degli aventi diritto e gestione delle premiazioni.", "benemerenze"),
    ("Libro soci SIAN", "Estrazione, controlli e invio del libro soci annuale.", "libro_soci"),
    ("Posti liberi", "SMS da AssoWeb ai donatori per una giornata con posti disponibili.", "posti_liberi"),
]


def home(request):
    anno = timezone.localdate().year
    per_stato = dict(Socio.objects.values_list("stato").annotate(n=Count("id")))
    contesto = {
        "anno": anno,
        "soci_attivi": per_stato.get(StatoSocio.DONATORE, 0) + per_stato.get(StatoSocio.COLLABORATORE, 0),
        "donatori_attivi": per_stato.get(StatoSocio.DONATORE, 0),
        # tutti i collaboratori, anche quelli che donano e sono contati tra i donatori
        "collaboratori": Socio.objects.filter(collaboratore=True).count(),
        "donazioni_anno": Donazione.objects.filter(data__year=anno, tipo__e_donazione=True).count(),
        "ultimo_aggiornamento": Socio.objects.aggregate(m=Max("aggiornato_assoweb_il"))["m"],
        "moduli": MODULI,
    }
    return render(request, "core/home.html", contesto)


@permission_required("core.view_esecuzione", raise_exception=True)
def esecuzione(request, pk):
    """Esito e log di un'esecuzione.

    Le richieste HTMX ricevono solo il riquadro, che si aggiorna ogni 2 secondi finché l'esecuzione è attiva;
    quando termina, la pagina intera si ricarica per mostrare l'esito e riabilitare i bottoni.
    """
    esecuzione = get_object_or_404(Esecuzione.objects.select_related("utente"), pk=pk)
    if not request.headers.get("HX-Request"):
        return render(request, "core/esecuzione.html", {"esecuzione": esecuzione})
    risposta = render(request, "core/_esecuzione.html", {"esecuzione": esecuzione})
    if not esecuzione.attiva:
        risposta["HX-Refresh"] = "true"
    return risposta
