"""Unione di un socio dello storico con il doppione creato dall'import AssoWeb.

L'import aggancia i soci per codice fiscale o per cognome, nome e data di nascita: se nello storico sono
sbagliati sia il CF sia la data di nascita (o il nome), crea una seconda scheda con i dati del portale e le
donazioni nuove. L'unione riporta tutto sulla scheda dello storico, che tiene il codice AssoAVIS, così i
prossimi import la trovano per codice fiscale e il doppione non si ricrea.
"""

from collections import Counter

from django.db.models import Min
from django.utils import timezone

from gestionale.soci.models import Donazione, Socio
from gestionale.soci.stati import aggiorna_stati

from .assoweb import CAMPI, iscrizione_da_assoweb

# dati di cui AssoWeb è la fonte master: si prendono dal doppione quando non sono vuoti, come fa l'import
CAMPI_ASSOWEB = ["codice_fiscale", "data_nascita", "tessera", *CAMPI.values()]
# l'idoneità si prende intera con le sue date, come in campi_da_riga
CAMPI_IDONEITA = ["idoneita", "idoneita_dal", "idoneita_al"]


class UnioneError(Exception):
    pass


def _collegati(doppione):
    """Righe di altre tabelle (benemerenze, premiazioni, invii SIAN, ...) che puntano al doppione."""
    trovati = []
    for relazione in Socio._meta.related_objects:
        if relazione.related_model is Donazione:
            continue
        n = relazione.related_model._default_manager.filter(**{relazione.field.name: doppione}).count()
        if n:
            trovati.append(f"{n} {relazione.related_model._meta.verbose_name_plural}")
    return trovati


def unisci(storico, doppione, oggi=None):
    """Sposta donazioni e dati di AssoWeb dal doppione allo storico e cancella il doppione.

    Va chiamata dentro una transazione. Restituisce {"donazioni spostate", "donazioni già presenti",
    "campi": nomi dei campi cambiati}, senza valori. CF, data di nascita e, se cambiano, cognome e nome
    che aveva lo storico finiscono nelle note, per correggerli anche in AssoAVIS.
    """
    oggi = oggi or timezone.localdate()
    if storico.pk == doppione.pk:
        raise UnioneError(f"socio {storico.pk}: storico e doppione sono la stessa scheda")
    if doppione.codice_interno_assoavis:
        raise UnioneError(f"socio {doppione.pk}: ha un codice AssoAVIS, quindi non è un doppione creato da AssoWeb")
    if storico.sezione_id != doppione.sezione_id:
        raise UnioneError(f"socio {storico.pk} e socio {doppione.pk}: sezioni diverse")
    if collegati := _collegati(doppione):
        raise UnioneError(f"socio {doppione.pk}: ha {', '.join(collegati)}, da sistemare a mano")

    date_storico = set(storico.donazioni.values_list("data", flat=True))
    gia_presenti = doppione.donazioni.filter(data__in=date_storico)
    n_gia_presenti = gia_presenti.count()
    gia_presenti.delete()
    n_spostate = doppione.donazioni.update(socio=storico)

    vecchi = {nome: getattr(storico, nome) for nome in ("cognome", "nome", "codice_fiscale", "data_nascita")}
    cambiati = []
    for nome in CAMPI_ASSOWEB:
        valore = getattr(doppione, nome)
        if valore not in ("", None) and getattr(storico, nome) != valore:
            setattr(storico, nome, valore)
            cambiati.append(nome)
    if doppione.idoneita:
        for nome in CAMPI_IDONEITA:
            if getattr(storico, nome) != getattr(doppione, nome):
                setattr(storico, nome, getattr(doppione, nome))
                cambiati.append(nome)
    storico.collaboratore |= doppione.collaboratore
    storico.no_benemerenze |= doppione.no_benemerenze
    storico.aggiornato_assoweb_il = doppione.aggiornato_assoweb_il

    # la data di iscrizione di AssoWeb segue la regola dell'import, ora che le donazioni sono tutte qui
    storico.data_iscrizione_assoweb = doppione.data_iscrizione_assoweb
    prima = storico.donazioni.filter(tipo__e_donazione=True).aggregate(prima=Min("data"))["prima"]
    nuova, _ = iscrizione_da_assoweb(doppione.data_iscrizione_assoweb, storico.data_iscrizione, prima,
                                     storico.data_nascita, oggi)
    if nuova:
        storico.data_iscrizione = nuova
        cambiati.append("data_iscrizione")

    if set(vecchi) & set(cambiati):
        nascita = vecchi["data_nascita"]
        nomi = [f"{nome} {vecchi[nome]}" for nome in ("cognome", "nome") if nome in cambiati]
        nota = (f"{oggi:%d/%m/%Y}: unito alla scheda creata da AssoWeb (socio {doppione.pk}). In AssoAVIS: "
                + ", ".join([*nomi, f"CF {vecchi['codice_fiscale'] or 'vuoto'}",
                             f"nascita {f'{nascita:%d/%m/%Y}' if nascita else 'vuota'}"]) + ".")
        storico.note = f"{storico.note}\n{nota}".strip()
    if doppione.note:
        storico.note = f"{storico.note}\n{doppione.note}".strip()

    # prima la cancellazione, per il vincolo di unicità del codice fiscale
    doppione.delete()
    storico.save()
    return {"donazioni spostate": n_spostate, "donazioni già presenti": n_gia_presenti, "campi": cambiati}


def unisci_coppie(coppie, oggi=None):
    """Unisce le coppie (id storico, id doppione) e poi ricalcola gli stati; va chiamata dentro una transazione.

    Restituisce i risultati di `unisci` per coppia e i passaggi di stato di aggiorna_stati.
    """
    oggi = oggi or timezone.localdate()
    ids = [pk for coppia in coppie for pk in coppia]
    if ripetuti := sorted(pk for pk, n in Counter(ids).items() if n > 1):
        raise UnioneError(f"soci in più di una coppia: {', '.join(map(str, ripetuti))}")
    soci = Socio.objects.in_bulk(ids)
    if mancanti := sorted(set(ids) - set(soci)):
        raise UnioneError(f"soci inesistenti: {', '.join(map(str, mancanti))}")
    risultati = {(s, d): unisci(soci[s], soci[d], oggi) for s, d in coppie}
    return risultati, aggiorna_stati(oggi)
