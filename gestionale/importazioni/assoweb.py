"""Import dei due CSV scaricati da AssoWeb (README M1).

AssoWeb è la fonte master di anagrafica, contatti, tessera, dati trasfusionali e idoneità; della data di
iscrizione si prendono solo le date utili (iscrizione_da_assoweb). L'import è idempotente: i soci si
agganciano per codice fiscale, le donazioni si aggiungono per (socio, data) e non si cancellano, e
rieseguire gli stessi file non cambia nulla. Alla fine si ricalcola lo stato dei soci.
"""

import csv
import datetime
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from django.db.models import Count, Min
from django.utils import timezone

from gestionale.benemerenze.regole import anni_compiuti
from gestionale.core.models import Sezione
from gestionale.soci.models import Donazione, Fonte, Idoneita, Socio, StatoSocio, TipoDonazione
from gestionale.soci.stati import aggiorna_stati, descrivi_passaggi

from .anomalie import righe_anomalie
from .assoavis import ImportazioneError

FILE_DONATORI = "DONATORI_TOT.csv"
FILE_DONAZIONI = "TOT_DONAZIONI.csv"

# colonna di DONATORI_TOT.csv -> campo del socio; i refusi nei nomi di colonna sono del portale.
# Non si importano ACCESSI e DATA_ULTIMA_DONAZIONE (si ricavano dalle donazioni) e il domicilio. TESSERA_ASSO
# si tratta a parte in campi_da_riga, DATA_ISCRIZIONE in _aggiorna_iscrizioni
CAMPI = {
    "CODICEREGIONALE": "codice_regionale",
    "COGNOME": "cognome",
    "NOME": "nome",
    "SESSO": "sesso",
    "LUOGO_NASCITA": "luogo_nascita",
    "SIGLAP_NASCIATA": "provincia_nascita",
    "LUOGO_RESIDENZA": "comune_residenza",
    "CAP_RESIDENZA": "cap_residenza",
    "SIGLAP_RESIDENZA": "provincia_residenza",
    "TELEFONO_CELLULARE": "cellulare",
    "TEL_ABITAZIONE": "telefono",
    "E_MAIL": "email",
    "GR_SANG": "gruppo_sanguigno",
    "FATTORE": "rh",
    "FENRH": "fenotipo_rh",
    "KELL": "kell",
    "STATO": "stato_assoweb",
}
IDONEITA = {
    "Idoneo": Idoneita.IDONEO,
    "Non Idoneo": Idoneita.NON_IDONEO,
    "Temporaneamente Sospeso": Idoneita.TEMP_SOSPESO,
    "Definitivamente Sospeso": Idoneita.DEF_SOSPESO,
}
COLONNE_DONATORI = {*CAMPI, "CODICEFISCALE", "SEZIONE", "DATA_NASCITA", "TESSERA_ASSO", "DATA_ISCRIZIONE", "IDONEITA",
                    "DATA_INIZIO", "DATA_FINE"}
COLONNE_DONAZIONI = {"CODICE_FISCALE", "DATA_DON", "TIPO", "CENTRO_PRELIEVO", "TOT_DON"}
CAMPI_AGGIORNATI = ["codice_fiscale", *CAMPI.values(), "data_nascita", "tessera", "idoneita", "idoneita_dal",
                    "idoneita_al", "modificato_il"]

# cifre del numero di tessera dopo il codice della sede, nel formato di AssoAVIS
CIFRE_TESSERA = 5
# la prima iscrizione che AssoAVIS ha messo a metà dei soci (1.716 su 3.423) al caricamento dello storico
SEGNAPOSTO_ISCRIZIONE = datetime.date(1997, 12, 31)
PRIMA_ISCRIZIONE_VALIDA = datetime.date(1900, 1, 1)
ETA_MINIMA = 18

VOCI = ["righe donatori", "soci nuovi", "soci aggiornati", "codici fiscali aggiunti o corretti", "righe donazioni",
        "donazioni aggiunte", "donazioni già presenti", "date di iscrizione da AssoWeb"]


