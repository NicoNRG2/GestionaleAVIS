"""Migrazione una tantum dello storico AssoAVIS nel nuovo DB (README §3).

Lo storico si legge dal DB SQL Server ASSOAVIS_STORICO, ripristinato in sola lettura. La lettura
(`leggi_storico`) è separata dalla scrittura (`importa`), che riceve righe già lette con i nomi di
colonna di AssoAVIS: così le mappature si provano senza SQL Server. Regole e decisioni in README §3.
"""

import datetime
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from django.db.models import Count
from django.db.models.functions import ExtractYear
from django.utils import timezone

from gestionale.benemerenze.models import Benemerenza, BenemerenzaAssegnata, Premiato, Premiazione, StatoPremiazione
from gestionale.core.models import Sezione
from gestionale.soci.models import CausaCessazione, Donazione, Fonte, Socio, StatoSocio, TipoDonazione

CODICE_SEZIONE = "1529"

# In AssoAVIS la premiazione del 03/11/2025 non è mai stata chiusa (ASSEGNATE = 0), ma le benemerenze sono
# state consegnate: si importa come assegnata e i premiati le ricevono
PREMIAZIONI_CONSEGNATE = {118}

PREGRESSE = ["donazioni_pregresse_si", "donazioni_pregresse_aferesi", "donazioni_pregresse_dopo_2004"]

# il report mostra anno per anno le donazioni da qui in poi, quelle precedenti in una riga sola
PRIMO_ANNO_DETTAGLIO = 2015


class ImportazioneError(Exception):
    pass


@dataclass
class Storico:
    """Righe lette da AssoAVIS e conteggi calcolati da SQL Server, che servono per la riconciliazione."""

    soci: list
    donazioni: list
    premiazioni: list
    premiati: list
    conteggi: dict


@dataclass
class Report:
    # (voce, AssoAVIS, nuovo DB, differenza attesa, nota)
    righe: list = field(default_factory=list)
    # (tipo, codice interno AssoAVIS, dettaglio): solo codici, niente dati personali
    anomalie: list = field(default_factory=list)

    def confronta(self, voce, assoavis, nuovo_db, attesa=0, nota=""):
        self.righe.append((voce, assoavis, nuovo_db, attesa, nota))

    @property
    def coincide(self):
        return all(assoavis + attesa == nuovo_db for _, assoavis, nuovo_db, attesa, _ in self.righe)


# --- lettura da SQL Server ---

def _righe(cursore, sql, *parametri):
    cursore.execute(sql, *parametri)
    colonne = [c[0] for c in cursore.description]
    return [dict(zip(colonne, riga)) for riga in cursore.fetchall()]


