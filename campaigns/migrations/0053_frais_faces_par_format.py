from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('campaigns', '0052_frais_diffusion'),
    ]

    operations = [
        migrations.AddField(
            model_name=model_name,
            name='frais_faces_par_format',
            field=models.JSONField(blank=True, default=dict),
        )
        for model_name in ('demandereservation', 'reservation', 'campagne')
    ]
