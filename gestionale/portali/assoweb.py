"""Client Playwright per AssoWeb, il portale regionale (README M1 e M6).

Il client non usa l'ORM di Django: dentro `sync_playwright()` gira un event loop asyncio nel thread corrente e
Django rifiuta ogni accesso al DB con SynchronousOnlyOperation, anche solo `connection.close()`. Chi lo chiama
legge dal DB prima (nome della sezione) e scrive dopo (import, registro eventi); il worker lo esegue in un thread
separato e riceve l'avanzamento con la funzione `avanza`, nel thread di Playwright.

La UI è Angular Material (`mat-select`, `mat-option`, pannelli espandibili); il caricamento dei report si attende
sullo spinner `.block-ui-wrapper.active`.
"""

import datetime
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from gestionale.importazioni.assoweb import FILE_DONATORI, FILE_DONAZIONI

URL_LOGIN = "https://sit.azero.veneto.it/assoweb/authentication/login"
PROVIDER = "AULSS 8 BERICA"
REPORT_DONATORI = "Donatori Totali - Lista di tutti i donatori di una determinata sezione e/o associazione"
REPORT_DONAZIONI = "Totale donazioni - La lista restituisce, in un determinato periodo, la lista di tutte le donazioni"
TIMEOUT_MS = 30_000
# un report di tutta la sezione può richiedere più di un'azione normale
ATTESA_REPORT_MS = 60_000
# dopo la ricerca: se entro questo tempo non compare nessuna riga, il filtro non ha trovato donatori
ATTESA_RIGHE_MS = 5_000
# l'invio di qualche centinaio di SMS
ATTESA_INVIO_MS = 120_000
# 100 donatori per pagina: nessun filtro della sezione arriva a tanto, il limite ferma i cicli senza fine
MAX_PAGINE = 50
# tag, id e classi dell'elemento che copre il centro di `el` (niente testo: può contenere nomi); null se nessuno
COPERTO_DA = """el => {
    const r = el.getBoundingClientRect();
    const sopra = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
    if (!sopra || el.contains(sopra)) return null;
    return sopra.tagName.toLowerCase() + (sopra.id ? "#" + sopra.id : "") + [...sopra.classList].map(c => "." + c).join("");
}"""
# righe della tabella dei risultati: mat-table può essere una <table> o fatta con i tag di Angular Material
RIGA = "tr, mat-header-row, mat-row"
# has_text cerca nel testo della riga, dove le celle sono attaccate ("CognomeNomeData nascita"): basta una colonna
INTESTAZIONE_RISULTATI = "Data nascita"
CASELLA = "input[type=checkbox]"


class ErrorePortale(Exception):
    """Automazione fallita; `screenshot` è il percorso dell'immagine della pagina al momento dell'errore."""

    def __init__(self, messaggio, screenshot=None):
        super().__init__(messaggio)
        self.screenshot = screenshot


def _niente(messaggio):
    pass


@contextmanager
def sessione(username, password, *, headless=True, cartella_errori=None, timeout_ms=TIMEOUT_MS, avanza=_niente):
    """Browser con il login già fatto: restituisce la pagina sulla home di AssoWeb.

    Qualunque errore, anche nel blocco `with` del chiamante, diventa ErrorePortale con lo screenshot della pagina
    salvato in `cartella_errori`. Lo screenshot può mostrare dati personali: va tenuto come i CSV di download/.
    """
    if not username or not password:
        raise ErrorePortale("Credenziali di AssoWeb mancanti: impostare APP_USERNAME_AVIS e APP_PASSWORD_AVIS in .env")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        try:
            pagina = browser.new_page(accept_downloads=True)
            pagina.set_default_timeout(timeout_ms)
            try:
                login(pagina, username, password)
                avanza("Login su AssoWeb effettuato")
                yield pagina
            except Exception as errore:
                # dei messaggi di Playwright si tiene la prima riga: il resto è il log delle chiamate
                messaggio = str(errore) if isinstance(errore, ErrorePortale) else str(errore).splitlines()[0]
                raise ErrorePortale(messaggio, _screenshot(pagina, cartella_errori)) from errore
        finally:
            browser.close()


