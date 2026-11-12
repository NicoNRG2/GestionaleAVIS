from django.db import migrations


def crea_sezione(apps, schema_editor):
    Sezione = apps.get_model("core", "Sezione")
    Sezione.objects.update_or_create(
        codice="1529",
        defaults={"nome": "AVIS Comunale Altopiano di Asiago", "nome_assoweb": "AVIS Comunale Altopiano di Asiago"},
    )


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]

    operations = [migrations.RunPython(crea_sezione, migrations.RunPython.noop)]