def leggi_storico(cursore, sezione=CODICE_SEZIONE):
    """Legge soci, donazioni e premiazioni della sezione da un cursore pyodbc su ASSOAVIS_STORICO."""
    sezione = int(sezione)
    catalogo = list(Benemerenza.objects.all())
    # i nomi di colonna vengono dal catalogo caricato dalle migrazioni, non da input esterni
    benemerenze = ", ".join(f"{b.colonna_flag_assoavis}, {b.colonna_data_assoavis}" for b in catalogo)
    soci_sezione = "SELECT CODINTERNODONAT FROM DONATORE WHERE SEZIONE = ?"
    premiazioni_sezione = "SELECT ID FROM PREMIAZIONISEZIONI WHERE SEZIONE = ?"

    soci = _righe(cursore, f"""
        SELECT CODINTERNODONAT, CODICEFISCALE, TESSERA, SOLOCOGNOME, SOLONOME, COGNOMEENOME, SESSO, DATADINASCITA,
               LUOGONASCITA, PROVDINASCITA, RESINDIRIZZO, RESCITTA, RESCAP, RESPROV, CELLULARE, RESTEL, EMAIL,
               DATAPRIMAISCR, CANCELLADEFIN, CAST(MOTIVOCANCELL AS varchar(200)) AS MOTIVOCANCELL,
               DONAZIONIPREGR, AFERESIPREGRES, DONAZIONIPREGRDOPO2004, NOBENEM, {benemerenze}
        FROM DONATORE WHERE SEZIONE = ?""", sezione)
    donazioni = _righe(cursore, f"""
        SELECT z.CODINTERNO, z.DATADON, z.TIPO, p.NOMEPUNTOPREL
        FROM DONAZIONI z LEFT JOIN PUNTOPRELIEVO p ON p.CODICEPUNTOPREL = z.PUNTOPRELIEVO
        WHERE z.CODINTERNO IN ({soci_sezione})""", sezione)
    premiazioni = _righe(cursore, f"""
        SELECT ID, DESCRIZIONE, DATAPREMIAZIONE, DATARIFERIMENTO, ASSEGNATE
        FROM PREMIAZIONI WHERE ID IN ({premiazioni_sezione})""", sezione)
    premiati = _righe(cursore, f"""
        SELECT IDPREMIAZIONE, CODINTERNODONAT, TIPOONOREFICIENZA, NUMDONAZIONI, NUMDONAZEFF
        FROM PREMIAZIONIPREMIATI WHERE IDPREMIAZIONE IN ({premiazioni_sezione})""", sezione)

    # conteggi calcolati da SQL Server, indipendenti dalle righe lette sopra
    def per_chiave(sql):
        cursore.execute(sql, sezione)
        return dict(cursore.fetchall())

    def scalare(sql):
        cursore.execute(sql, sezione)
        return cursore.fetchone()[0]

    somme_flag = ", ".join(f"SUM(CASE WHEN {b.colonna_flag_assoavis} = 1 THEN 1 ELSE 0 END)" for b in catalogo)
    cursore.execute(f"SELECT {somme_flag} FROM DONATORE WHERE SEZIONE = ?", sezione)
    flag = {b.codice: n or 0 for b, n in zip(catalogo, cursore.fetchone())}
    conteggi = {
        "anagrafiche": scalare("SELECT COUNT(*) FROM DONATORE WHERE SEZIONE = ?"),
        "donazioni_per_anno": per_chiave(
            f"SELECT YEAR(DATADON), COUNT(*) FROM DONAZIONI WHERE CODINTERNO IN ({soci_sezione}) GROUP BY YEAR(DATADON)"),
        "donazioni_per_tipo": per_chiave(
            f"SELECT TIPO, COUNT(*) FROM DONAZIONI WHERE CODINTERNO IN ({soci_sezione}) GROUP BY TIPO"),
        "benemerenze": flag,
        "premiazioni": scalare(f"SELECT COUNT(*) FROM PREMIAZIONI WHERE ID IN ({premiazioni_sezione})"),
        "premiati": scalare(
            f"SELECT COUNT(*) FROM PREMIAZIONIPREMIATI WHERE IDPREMIAZIONE IN ({premiazioni_sezione})"),
    }
    return Storico(soci, donazioni, premiazioni, premiati, conteggi)


# --- mappature ---

def _data(valore):
    """pyodbc restituisce datetime anche per le colonne che contengono solo una data."""
    return valore.date() if isinstance(valore, datetime.datetime) else valore


def _testo(valore):
    return (valore or "").strip()


def _prima(*date):
    date = [d for d in date if d]
    return min(date) if date else None


def causa_cessazione(motivo):
    """MOTIVOCANCELL è testo libero: i trasferimenti hanno il loro codice SIAN, tutto il resto è Cessazione."""
    return CausaCessazione.TRASFERITO if "TRASFER" in _testo(motivo).upper() else CausaCessazione.CESSAZIONE


