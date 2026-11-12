import datetime

import pytest
from django.db import IntegrityError

from gestionale.benemerenze.models import Benemerenza, Premiato, Premiazione, StatoPremiazione
from gestionale.core.models import Sezione
from gestionale.soci.models import Socio


def test_catalogo(db):
    assert sorted(Benemerenza.objects.values_list("codice", flat=True)) == list(range(1, 13))
    # nomi storici fuorvianti di AssoAVIS: FLAGMEDBRONZO è l'Argento
    assert Benemerenza.objects.get(colonna_flag_assoavis="FLAGMEDBRONZO").nome == "Argento"
    donatori = list(Benemerenza.objects.filter(per_collaboratori=False).exclude(codice=11).values_list("codice", flat=True))
    assert donatori == [1, 2, 3, 4, 5, 6, 10]


def test_premiato_unico_per_benemerenza(db):
    socio = Socio.objects.create(sezione=Sezione.objects.get(codice="1529"), cognome="Rossi", nome="Anna")
    premiazione = Premiazione.objects.create(descrizione="Premiazione 2026", data_consegna=datetime.date(2026, 12, 8))
    assert premiazione.stato == StatoPremiazione.BOZZA
    rame, argento = Benemerenza.objects.get(codice=1), Benemerenza.objects.get(codice=2)
    # due benemerenze diverse nella stessa premiazione sono ammesse, la stessa due volte no
    Premiato.objects.create(premiazione=premiazione, socio=socio, benemerenza=rame, donazioni_valide=8,
                            donazioni_effettive=8)
    Premiato.objects.create(premiazione=premiazione, socio=socio, benemerenza=argento, donazioni_valide=16,
                            donazioni_effettive=16)
    with pytest.raises(IntegrityError):
        Premiato.objects.create(premiazione=premiazione, socio=socio, benemerenza=rame, donazioni_valide=8,
                                donazioni_effettive=8)