def login(pagina, username, password):
    """Login OpenID Connect con il provider dell'AULSS 8 Berica; termina sulla home di AssoWeb."""
    pagina.goto(URL_LOGIN)
    # il firewall del portale (FortiWeb, "Known Bots Detection") blocca Chromium headless per lo user agent
    if pagina.get_by_text("Web Page Blocked").count():
        raise ErrorePortale("Il firewall di AssoWeb ha bloccato la pagina di login: succede con il browser headless, "
                            "impostare PORTALI_HEADLESS=0 in .env o usare --mostra-browser")
    pagina.locator("div.card", has_text="Autenticazione con OpenID Connect").locator("button").click()
    pagina.locator("#wayf").select_option(label=PROVIDER)
    pagina.locator("#accesso").click()
    pagina.locator("#username").fill(username)
    pagina.locator("#password").fill(password)
    pagina.locator("button[type='submit']").click()
    # con le credenziali sbagliate il form di SmartIDP resta aperto e il menu di AssoWeb non compare
    try:
        pagina.locator("a", has_text="Statistiche e reports").wait_for()
    except PlaywrightTimeoutError:
        raise ErrorePortale("Login su AssoWeb non riuscito: controllare le credenziali") from None


def scarica_csv(pagina, nome_sezione, anno, cartella, avanza=_niente):
    """Dalla home di AssoWeb scarica i donatori e le donazioni dell'anno della sezione. I file vengono sovrascritti."""
    cartella = Path(cartella)
    cartella.mkdir(parents=True, exist_ok=True)

    pagina.locator("a", has_text="Statistiche e reports").click()
    pannello = pagina.locator("#mat-expansion-panel-header-0")
    _chiudi_flyout(pagina, pannello)
    pannello.click()

    pagina.locator("div.link", has_text=REPORT_DONATORI).click()
    _filtra_sezione(pagina, nome_sezione)
    donatori = _esporta(pagina, cartella / FILE_DONATORI)
    avanza(f"Scaricato {FILE_DONATORI}")
    pagina.locator("button", has_text="Indietro").click()

    pagina.locator("div.link", has_text=REPORT_DONAZIONI).click()
    pagina.locator("input[placeholder='DA']").fill(f"01/01/{anno}")
    pagina.locator("input[placeholder='A']").fill(f"31/12/{anno}")
    # chiude il datepicker rimasto aperto
    pagina.keyboard.press("Escape")
    _filtra_sezione(pagina, nome_sezione)
    donazioni = _esporta(pagina, cartella / FILE_DONAZIONI)
    avanza(f"Scaricato {FILE_DONAZIONI} del {anno}")
    return donatori, donazioni


def scarica(username, password, nome_sezione, cartella, *, anno=None, headless=True, cartella_errori=None,
            timeout_ms=TIMEOUT_MS, avanza=_niente):
    """Login e download dei due CSV; l'anno delle donazioni di default è quello corrente."""
    anno = anno or datetime.date.today().year
    with sessione(username, password, headless=headless, cartella_errori=cartella_errori, timeout_ms=timeout_ms,
                  avanza=avanza) as pagina:
        return scarica_csv(pagina, nome_sezione, anno, cartella, avanza)