def socio_da_riga(riga, oggi):
    """Campi del Socio da una riga di DONATORE, più i testi delle anomalie trovate."""
    anomalie = []
    cessazione = _data(riga["CANCELLADEFIN"])
    if cessazione and cessazione > oggi:
        anomalie.append(f"data di cessazione nel futuro ({cessazione:%d/%m/%Y}), non importata")
        cessazione = None
    sesso = _testo(riga["SESSO"]).upper()
    campi = {
        "codice_interno_assoavis": _testo(riga["CODINTERNODONAT"]),
        # bulk_create non passa da Socio.save(): la normalizzazione delle chiavi si fa qui
        "codice_fiscale": _testo(riga["CODICEFISCALE"]).upper() or None,
        "tessera": _testo(riga["TESSERA"]),
        "cognome": _testo(riga["SOLOCOGNOME"]) or _testo(riga["COGNOMEENOME"]),
        "nome": _testo(riga["SOLONOME"]),
        "sesso": sesso if sesso in ("M", "F") else "",
        "data_nascita": _data(riga["DATADINASCITA"]),
        "luogo_nascita": _testo(riga["LUOGONASCITA"]),
        "provincia_nascita": _testo(riga["PROVDINASCITA"]),
        "indirizzo_residenza": _testo(riga["RESINDIRIZZO"]),
        "comune_residenza": _testo(riga["RESCITTA"]),
        "cap_residenza": _testo(riga["RESCAP"]),
        "provincia_residenza": _testo(riga["RESPROV"]),
        "cellulare": _testo(riga["CELLULARE"]),
        "telefono": _testo(riga["RESTEL"]),
        "email": _testo(riga["EMAIL"]),
        # i codici di stato di AssoAVIS non si importano: chi dona ancora torna donatore con aggiorna_stati
        "stato": StatoSocio.EX_SOCIO,
        "data_iscrizione": _data(riga["DATAPRIMAISCR"]),
        "data_cessazione": cessazione,
        "causa_cessazione": causa_cessazione(riga["MOTIVOCANCELL"]) if cessazione else None,
        "donazioni_pregresse_si": riga["DONAZIONIPREGR"] or 0,
        "donazioni_pregresse_aferesi": riga["AFERESIPREGRES"] or 0,
        "donazioni_pregresse_dopo_2004": riga["DONAZIONIPREGRDOPO2004"] or 0,
        "no_benemerenze": riga["NOBENEM"] == 1,
        "note": "",
    }
    if any(campi[nome] < 0 for nome in PREGRESSE):
        anomalie.append("donazioni pregresse negative, importate così come sono")
    return campi, anomalie


def benemerenze_da_riga(riga, catalogo):
    """Le coppie flag/data di DONATORE diventano {codice benemerenza: data}; la data può mancare."""
    return {b.codice: _data(riga[b.colonna_data_assoavis]) for b in catalogo if riga[b.colonna_flag_assoavis] == 1}


def fondi_duplicati(soci, ultima_donazione):
    """Fonde le anagrafiche con lo stesso codice fiscale (reiscrizioni).

    `soci` è {codice interno: {"campi": ..., "benemerenze": ...}} e viene modificato. Resta l'anagrafica con
    la donazione più recente; dalle altre si prendono i campi vuoti, l'iscrizione più vecchia e le benemerenze.
    La cessazione resta quella dell'anagrafica rimasta.
    Restituisce {codice fuso: codice rimasto} e il numero di benemerenze presenti in più anagrafiche fuse.
    """
    gruppi = defaultdict(list)
    for codice, socio in soci.items():
        if socio["campi"]["codice_fiscale"]:
            gruppi[socio["campi"]["codice_fiscale"]].append(codice)
    rimasto, benemerenze_doppie = {}, Counter()
    for codici in gruppi.values():
        if len(codici) == 1:
            continue
        codici.sort(key=lambda c: (ultima_donazione.get(c) or datetime.date.min, c), reverse=True)
        principale = soci[codici[0]]
        for codice in codici[1:]:
            benemerenze_doppie.update(_fondi(principale, soci.pop(codice)))
            rimasto[codice] = codici[0]
    return rimasto, benemerenze_doppie


def _fondi(principale, altro):
    p, a = principale["campi"], altro["campi"]
    for nome, valore in a.items():
        if nome not in ("data_cessazione", "causa_cessazione") and p[nome] in ("", None):
            p[nome] = valore
    p["data_iscrizione"] = _prima(p["data_iscrizione"], a["data_iscrizione"])
    for nome in PREGRESSE:
        p[nome] = max(p[nome], a[nome])
    p["no_benemerenze"] = p["no_benemerenze"] or a["no_benemerenze"]
    nota = f"Fusa con l'anagrafica AssoAVIS {a['codice_interno_assoavis']} (stesso codice fiscale)."
    p["note"] = f"{p['note']}\n{nota}".strip()
    doppie = []
    for codice, data in altro["benemerenze"].items():
        if codice in principale["benemerenze"]:
            doppie.append(codice)
        principale["benemerenze"][codice] = _prima(principale["benemerenze"].get(codice), data)
    return doppie


# --- scrittura ---

def azzera():
    """Cancella soci, donazioni, benemerenze e premiazioni, per rifare l'import durante lo sviluppo."""
    Premiazione.objects.all().delete()
    BenemerenzaAssegnata.objects.all().delete()
    Donazione.objects.all().delete()
    Socio.objects.all().delete()


