"""Job "Invio libro soci SIAN" (README M5): estrae il libro soci e lo invia con il web service del SIAN.

Lo esegue il worker (core/worker.py). Parametri dell'esecuzione: `anno` del registro, codice della `sezione`,
`modalita` e, per la modalità singolo, l'ID del `socio`:
- *totale*: `InvioListaTesserati` con Tipo 1, che **sostituisce l'intero registro della sede**. L'elaborazione sul
  SIAN è asincrona: il job attende l'esito per qualche minuto, poi lo si rilegge dalla pagina dell'invio;
- *singolo*: `InvioSingoloTesserato` di un socio, con l'esito subito. Serve a provare il collegamento.

Il log cita i soci come `socio <id>` e riporta solo conteggi: le segnalazioni del SIAN possono contenere i dati
del socio e restano nelle righe dell'invio.
"""

import time
from collections import Counter

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from gestionale.core.eventi import registra
from gestionale.core.models import Sezione
from gestionale.portali import sian

from . import libro_soci
from .models import EsitoSian, InvioSian, InvioSianRiga, ModalitaSian, OperazioneSian

# quanto il job attende l'esito di un invio completo, e ogni quanto lo chiede
ATTESA_ESITO = 5 * 60
INTERVALLO_ESITO = 15


def invia(esecuzione, log):
    parametri = esecuzione.parametri
    anno, modalita = parametri["anno"], parametri["modalita"]
    sezione = Sezione.objects.get(codice=parametri["sezione"])
    token, url = settings.SIAN_TOKEN, settings.SIAN_URL
    if not token:
        raise sian.ErroreSian("Token del SIAN mancante: impostare SIAN_TOKEN in .env")

    anno_registro, _ = sian.is_alive(sezione.codice, token, url=url)
    log(f"Collegamento al SIAN riuscito: è aperto il registro {anno_registro}")
    if anno_registro != anno:
        raise sian.ErroreSian(f"Sul SIAN è aperto il registro {anno_registro}, non quello del {anno}")

    libro = libro_soci.estrai(anno, sezione, timezone.localdate())
    if modalita == ModalitaSian.SINGOLO:
        _invia_singolo(esecuzione, log, libro, parametri["socio"], token, url)
    else:
        _invia_totale(esecuzione, log, libro, token, url)


def _invia_singolo(esecuzione, log, libro, socio, token, url):
    record = next((r for r in libro.record if r.socio.pk == socio), None)
    if record is None:
        raise ValueError(f"Il socio {socio} non è nel libro soci del {libro.anno}")
    if record.scarti:
        raise ValueError(f"Il socio {socio} non supera i controlli: {'; '.join(record.scarti)}")
    log(f"Invio del socio {socio} ({libro_soci.TIPI_SOCIO[record.tipo_socio].lower()}"
        f"{', cessato' if record.cessato else ''})")
    xml = sian.xml_tesserati([libro_soci.record_xml(record.campi)])
    esito = sian.invia_singolo(libro.sede, libro.anno, token, xml, url=url)
    # errore dell'intera richiesta (es. token o anno), non del socio
    errore = "" if esito.errore in ("", "0") else esito.errore.splitlines()[0]
    with transaction.atomic():
        invio = InvioSian.objects.create(anno=libro.anno, sede=libro.sede, modalita=ModalitaSian.SINGOLO,
                                         esecuzione=esecuzione, esito_il=esito.data_analisi or timezone.now())
        riga = _riga(invio, record, EsitoSian.INVIATO)
        if errore:
            riga.esito, riga.messaggio = EsitoSian.ERRORE, errore
        else:
            # le segnalazioni sono tutte di questo socio, anche se il SIAN non restituisce il suo UNIVOCOAVIS
            riga.esito = EsitoSian.ERRORE if any(r.bloccante for r in esito.record) else EsitoSian.OK
            riga.messaggio = "; ".join(e for r in esito.record for e in r.errori if e)
        riga.save(update_fields=["esito", "messaggio"])
        registra("invio_sian", utente=esecuzione.utente, oggetto=f"esecuzione {esecuzione.pk}", anno=libro.anno,
                 modalita=ModalitaSian.SINGOLO, socio=socio, esito=riga.esito)
    if errore:
        raise sian.ErroreSian(f"Il SIAN ha risposto: {errore}")
    segnalazioni = sum(len(r.errori) for r in esito.record)
    log(f"Esito: {riga.get_esito_display().lower()}"
        + (f", {segnalazioni} {'segnalazione' if segnalazioni == 1 else 'segnalazioni'} del SIAN nella pagina "
           "dell'invio" if segnalazioni else ""))


