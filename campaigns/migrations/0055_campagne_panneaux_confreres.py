from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('campaigns', '0054_reservation_panneaux_confreres')]

    operations = [
        migrations.AddField('campagne', 'panneaux_confreres', models.JSONField(default=list, blank=True)),
    ]