@dataclass
class Report:
    conteggi: Counter = field(default_factory=lambda: Counter(dict.fromkeys(VOCI, 0)))
    # (tipo, riferimento, dettaglio): il riferimento è "socio <id>" o "riga <n>" del CSV, mai un dato personale
    anomalie: list = field(default_factory=list)
    # {(stato prima, stato dopo): n} da aggiorna_stati
    passaggi_stato: Counter = field(default_factory=Counter)


def righe_report(report, massimo=None):
    """Il report in righe di testo, per la console e per il log delle esecuzioni; `massimo` come in righe_anomalie."""
    righe = ["Import da AssoWeb", *(f"  {voce:36}{n:>7}" for voce, n in report.conteggi.items()), "",
             "Stato dei soci", *(f"  {riga}" for riga in descrivi_passaggi(report.passaggi_stato)
                                 or ["nessun cambiamento"]), ""]
    return righe + righe_anomalie(report.anomalie, massimo)


def leggi_csv(percorso):
    """Righe di un CSV di AssoWeb (separatore ";", ASCII). Il ";" finale crea una colonna senza nome, ignorata."""
    with open(percorso, newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file, delimiter=";"))


def _data(riga, colonna):
    """AssoWeb esporta le date in ISO (aaaa-mm-gg); gg/mm/aaaa si accetta per sicurezza."""
    testo = riga[colonna].strip()[:10]
    if not testo:
        return None
    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(testo, formato).date()
        except ValueError:
            pass
    raise ImportazioneError(f"Colonna {colonna}: data in un formato non previsto. AssoWeb ha cambiato l'export?")


def _chiave(cognome, nome, nascita):
    return cognome.strip().upper(), nome.strip().upper(), nascita


def _controlla_colonne(righe, attese, nome_file):
    if righe and (mancanti := attese - set(righe[0])):
        raise ImportazioneError(f"In {nome_file} mancano le colonne {', '.join(sorted(mancanti))}.")


def tessera_da_assoweb(valore, codice_sede):
    """La tessera di AssoWeb nel formato di AssoAVIS, o None; più il testo dell'anomalia.

    È la stessa numerazione: AssoAVIS scrive il codice della sede seguito dal numero su 5 cifre (152900123),
    AssoWeb il solo numero (123). Coincidono per 1.248 dei 1.263 soci che hanno la tessera in tutte e due le
    fonti. Un numero già nel formato di AssoAVIS si prende così com'è.
    """
    valore = valore.strip()
    if not valore:
        return None, None
    if valore.isdigit() and len(valore) <= CIFRE_TESSERA:
        return f"{codice_sede}{int(valore):0{CIFRE_TESSERA}d}", None
    if valore.isdigit() and valore.startswith(codice_sede) and len(valore) == len(codice_sede) + CIFRE_TESSERA:
        return valore, None
    return None, "tessera di AssoWeb in un formato non previsto"


def campi_da_riga(riga, codice_sede):
    """Campi del socio da una riga di DONATORI_TOT.csv, più i testi delle anomalie.

    I valori vuoti restano fuori, così non cancellano quelli dello storico (AssoWeb non ha l'indirizzo e ha
    meno contatti). L'idoneità invece si prende intera con le sue date: una sospensione finita non lascia date.
    """
    campi = {campo: riga[colonna].strip() for colonna, campo in CAMPI.items() if riga[colonna].strip()}
    if nascita := _data(riga, "DATA_NASCITA"):
        campi["data_nascita"] = nascita
    anomalie = []
    tessera, anomalia = tessera_da_assoweb(riga["TESSERA_ASSO"], codice_sede)
    if tessera:
        campi["tessera"] = tessera
    elif anomalia:
        anomalie.append(anomalia)
    idoneita = riga["IDONEITA"].strip()
    if idoneita in IDONEITA:
        campi.update(idoneita=IDONEITA[idoneita], idoneita_dal=_data(riga, "DATA_INIZIO"),
                     idoneita_al=_data(riga, "DATA_FINE"))
    elif idoneita:
        anomalie.append(f"idoneità non prevista: {idoneita}")
    return campi, anomalie


