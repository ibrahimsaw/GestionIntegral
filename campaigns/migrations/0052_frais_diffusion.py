from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('campaigns', '0051_demandereservation_panneau_confrere'),
    ]

    operations = [
        migrations.AddField(
            model_name=model_name,
            name=field_name,
            field=models.DecimalField(blank=True, decimal_places=2, default=0, max_digits=14),
        )
        for model_name in ('demandereservation', 'reservation')
        for field_name in ('prix_affichage', 'prix_impression')
    ]