def sms_convocazione(pagina, filtro, testo, *, giornata=None, invia=True, avanza=_niente):
    """Dalla home di AssoWeb manda l'SMS `testo` ai donatori trovati da *Ricerca e convocazione* (README M6).

    I criteri sono quelli del filtro salvato sul portale con il nome `filtro`; `giornata` imposta la *Data prevista
    donazione* (posti liberi). I risultati sono a pagine di 100: si spunta la casella di intestazione di ogni pagina,
    e la selezione resta passando alla successiva. Con `invia=False` la finestra *Invia SMS* si chiude con *Annulla*
    (simulazione: non parte niente). Restituisce il numero dei donatori selezionati; con zero non apre la finestra.
    """
    pagina.locator("a", has_text="Ricerca e convocazione").click()
    # la pagina si apre con lo spinner mentre carica i filtri salvati
    _attendi_caricamento(pagina)
    # si clicca il campo, non la mat-select: finché è vuota, sopra c'è l'etichetta "Filtri di ricerca" e Playwright
    # aspetterebbe che si sposti. Il click sul campo apre la select, come per un utente
    filtri = pagina.locator("mat-form-field", has_text="Filtri di ricerca")
    _chiudi_flyout(pagina, filtri)
    filtri.click()
    pagina.locator("mat-option").first.wait_for()
    # has_text troverebbe anche i filtri che contengono il nome ("test" e "test2"): serve il nome esatto
    opzione = pagina.locator("mat-option").get_by_text(filtro, exact=True)
    if not opzione.count():
        raise ErrorePortale(f"Il filtro «{filtro}» non è tra i filtri di ricerca salvati su AssoWeb")
    opzione.click()
    if giornata:
        # è il titolo di un pannello espandibile (mat-expansion-panel-header), non un <button>
        pagina.locator("button, mat-expansion-panel-header", has_text="Idoneità e donazioni").click()
        # come per il filtro: sulla select vuota c'è l'etichetta
        pagina.locator("mat-form-field", has_text="Tipo donazione").click()
        sangue_intero = pagina.locator("mat-option", has_text="Sangue intero")
        # se il filtro salvato lo ha già, in una selezione multipla il click lo toglierebbe
        if sangue_intero.get_attribute("aria-selected") != "true":
            sangue_intero.click()
        pagina.keyboard.press("Escape")
        pagina.locator("mat-form-field", has_text="Data prevista donazione").locator("input").fill(
            f"{giornata:%d/%m/%Y}")
        # chiude il datepicker, se si è aperto
        pagina.keyboard.press("Escape")
    avanza(f"Ricerca e convocazione con il filtro {filtro}"
           + (f" e la data prevista donazione {giornata:%d/%m/%Y}" if giornata else ""))
    pagina.locator("button", has_text="Cerca").click()
    _attendi_caricamento(pagina)

    intestazione = pagina.locator(RIGA, has_text=INTESTAZIONE_RISULTATI)
    intestazione.wait_for(timeout=ATTESA_REPORT_MS)
    # righe dei donatori: hanno la casella di selezione, l'intestazione no ("Data nascita" c'è solo lì)
    righe = pagina.locator(RIGA).filter(has=pagina.locator(CASELLA)).filter(has_not_text=INTESTAZIONE_RISULTATI)
    try:
        righe.first.wait_for(timeout=ATTESA_RIGHE_MS)
    except PlaywrightTimeoutError:
        avanza("Nessun donatore trovato")
        return 0

    # il bottone ha solo l'icona: "Pagina successiva" è nell'aria-label, has_text non lo trova
    successiva = pagina.locator("button[aria-label='Pagina successiva']")
    selezionati = 0
    for numero in range(1, MAX_PAGINE + 1):
        intestazione.locator(CASELLA).check()
        sulla_pagina = righe.filter(has=pagina.locator(f"{CASELLA}:checked")).count()
        avanza(f"Pagina {numero}: {sulla_pagina} donatori selezionati su {righe.count()}")
        selezionati += sulla_pagina
        if not (successiva.count() and successiva.is_enabled()):
            break
        _pagina_successiva(pagina, righe, successiva)
    else:
        raise ErrorePortale(f"Più di {MAX_PAGINE} pagine di risultati: il filtro sembra sbagliato. Nessun SMS inviato")

    if not selezionati:
        avanza("Nessun donatore selezionato")
        return 0
    pagina.locator("button", has_text="Invia SMS").click()
    pagina.locator("mat-form-field", has_text="Testo").locator("textarea").fill(testo)
    if not invia:
        pagina.locator("button", has_text="Annulla").click()
        avanza("Simulazione: finestra Invia SMS chiusa con Annulla, nessun SMS inviato")
        return selezionati
    # il banner "Messaggio inviato" sparisce subito: un osservatore del DOM se lo ricorda
    pagina.evaluate("""() => {
        window.messaggioInviato = false;
        new MutationObserver(mutazioni => {
            for (const mutazione of mutazioni)
                for (const nodo of mutazione.addedNodes)
                    if (nodo.textContent?.includes("Messaggio inviato")) window.messaggioInviato = true;
        }).observe(document.body, {childList: true, subtree: true});
    }""")
    pagina.locator("button", has_text="Conferma").click()
    try:
        pagina.wait_for_function("() => window.messaggioInviato", timeout=ATTESA_INVIO_MS)
    except PlaywrightTimeoutError:
        raise ErrorePortale("Dopo «Conferma» AssoWeb non ha mostrato «Messaggio inviato»: gli SMS potrebbero essere "
                            "partiti, va controllato sul portale prima di riprovare") from None
    avanza("AssoWeb ha risposto «Messaggio inviato»")
    return selezionati


