"""Export delle statistiche in Excel: un foglio per tabella, con titolo, intestazione e note."""

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

GRASSETTO = Font(bold=True)


def cartella(tabelle, intestazione):
    """File .xlsx (in byte) con le `Tabella` di calcolo.py; `intestazione` va sotto il titolo di ogni foglio."""
    libro = Workbook()
    libro.remove(libro.active)
    for tabella in tabelle:
        foglio = libro.create_sheet(tabella.foglio)
        foglio.append([tabella.titolo])
        foglio["A1"].font = Font(bold=True, size=12)
        foglio.append([intestazione])
        if tabella.nota:
            foglio.append([tabella.nota])
        foglio.append([])
        foglio.append(tabella.colonne)
        for cella in foglio[foglio.max_row]:
            cella.font = GRASSETTO
        for riga in tabella.righe:
            foglio.append(riga)
        if tabella.totale:
            foglio.append(tabella.totale)
            for cella in foglio[foglio.max_row]:
                cella.font = GRASSETTO
        # la prima colonna contiene le etichette delle righe; titolo e note restano a sinistra e possono sbordare
        foglio.column_dimensions["A"].width = max(len(str(riga[0])) for riga in [tabella.colonne, *tabella.righe,
                                                                                     tabella.totale or [""]]) + 2
        for colonna in range(2, len(tabella.colonne) + 1):
            foglio.column_dimensions[get_column_letter(colonna)].width = 14
    contenuto = BytesIO()
    libro.save(contenuto)
    return contenuto.getvalue()
