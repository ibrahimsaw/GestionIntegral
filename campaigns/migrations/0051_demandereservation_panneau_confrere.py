from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('campaigns', '0050_demanderemises'),
    ]

    operations = [
        migrations.AddField(
            model_name='demandereservation',
            name='panneau_confrere',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
