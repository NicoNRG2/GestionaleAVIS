from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sian', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='inviosian',
            name='sede',
            # gli invii già presenti erano tutti della sezione 1529
            field=models.CharField(default='1529', max_length=10),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='inviosian',
            name='modalita',
            field=models.CharField(choices=[('totale', 'Registro completo'), ('singolo', 'Un socio')], default='totale', max_length=10),
        ),
        migrations.AddField(
            model_name='inviosian',
            name='id_invio',
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name='inviosian',
            name='esito_il',
            field=models.DateTimeField(blank=True, null=True, verbose_name='esito del SIAN del'),
        ),
        migrations.AlterField(
            model_name='inviosianriga',
            name='esito',
            field=models.CharField(choices=[('ok', 'Riuscito'), ('errore', 'Errore'), ('inviato', "In attesa dell'esito"), ('scartato', 'Non inviato')], max_length=10),
        ),
    ]