def importa(storico, oggi=None):
    """Scrive lo storico in un DB senza soci e restituisce il report. Va chiamata dentro una transazione."""
    oggi = oggi or timezone.localdate()
    if Socio.objects.exists():
        raise ImportazioneError("Il DB contiene già dei soci: lo storico si importa una sola volta, su un DB vuoto.")
    report = Report()
    sezione = Sezione.objects.get(codice=CODICE_SEZIONE)
    catalogo = {b.codice: b for b in Benemerenza.objects.all()}
    tipi = {t.codice_assoavis: t for t in TipoDonazione.objects.all()}

    # soci, con la fusione dei codici fiscali duplicati
    soci = {}
    for riga in storico.soci:
        campi, anomalie = socio_da_riga(riga, oggi)
        codice = campi["codice_interno_assoavis"]
        soci[codice] = {"campi": campi, "benemerenze": benemerenze_da_riga(riga, catalogo.values())}
        report.anomalie += [("Anagrafica", codice, testo) for testo in anomalie]
    ultima_donazione = {}
    for riga in storico.donazioni:
        codice = _testo(riga["CODINTERNO"])
        ultima_donazione[codice] = max(ultima_donazione.get(codice, datetime.date.min), _data(riga["DATADON"]))
    rimasto, benemerenze_doppie = fondi_duplicati(soci, ultima_donazione)
    for fuso, codice in rimasto.items():
        report.anomalie.append(("Codice fiscale duplicato", codice, f"fusa l'anagrafica {fuso}"))
    creati = Socio.objects.bulk_create(Socio(sezione=sezione, **s["campi"]) for s in soci.values())
    id_socio = {s.codice_interno_assoavis: s.pk for s in creati}
    id_socio.update({fuso: id_socio[codice] for fuso, codice in rimasto.items()})

    # donazioni
    donazioni, viste, scartate_anno, scartate_tipo = [], set(), Counter(), Counter()
    for riga in storico.donazioni:
        codice, data = _testo(riga["CODINTERNO"]), _data(riga["DATADON"])
        socio_id, tipo = id_socio.get(codice), tipi.get(riga["TIPO"])
        motivo = ("socio non importato" if socio_id is None else f"tipo {riga['TIPO']} sconosciuto" if tipo is None
                  else "seconda donazione nello stesso giorno" if (socio_id, data) in viste else "")
        if motivo:
            report.anomalie.append(("Donazione scartata", codice, motivo))
            scartate_anno[data.year] += 1
            scartate_tipo[riga["TIPO"]] += 1
            continue
        viste.add((socio_id, data))
        donazioni.append(Donazione(socio_id=socio_id, data=data, tipo=tipo, fonte=Fonte.ASSOAVIS,
                                   centro_prelievo=_testo(riga["NOMEPUNTOPREL"])))
    Donazione.objects.bulk_create(donazioni, batch_size=5000)

    # premiazioni e premiati
    premiazioni = {}
    for riga in storico.premiazioni:
        consegna = _data(riga["DATAPREMIAZIONE"])
        consegnata = riga["ASSEGNATE"] == 1 or riga["ID"] in PREMIAZIONI_CONSEGNATE
        premiazioni[riga["ID"]] = Premiazione(
            descrizione=_testo(riga["DESCRIZIONE"]) or f"Premiazione del {consegna:%d/%m/%Y}",
            data_consegna=consegna,
            data_riferimento=_data(riga["DATARIFERIMENTO"]),
            stato=StatoPremiazione.ASSEGNATA if consegnata else StatoPremiazione.CALCOLATA,
            codice_assoavis=riga["ID"],
        )
    Premiazione.objects.bulk_create(premiazioni.values())

    # benemerenze possedute: i flag di DONATORE, più quelle delle premiazioni consegnate ma mai chiuse
    possedute = {(id_socio[codice], b): data for codice, s in soci.items() for b, data in s["benemerenze"].items()}
    premiati, visti, aggiunte = [], set(), Counter()
    for riga in storico.premiati:
        codice, id_premiazione = _testo(riga["CODINTERNODONAT"]), riga["IDPREMIAZIONE"]
        socio_id, premiazione = id_socio.get(codice), premiazioni.get(id_premiazione)
        benemerenza = catalogo.get(riga["TIPOONOREFICIENZA"])
        chiave = (id_premiazione, socio_id, riga["TIPOONOREFICIENZA"])
        if socio_id is None or premiazione is None or benemerenza is None or chiave in visti:
            report.anomalie.append(("Premiato scartato", codice, f"premiazione {id_premiazione}"))
            continue
        visti.add(chiave)
        premiati.append(Premiato(premiazione=premiazione, socio_id=socio_id, benemerenza=benemerenza,
                                 donazioni_valide=riga["NUMDONAZIONI"] or 0,
                                 donazioni_effettive=riga["NUMDONAZEFF"] or 0))
        if (socio_id, benemerenza.codice) in possedute:
            continue
        if id_premiazione in PREMIAZIONI_CONSEGNATE:
            possedute[(socio_id, benemerenza.codice)] = premiazione.data_consegna
            aggiunte[benemerenza.codice] += 1
        elif premiazione.stato == StatoPremiazione.ASSEGNATA:
            report.anomalie.append(("Premiato senza benemerenza", codice,
                                    f"{benemerenza} della premiazione {id_premiazione}: in AssoAVIS non risulta"))
    Premiato.objects.bulk_create(premiati)
    BenemerenzaAssegnata.objects.bulk_create(
        BenemerenzaAssegnata(socio_id=socio_id, benemerenza=catalogo[codice], data=data, fonte=Fonte.ASSOAVIS)
        for (socio_id, codice), data in possedute.items())

    _riconcilia(report, storico.conteggi, catalogo, tipi, fusi=len(rimasto), scartate_anno=scartate_anno,
                scartate_tipo=scartate_tipo, premiati_scartati=len(storico.premiati) - len(premiati),
                aggiunte=aggiunte, benemerenze_doppie=benemerenze_doppie)
    return report