def iscrizione_da_assoweb(data, attuale, prima_donazione, nascita, oggi):
    """La data di iscrizione da prendere da AssoWeb, o None per tenere `attuale`; più il testo dell'anomalia.

    AssoWeb ha date utili ma anche refusi, quindi si prende solo una data plausibile che migliora quella
    attuale (README §3):
    - plausibile: dal 1900 a oggi e con il socio maggiorenne, altrimenti è un'anomalia;
    - non dopo la prima donazione registrata, perché non si dona prima di iscriversi (molte sono del 2018,
      probabilmente la migrazione al sistema regionale); se lo è anche la data attuale, vale la più vecchia;
    - migliora la data attuale se questa manca, è il segnaposto 31/12/1997 o è più recente: la prima
      iscrizione è la data più vecchia, e una più recente può essere l'arrivo di un trasferito.
    """
    if data is None or data == attuale:
        return None, None
    if not PRIMA_ISCRIZIONE_VALIDA <= data <= oggi:
        return None, f"data di iscrizione di AssoWeb impossibile (anno {data.year})"
    if nascita and anni_compiuti(nascita, data) < ETA_MINIMA:
        return None, "data di iscrizione di AssoWeb prima dei 18 anni"
    if attuale is not None and attuale != SEGNAPOSTO_ISCRIZIONE and data >= attuale:
        return None, None
    if prima_donazione and data > prima_donazione and not (attuale and data < attuale):
        return None, None
    return data, None


def importa(righe_donatori, righe_donazioni, oggi=None):
    """Importa le righe dei due CSV e ricalcola lo stato dei soci. Va chiamata dentro una transazione."""
    if not righe_donatori:
        raise ImportazioneError(f"{FILE_DONATORI} è vuoto: il download da AssoWeb non è andato a buon fine?")
    _controlla_colonne(righe_donatori, COLONNE_DONATORI, FILE_DONATORI)
    _controlla_colonne(righe_donazioni, COLONNE_DONAZIONI, FILE_DONAZIONI)
    report = Report()
    oggi, adesso = oggi or timezone.localdate(), timezone.now()
    in_assoweb = {r["CODICEFISCALE"].strip().upper() for r in righe_donatori}
    per_cf, iscrizioni = _importa_soci(righe_donatori, in_assoweb, report, adesso)
    _importa_donazioni(righe_donazioni, per_cf, report)
    # dopo le donazioni, perché la regola guarda la prima donazione registrata
    _aggiorna_iscrizioni(iscrizioni, report, oggi, adesso)

    report.passaggi_stato = aggiorna_stati(oggi)
    for socio_id in (Socio.objects.filter(stato=StatoSocio.DONATORE).exclude(codice_fiscale__in=in_assoweb)
                     .order_by("pk").values_list("pk", flat=True)):
        report.anomalie.append(("Socio donatore assente da AssoWeb", f"socio {socio_id}", ""))
    return report


