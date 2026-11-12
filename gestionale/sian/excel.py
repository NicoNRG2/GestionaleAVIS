"""Export del libro soci nel formato dell'Uploader SIAN (manuale SianUploader, Ver.06 del 05/03/2025).

È il ripiego se il web service non funziona: l'Uploader legge il file e sostituisce l'intero registro della sede.
Regole del file: intestazioni esatte sulla prima riga, date `gg/mm/aaaa`, `GENERE` M/F, `CELLULARENONDISP` ed
`EMAILNONDISP` a 1 quando il dato manca, `CAUSACESSAZIONE` solo con la data di cessazione. I codici e le date si
scrivono come testo, così Excel non toglie gli zeri iniziali (CAP, cellulare).
"""

import datetime
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from .libro_soci import COLONNE

NUMERI = {"ANNORIFERIMENTO", "CELLULARENONDISP", "EMAILNONDISP", "TIPOSOCIO", "CAUSACESSAZIONE"}


def _valore(colonna, valore):
    if valore is None:
        return None
    if isinstance(valore, datetime.date):
        return valore.strftime("%d/%m/%Y")
    return valore if colonna in NUMERI else str(valore)


def cartella(libro):
    """File .xlsx (in byte) con i soci che superano i controlli: gli scartati il SIAN li rifiuterebbe."""
    libro_excel = Workbook()
    foglio = libro_excel.active
    foglio.title = "Registro soci"
    foglio.append(COLONNE)
    for cella in foglio[1]:
        cella.font = Font(bold=True)
    for record in libro.inviabili:
        foglio.append([_valore(colonna, record.campi[colonna]) for colonna in COLONNE])
    for indice, colonna in enumerate(COLONNE, start=1):
        foglio.column_dimensions[get_column_letter(indice)].width = max(len(colonna) + 2, 12)
    foglio.freeze_panes = "A2"
    uscita = BytesIO()
    libro_excel.save(uscita)
    return uscita.getvalue()