def _invia_totale(esecuzione, log, libro, token, url):
    riepilogo = libro.riepilogo()
    log(f"Libro soci {libro.anno}: {riepilogo['soci']} soci ({riepilogo['donatori']} donatori, "
        f"{riepilogo['collaboratori']} collaboratori) e {riepilogo['cessati']} cessati nell'anno",
        f"Da inviare: {riepilogo['inviabili']}; non inviati perché non superano i controlli: "
        f"{riepilogo['scartati']}")
    if not libro.inviabili:
        raise ValueError("Nessun socio da inviare")
    with transaction.atomic():
        invio = InvioSian.objects.create(anno=libro.anno, sede=libro.sede, modalita=ModalitaSian.TOTALE,
                                         esecuzione=esecuzione)
        for record in libro.record:
            _riga(invio, record, EsitoSian.INVIATO if record.inviabile else EsitoSian.SCARTATO)

    pacchetti = sian.pacchetti([libro_soci.record_xml(r.campi) for r in libro.inviabili])
    log(f"Invio del registro completo al SIAN in {len(pacchetti)} pacchett{'o' if len(pacchetti) == 1 else 'i'}")
    invio.id_invio = sian.invia_lista(libro.sede, libro.anno, token, pacchetti, url=url, avanza=log)
    invio.save(update_fields=["id_invio"])
    registra("invio_sian", utente=esecuzione.utente, oggetto=f"esecuzione {esecuzione.pk}", anno=libro.anno,
             modalita=ModalitaSian.TOTALE, inviati=riepilogo["inviabili"], scartati=riepilogo["scartati"])
    log("Registro inviato: il SIAN lo elabora, attendo l'esito")

    scadenza = time.monotonic() + ATTESA_ESITO
    while True:
        conteggi = aggiorna_esito(invio, token, url)
        if conteggi is not None:
            log(*righe_esito(conteggi))
            return
        if time.monotonic() >= scadenza:
            log("Il SIAN non ha ancora elaborato il registro: l'esito si legge più tardi dalla pagina dell'invio, "
                "con il bottone Aggiorna l'esito")
            return
        time.sleep(INTERVALLO_ESITO)


def _riga(invio, record, esito):
    campi = record.campi
    return InvioSianRiga.objects.create(
        invio=invio, socio=record.socio, codice_fiscale=campi["CODICEFISCALE"],
        operazione=OperazioneSian.CESSAZIONE if record.cessato else OperazioneSian.INSERIMENTO,
        tipo_socio=campi["TIPOSOCIO"], data_cessazione=campi["DATACESSAZIONE"],
        causa_cessazione=campi["CAUSACESSAZIONE"], esito=esito, messaggio="; ".join(record.scarti))


def aggiorna_esito(invio, token, url):
    """Legge l'esito di un invio completo dal SIAN e lo scrive nelle righe; None se non è ancora pronto."""
    esito = sian.log_importazione(invio.sede, invio.id_invio, token, url=url)
    if esito.data_analisi is None:
        return None
    if esito.errore and esito.errore != "0":
        raise sian.ErroreSian(f"Il SIAN ha rifiutato l'invio: {esito.errore.splitlines()[0]}")
    with transaction.atomic():
        conteggi = _applica_esito(invio, esito)
        invio.esito_il = esito.data_analisi
        invio.save(update_fields=["esito_il"])
    return conteggi


def _applica_esito(invio, esito):
    """Segna le righe in attesa come riuscite, tranne quelle che il SIAN segnala.

    Il SIAN restituisce i record segnalati con l'XML inviato: UNIVOCOAVIS è l'ID del socio, il codice fiscale è il
    ripiego. Le segnalazioni bloccanti (critiche o eccezioni del server) sono errori; le altre sono avvisi e il
    record è comunque acquisito.
    """
    righe = {r.socio_id: r for r in invio.righe.filter(esito=EsitoSian.INVIATO)}
    per_cf = {r.codice_fiscale: r for r in righe.values() if r.codice_fiscale}
    conteggi = Counter()
    for record in esito.record:
        univoco = record.campo("UNIVOCOAVIS")
        riga = righe.get(int(univoco)) if univoco.isdigit() else None
        riga = riga or per_cf.get(record.campo("CODICEFISCALE").upper())
        if riga is None:
            conteggi["non_collegate"] += 1
            continue
        riga.esito = EsitoSian.ERRORE if record.bloccante else EsitoSian.OK
        riga.messaggio = "; ".join(e for e in record.errori if e)
        riga.save(update_fields=["esito", "messaggio"])
        conteggi["errori" if record.bloccante else "avvisi"] += 1
    conteggi["riusciti"] = invio.righe.filter(esito=EsitoSian.INVIATO).update(esito=EsitoSian.OK) + conteggi["avvisi"]
    return conteggi


def righe_esito(conteggi):
    righe = [f"Esito del SIAN: {conteggi['riusciti']} soci acquisiti, di cui {conteggi['avvisi']} con avvisi; "
             f"{conteggi['errori']} con errori"]
    if conteggi["non_collegate"]:
        righe.append(f"{conteggi['non_collegate']} segnalazioni del SIAN non riconducibili a un socio dell'invio: "
                     "vanno viste sul portale SIAN, in Allarmi importazione")
    return righe