def _riconcilia(report, conteggi, catalogo, tipi, fusi, scartate_anno, scartate_tipo, premiati_scartati,
                aggiunte, benemerenze_doppie):
    """Confronta i conteggi di SQL Server con quelli letti dal nuovo DB; le differenze attese hanno una nota."""
    report.confronta("Anagrafiche", conteggi["anagrafiche"], Socio.objects.count(), -fusi,
                     f"{fusi} fuse per codice fiscale duplicato" if fusi else "")

    donazioni = Donazione.objects.order_by()
    per_anno = dict(donazioni.annotate(anno=ExtractYear("data")).values("anno").annotate(n=Count("id"))
                    .values_list("anno", "n"))
    sorgente_anno = conteggi["donazioni_per_anno"]
    anni = sorted(set(sorgente_anno) | set(per_anno))
    vecchi = [a for a in anni if a < PRIMO_ANNO_DETTAGLIO]
    if vecchi:
        report.confronta(f"Donazioni {vecchi[0]}-{vecchi[-1]}", sum(sorgente_anno.get(a, 0) for a in vecchi),
                         sum(per_anno.get(a, 0) for a in vecchi), -sum(scartate_anno[a] for a in vecchi))
    for anno in anni:
        if anno >= PRIMO_ANNO_DETTAGLIO:
            report.confronta(f"Donazioni {anno}", sorgente_anno.get(anno, 0), per_anno.get(anno, 0),
                             -scartate_anno[anno])

    per_tipo = dict(donazioni.values("tipo__codice_assoavis").annotate(n=Count("id"))
                    .values_list("tipo__codice_assoavis", "n"))
    for codice, n in sorted(conteggi["donazioni_per_tipo"].items(), key=lambda c: c[0] or 0):
        nome = tipi[codice].codice if codice in tipi else f"tipo {codice}"
        report.confronta(f"Donazioni {nome}", n, per_tipo.get(codice, 0), -scartate_tipo[codice])

    per_benemerenza = dict(BenemerenzaAssegnata.objects.order_by().values("benemerenza__codice")
                           .annotate(n=Count("id")).values_list("benemerenza__codice", "n"))
    for b in sorted(catalogo.values(), key=lambda b: b.ordine):
        sorgente, nuove = conteggi["benemerenze"].get(b.codice, 0), per_benemerenza.get(b.codice, 0)
        if sorgente or nuove:
            note = []
            if aggiunte[b.codice]:
                note.append(f"+{aggiunte[b.codice]} dalla premiazione consegnata")
            if benemerenze_doppie[b.codice]:
                note.append(f"-{benemerenze_doppie[b.codice]} per anagrafiche fuse")
            report.confronta(f"Benemerenze {b.nome}", sorgente, nuove,
                             aggiunte[b.codice] - benemerenze_doppie[b.codice], ", ".join(note))

    report.confronta("Premiazioni", conteggi["premiazioni"], Premiazione.objects.count())
    report.confronta("Premiati", conteggi["premiati"], Premiato.objects.count(), -premiati_scartati)
