from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from gestionale.benemerenze import views as benemerenze_views
from gestionale.comunicazioni import views as comunicazioni_views
from gestionale.core import views as core_views
from gestionale.importazioni import views as importazioni_views
from gestionale.sian import views as sian_views
from gestionale.soci import views as soci_views
from gestionale.statistiche import views as statistiche_views

urlpatterns = [
    path("", core_views.home, name="home"),
    path("soci/", soci_views.elenco, name="soci"),
    path("soci/<int:pk>/", soci_views.scheda, name="socio"),
    path("statistiche/", statistiche_views.statistiche, name="statistiche"),
    path("statistiche/excel/", statistiche_views.esporta, name="statistiche_esporta"),
    path("benemerenze/", benemerenze_views.elenco, name="benemerenze"),
    path("benemerenze/nuova/", benemerenze_views.nuova, name="premiazione_nuova"),
    path("benemerenze/<int:pk>/", benemerenze_views.dettaglio, name="premiazione"),
    path("benemerenze/<int:pk>/modifica/", benemerenze_views.modifica, name="premiazione_modifica"),
    path("benemerenze/<int:pk>/calcola/", benemerenze_views.calcola, name="premiazione_calcola"),
    path("benemerenze/<int:pk>/soci/<int:socio>/esclusione/", benemerenze_views.escludi, name="premiazione_escludi"),
    path("benemerenze/<int:pk>/aggiungi/", benemerenze_views.aggiungi, name="premiazione_aggiungi"),
    path("benemerenze/<int:pk>/assegna/", benemerenze_views.assegna, name="premiazione_assegna"),
    path("benemerenze/<int:pk>/elimina/", benemerenze_views.elimina, name="premiazione_elimina"),
    path("benemerenze/<int:pk>/excel/", benemerenze_views.esporta, name="premiazione_esporta"),
    path("libro-soci/", sian_views.libro, name="libro_soci"),
    path("libro-soci/sian/", sian_views.stato_sian, name="libro_soci_stato"),
    path("libro-soci/excel/", sian_views.esporta, name="libro_soci_esporta"),
    path("libro-soci/invia/", sian_views.invia, name="libro_soci_invia"),
    path("libro-soci/prova/", sian_views.prova, name="libro_soci_prova"),
    path("libro-soci/invii/<int:pk>/", sian_views.dettaglio, name="invio_sian"),
    path("libro-soci/invii/<int:pk>/esito/", sian_views.aggiorna_esito, name="invio_sian_esito"),
    path("posti-liberi/", comunicazioni_views.posti_liberi, name="posti_liberi"),
    path("posti-liberi/invia/", comunicazioni_views.invia, name="posti_liberi_invia"),
    path("aggiorna-dati/", importazioni_views.aggiorna_dati, name="aggiorna_dati"),
    path("esecuzioni/<int:pk>/", core_views.esecuzione, name="esecuzione"),
    path("accesso/", auth_views.LoginView.as_view(redirect_authenticated_user=True), name="login"),
    path("uscita/", auth_views.LogoutView.as_view(), name="logout"),
    path("password/", auth_views.PasswordChangeView.as_view(), name="password_change"),
    path("password/fatto/", auth_views.PasswordChangeDoneView.as_view(), name="password_change_done"),
    path("admin/", admin.site.urls),
]
