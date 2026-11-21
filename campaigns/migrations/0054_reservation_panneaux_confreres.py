from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('campaigns', '0053_frais_faces_par_format')]

    operations = [
        migrations.AddField(
            'reservation', 'panneaux_confreres_reserves', models.JSONField(default=list, blank=True),
        ),
    ]
