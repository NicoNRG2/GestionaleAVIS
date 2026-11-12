from django.db import migrations, models


def segna_collaboratori(apps, schema_editor):
    # chi è già nello stato collaboratore fa parte dei collaboratori
    apps.get_model("soci", "Socio").objects.filter(stato="collaboratore").update(collaboratore=True)


class Migration(migrations.Migration):

    dependencies = [
        ('soci', '0004_iscrizione_assoweb'),
    ]

    operations = [
        migrations.AddField(
            model_name='socio',
            name='collaboratore',
            field=models.BooleanField(default=False, help_text='Resta socio anche senza donare: donatore finché dona, poi collaboratore.'),
        ),
        migrations.RunPython(segna_collaboratori, migrations.RunPython.noop),
    ]
