from django.db import migrations

# catalogo AssoAVIS (POPUPINTERNI, IDTIPO=109): codice, nome, per collaboratori, ordine, colonne flag/data di DONATORE
CATALOGO = [
    (1, "Rame", False, 10, "FLAGBENEMERENZ", "DATABENEMERENZ"),
    (2, "Argento", False, 20, "FLAGMEDBRONZO", "DATAMEDBRONZO"),
    (3, "Argento dorato", False, 30, "FLAGMEDARGENT", "DATAMEDARGENT"),
    (4, "Oro", False, 40, "FLAGMEDORO", "DATAMEDORO"),
    (5, "Oro con Rubino", False, 50, "FLAGDISTINTIVO", "DATADISTINTIVO"),
    (6, "Oro con Smeraldo", False, 60, "FLAGCROCEORO", "DATACROCEORO"),
    (10, "Oro con Diamante", False, 70, "FLAGPREMAGG", "DATAPREMAGG"),
    (7, "Argento (collaboratori)", True, 110, "FLAGGOCCIABRO", "DATAGOCCIABRO"),
    (8, "Argento dorato (collaboratori)", True, 120, "FLAGGOCCIAARG", "DATAGOCCIAARG"),
    (9, "Oro (collaboratori)", True, 130, "FLAGGOCCIAORO", "DATAGOCCIAORO"),
    (12, "Oro con Rubino (collaboratori)", True, 140, "FLAGRUBINOCOLL", "DATARUBINOCOLL"),
    (11, "Altra benemerenza", False, 200, "FLAGALTRABENE", "DATAALTRABEN"),
]


def crea_catalogo(apps, schema_editor):
    Benemerenza = apps.get_model("benemerenze", "Benemerenza")
    for codice, nome, collaboratori, ordine, flag, data in CATALOGO:
        Benemerenza.objects.update_or_create(
            codice=codice,
            defaults={
                "nome": nome,
                "per_collaboratori": collaboratori,
                "ordine": ordine,
                "colonna_flag_assoavis": flag,
                "colonna_data_assoavis": data,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("benemerenze", "0001_initial")]

    operations = [migrations.RunPython(crea_catalogo, migrations.RunPython.noop)]
