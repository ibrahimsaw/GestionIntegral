from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('campaigns', '0055_campagne_panneaux_confreres')]

    operations = [
        migrations.AddField('demandereservation', 'tarif_faces_par_periode', models.BooleanField(default=False)),
        migrations.AddField('reservation', 'tarif_faces_par_periode', models.BooleanField(default=False)),
        migrations.AddField('campagne', 'tarif_faces_par_periode', models.BooleanField(default=False)),
    ]