def _importa_soci(righe, in_assoweb, report, adesso):
    """Aggiorna o crea i soci; restituisce {codice fiscale: socio} per agganciare le donazioni e le coppie
    (socio, DATA_ISCRIZIONE) per _aggiorna_iscrizioni.

    `in_assoweb` sono i CF del file: un socio con uno di questi CF appartiene a quella riga e non si
    aggancia ad altre per cognome, nome e data di nascita.
    """
    sezioni = {s.nome_assoweb: s for s in Sezione.objects.exclude(nome_assoweb="")}
    soci = list(Socio.objects.all())
    per_cf = {s.codice_fiscale: s for s in soci if s.codice_fiscale}
    per_tessera = {s.tessera: s for s in soci if s.tessera}
    per_nome = defaultdict(list)
    for socio in soci:
        if socio.data_nascita:
            per_nome[_chiave(socio.cognome, socio.nome, socio.data_nascita)].append(socio)
    # AssoWeb ha qualche tessera ripetuta su più donatori: non si sa di chi è, quindi non si prende
    tessere = Counter(tessera_da_assoweb(r["TESSERA_ASSO"], sezioni[r["SEZIONE"].strip()].codice)[0]
                      for r in righe if r["SEZIONE"].strip() in sezioni)
    ripetute = {tessera for tessera, n in tessere.items() if tessera and n > 1}

    nuovi, modificati, presenti, visti, iscrizioni = [], {}, [], set(), []
    for numero, riga in enumerate(righe, start=2):  # la riga 1 è l'intestazione
        report.conteggi["righe donatori"] += 1
        rif = f"riga {numero}"
        cf = riga["CODICEFISCALE"].strip().upper()
        sezione = sezioni.get(riga["SEZIONE"].strip())
        motivo = ("codice fiscale mancante" if not cf else "codice fiscale ripetuto" if cf in visti
                  else "sezione non gestita" if sezione is None else "")
        if motivo:
            report.anomalie.append(("Riga di donatore scartata", rif, motivo))
            continue
        visti.add(cf)
        campi, anomalie = campi_da_riga(riga, sezione.codice)

        socio = per_cf.get(cf)
        if socio is None and "data_nascita" in campi:
            candidati = [s for s in per_nome[_chiave(riga["COGNOME"], riga["NOME"], campi["data_nascita"])]
                         if s.codice_fiscale not in in_assoweb]
            if len(candidati) == 1:
                socio = candidati[0]
                report.anomalie.append(("Codice fiscale corretto" if socio.codice_fiscale else
                                        "Codice fiscale aggiunto", f"socio {socio.pk}",
                                        "agganciato ad AssoWeb per cognome, nome e data di nascita"))
                report.conteggi["codici fiscali aggiunti o corretti"] += 1
                socio.codice_fiscale = cf
                per_cf[cf] = socio
                modificati[socio.pk] = socio
            elif candidati:
                report.anomalie.append(("Omonimi: creato un nuovo socio", rif,
                                        f"{len(candidati)} soci con gli stessi cognome, nome e data di nascita"))

        # la tessera di un altro socio, nel DB o in un'altra riga del file, va sistemata a mano
        tessera = campi.get("tessera")
        if tessera and tessera != getattr(socio, "tessera", None) and (
                tessera in ripetute or per_tessera.get(tessera, socio) is not socio):
            del campi["tessera"]
            anomalie.append("tessera di AssoWeb già di un altro socio")

        if socio is None:
            socio = Socio(sezione=sezione, codice_fiscale=cf, aggiornato_assoweb_il=adesso, **campi)
            nuovi.append(socio)
            per_cf[cf] = socio
            iscrizioni.append((socio, _data(riga, "DATA_ISCRIZIONE")))
            report.anomalie += [("Dato non importato", rif, testo) for testo in anomalie]
            continue
        iscrizioni.append((socio, _data(riga, "DATA_ISCRIZIONE")))
        report.anomalie += [("Dato non importato", f"socio {socio.pk}", testo) for testo in anomalie]
        cambiati = [nome for nome, valore in campi.items() if getattr(socio, nome) != valore]
        if "cap_residenza" in cambiati and socio.cap_residenza and socio.indirizzo_residenza:
            report.anomalie.append(("Residenza cambiata", f"socio {socio.pk}",
                                    "l'indirizzo viene dallo storico AssoAVIS: da verificare"))
        if "tessera" in cambiati and socio.tessera:
            report.anomalie.append(("Tessera cambiata", f"socio {socio.pk}",
                                    "AssoWeb ha un numero diverso da quello dello storico AssoAVIS"))
        for nome in cambiati:
            setattr(socio, nome, campi[nome])
        if cambiati:
            modificati[socio.pk] = socio
        presenti.append(socio.pk)

    Socio.objects.bulk_create(nuovi)
    for socio in modificati.values():
        socio.modificato_il = adesso
    Socio.objects.bulk_update(modificati.values(), CAMPI_AGGIORNATI, batch_size=500)
    Socio.objects.filter(pk__in=presenti).update(aggiornato_assoweb_il=adesso)
    report.conteggi["soci nuovi"] = len(nuovi)
    report.conteggi["soci aggiornati"] = len(modificati)
    return per_cf, iscrizioni


