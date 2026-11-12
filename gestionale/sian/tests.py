from gestionale.core.models import Esecuzione, TipoEsecuzione
from gestionale.sian.models import EsitoSian, InvioSian, InvioSianRiga, OperazioneSian


def test_riga_di_cancellazione_senza_socio(db):
    invio = InvioSian.objects.create(anno=2026, esecuzione=Esecuzione.objects.create(tipo=TipoEsecuzione.INVIO_SIAN))
    # un record letto dal SIAN che non corrisponde a nessun socio del DB
    InvioSianRiga.objects.create(invio=invio, codice_fiscale="RSSNNA80A41A001X",
                                 operazione=OperazioneSian.CANCELLAZIONE, esito=EsitoSian.OK)
    assert invio.righe.get().socio is None
    assert invio.esecuzione.invio_sian == invio