def invia_sms(username, password, filtro, testo, *, giornata=None, invia=True, headless=True, cartella_errori=None,
              timeout_ms=TIMEOUT_MS, avanza=_niente):
    """Login e SMS ai donatori del filtro salvato (vedi `sms_convocazione`); restituisce i donatori selezionati."""
    with sessione(username, password, headless=headless, cartella_errori=cartella_errori, timeout_ms=timeout_ms,
                  avanza=avanza) as pagina:
        return sms_convocazione(pagina, filtro, testo, giornata=giornata, invia=invia, avanza=avanza)


def _pagina_successiva(pagina, righe, successiva):
    # la tabella cambia senza ricaricare la pagina: si aspetta che la prima riga sia un'altra. Il testo della riga
    # (nome e data di nascita) resta nel browser: un'asserzione di Playwright lo metterebbe nel messaggio d'errore
    vecchia = righe.first.element_handle()
    testo = vecchia.inner_text()
    successiva.click()
    pagina.wait_for_function("([riga, testo]) => !riga.isConnected || riga.innerText !== testo",
                             arg=[vecchia, testo], timeout=ATTESA_REPORT_MS)
    righe.first.wait_for()


def _chiudi_flyout(pagina, bersaglio):
    # il flyout della sidebar resta aperto dopo il click sul menu e copre la pagina: due movimenti del mouse per
    # far scattare il mouseleave
    pagina.mouse.move(800, 50)
    pagina.mouse.move(800, 300)
    pagina.wait_for_timeout(300)
    # Se qualcosa copre il primo elemento da usare, Playwright aspetta in silenzio fino al timeout (o finché l'utente
    # non muove il mouse): il click di prova (trial, senza eventi) lo scopre e l'errore dice cosa lo copre
    try:
        bersaglio.click(trial=True)
    except PlaywrightTimeoutError:
        sopra = bersaglio.evaluate(COPERTO_DA) if bersaglio.count() == 1 else None
        if sopra:
            raise ErrorePortale(f"Il primo elemento della pagina è coperto da {sopra}") from None
        raise


def _attendi_caricamento(pagina):
    # lo spinner può comparire e sparire prima che lo si veda
    spinner = pagina.locator(".block-ui-wrapper.active")
    try:
        spinner.wait_for(state="visible", timeout=3000)
    except PlaywrightTimeoutError:
        pass
    spinner.wait_for(state="hidden", timeout=ATTESA_REPORT_MS)


def _filtra_sezione(pagina, nome_sezione):
    pagina.locator("mat-select").click()
    pagina.locator("mat-option", has_text=nome_sezione).click()
    pagina.keyboard.press("Escape")
    pagina.locator("button", has_text="Conferma").click()
    _attendi_caricamento(pagina)


def _esporta(pagina, percorso):
    with pagina.expect_download() as download:
        pagina.locator("button", has_text="Esporta").click()
    download.value.save_as(percorso)
    return percorso


def _screenshot(pagina, cartella):
    if cartella is None:
        return None
    try:
        Path(cartella).mkdir(parents=True, exist_ok=True)
        percorso = Path(cartella) / f"assoweb_{datetime.datetime.now():%Y%m%d_%H%M%S}.png"
        pagina.screenshot(path=percorso, full_page=True)
        return percorso
    except Exception:
        # il browser può essersi già chiuso: lo screenshot non deve nascondere l'errore originale
        return None