def _aggiorna_iscrizioni(iscrizioni, report, oggi, adesso):
    """Applica iscrizione_da_assoweb solo ai soci la cui data su AssoWeb è nuova o cambiata dall'ultimo import.

    Così una data corretta a mano nel gestionale resta finché la data non cambia su AssoWeb. Le date
    impossibili si segnalano a ogni import, finché non vengono corrette sul portale.
    """
    prime = dict(Donazione.objects.filter(socio_id__in=[s.pk for s, _ in iscrizioni], tipo__e_donazione=True)
                 .order_by().values("socio_id").annotate(prima=Min("data")).values_list("socio_id", "prima"))
    modificati = []
    for socio, data in iscrizioni:
        nuova, anomalia = iscrizione_da_assoweb(data, socio.data_iscrizione, prime.get(socio.pk), socio.data_nascita,
                                                oggi)
        if anomalia:
            report.anomalie.append(("Dato non importato", f"socio {socio.pk}", anomalia))
        if socio.data_iscrizione_assoweb == data:
            continue
        socio.data_iscrizione_assoweb = data
        if nuova:
            socio.data_iscrizione, socio.modificato_il = nuova, adesso
            report.conteggi["date di iscrizione da AssoWeb"] += 1
        modificati.append(socio)
    Socio.objects.bulk_update(modificati, ["data_iscrizione", "data_iscrizione_assoweb", "modificato_il"],
                              batch_size=500)


def _importa_donazioni(righe, per_cf, report):
    tipi = {t.codice: t for t in TipoDonazione.objects.all()}
    lette, tot_don = [], {}
    for numero, riga in enumerate(righe, start=2):
        report.conteggi["righe donazioni"] += 1
        socio = per_cf.get(riga["CODICE_FISCALE"].strip().upper())
        tipo = tipi.get(riga["TIPO"].strip())
        data_don = _data(riga, "DATA_DON")
        motivo = ("donatore assente da " + FILE_DONATORI if socio is None else
                  f"tipo {riga['TIPO'].strip()} sconosciuto" if tipo is None else
                  "data mancante" if data_don is None else "")
        if motivo:
            report.anomalie.append(("Donazione scartata", f"riga {numero}", motivo))
            continue
        lette.append((socio, data_don, tipo, riga["CENTRO_PRELIEVO"].strip()))
        if riga["TOT_DON"].strip().isdigit():
            tot_don[socio.pk] = max(tot_don.get(socio.pk, 0), int(riga["TOT_DON"]))

    anni = {data_don.year for _, data_don, _, _ in lette}
    esistenti = {(socio_id, data_don): tipo_id for socio_id, data_don, tipo_id
                 in Donazione.objects.filter(data__year__in=anni).values_list("socio_id", "data", "tipo_id")}
    nuove = []
    for socio, data_don, tipo, centro in lette:
        chiave = (socio.pk, data_don)
        if chiave in esistenti:
            report.conteggi["donazioni già presenti"] += 1
            if esistenti[chiave] != tipo.pk:
                report.anomalie.append(("Donazione già presente con un altro tipo", f"socio {socio.pk}",
                                        "lasciata com'è nel DB"))
            continue
        esistenti[chiave] = tipo.pk
        nuove.append(Donazione(socio=socio, data=data_don, tipo=tipo, centro_prelievo=centro, fonte=Fonte.ASSOWEB))
    Donazione.objects.bulk_create(nuove)
    report.conteggi["donazioni aggiunte"] = len(nuove)

    # TOT_DON conta solo le donazioni registrate nel sistema regionale ed è quasi sempre minore del totale
    # del DB, che ha tutto lo storico: solo un valore maggiore segnala donazioni mancanti
    nel_db = dict(Donazione.objects.filter(socio_id__in=tot_don, tipo__e_donazione=True).order_by()
                  .values("socio_id").annotate(n=Count("id")).values_list("socio_id", "n"))
    for socio_id, totale in sorted(tot_don.items()):
        if totale > nel_db.get(socio_id, 0):
            report.anomalie.append(("Donazioni che mancano nel DB", f"socio {socio_id}",
                                    f"AssoWeb ne conta {totale}, il DB {nel_db.get(socio_id, 0)}"))
