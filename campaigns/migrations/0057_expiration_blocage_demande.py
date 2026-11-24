from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('campaigns', '0056_tarif_faces_par_periode')]

    operations = [
        migrations.AddField('demandereservation', 'expiration_blocage', models.DateTimeField(null=True, blank=True)),
    ]
