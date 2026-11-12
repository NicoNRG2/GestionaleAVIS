from django.db import migrations

# codici AssoAVIS della tabella TIPODONAZ; le emazie (globuli rossi) servono per la regola "Donne x2"
TIPI = [
    (1, "SI", "Sangue intero", True),
    (2, "PL", "Plasmaferesi", False),
    (3, "PP", "Plasmapiastrinoaferesi", False),
    (4, "PI", "Piastrinoaferesi", False),
    (5, "LE", "Leucoaferesi", False),
    (6, "CS", "Cellule staminali", False),
    (7, "AM", "Aferesi multipla", False),
    (8, "EA", "Eritroplasmaferesi", True),
    (9, "EP", "Eritropiastrinoaferesi", True),
    (10, "DP", "Doppia piastrinoaferesi", False),
    (11, "XX", "Solo presentazione", False),
]


def crea_tipi(apps, schema_editor):
    TipoDonazione = apps.get_model("soci", "TipoDonazione")
    for codice_assoavis, sigla, descrizione, emazie in TIPI:
        TipoDonazione.objects.update_or_create(
            codice=sigla,
            defaults={
                "codice_assoavis": codice_assoavis,
                "descrizione": descrizione,
                "contiene_emazie": emazie,
                "e_donazione": sigla != "XX",
            },
        )


class Migration(migrations.Migration):
    dependencies = [("soci", "0001_initial")]

    operations = [migrations.RunPython(crea_tipi, migrations.RunPython.noop)]
