"""Export di una premiazione in Excel, per preparare la cerimonia: riepilogo, premiati con i contatti, esclusi."""

from collections import Counter
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from . import regole

GRASSETTO = Font(bold=True)
DATA = "DD/MM/YYYY"


def _intestazione(foglio, colonne):
    foglio.append(colonne)
    for cella in foglio[foglio.max_row]:
        cella.font = GRASSETTO


def _larghezze(foglio, larghezze):
    for colonna, larghezza in enumerate(larghezze, start=1):
        foglio.column_dimensions[get_column_letter(colonna)].width = larghezza


def _date(foglio, colonne):
    for riga in foglio.iter_rows(min_row=2):
        for indice in colonne:
            riga[indice].number_format = DATA


def cartella(premiazione, premiati, esclusi):
    """File .xlsx (in byte); `premiati` ed `esclusi` sono i PremiatoSocio di calcolo.premiati_per_socio."""
    libro = Workbook()
    riepilogo = libro.active
    riepilogo.title = "Riepilogo"
    riepilogo.append([premiazione.descrizione])
    riepilogo["A1"].font = Font(bold=True, size=12)
    statuto = regole.statuto(premiazione.data_riferimento) if premiazione.data_riferimento else None
    for etichetta, valore in [
        ("Stato", premiazione.get_stato_display()),
        ("Data di consegna", premiazione.data_consegna),
        ("Donazioni contate fino al", premiazione.data_riferimento),
        ("Ex soci premiati se cessati dopo il", premiazione.ex_soci_cessati_dal or "nessun ex socio"),
        ("Regole", statuto.nome if statuto else ""),
    ]:
        riepilogo.append([etichetta, valore])
        if hasattr(valore, "year"):
            riepilogo.cell(riepilogo.max_row, 2).number_format = DATA
    riepilogo.append([])
    _intestazione(riepilogo, ["Benemerenza consegnata", "Soci"])
    per_benemerenza = Counter(g.principale.benemerenza for g in premiati)
    for benemerenza, n in sorted(per_benemerenza.items(), key=lambda b: b[0].ordine):
        riepilogo.append([benemerenza.nome, n])
    riepilogo.append(["Totale", len(premiati)])
    riepilogo.cell(riepilogo.max_row, 1).font = GRASSETTO
    if esclusi:
        riepilogo.append(["Esclusi", len(esclusi)])
    _larghezze(riepilogo, [36, 16])

    foglio = libro.create_sheet("Premiati")
    _intestazione(foglio, ["Benemerenza", "Matura anche", "Cognome", "Nome", "Nascita", "Indirizzo", "CAP", "Comune",
                           "Prov.", "Cellulare", "Email", "Donazioni valide", "Donazioni effettive", "Iscrizione",
                           "Stato", "ID socio", "Note"])
    for g in premiati:
        s, p = g.socio, g.principale
        foglio.append([p.benemerenza.nome, ", ".join(b.nome for b in g.insieme_a), s.cognome, s.nome, s.data_nascita,
                       s.indirizzo_residenza, s.cap_residenza, s.comune_residenza, s.provincia_residenza, s.cellulare,
                       s.email, p.donazioni_valide, p.donazioni_effettive, s.data_iscrizione, s.get_stato_display(),
                       s.pk, f"Aggiunto a mano: {p.motivo}" if p.aggiunto_a_mano else ""])
    _date(foglio, [4, 13])
    _larghezze(foglio, [22, 22, 18, 16, 12, 30, 7, 20, 6, 14, 28, 10, 10, 12, 18, 9, 30])
    foglio.freeze_panes = "A2"

    if esclusi:
        foglio = libro.create_sheet("Esclusi")
        _intestazione(foglio, ["Benemerenza", "Cognome", "Nome", "ID socio", "Motivo"])
        for g in esclusi:
            foglio.append([g.principale.benemerenza.nome, g.socio.cognome, g.socio.nome, g.socio.pk,
                           g.principale.motivo])
        _larghezze(foglio, [22, 18, 16, 9, 50])

    contenuto = BytesIO()
    libro.save(contenuto)
    return contenuto.getvalue()
