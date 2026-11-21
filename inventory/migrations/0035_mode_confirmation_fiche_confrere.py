from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('inventory', '0034_mode_confirmation_confrere')]

    operations = [
        migrations.AddField(
            'confrere', 'confirmation_automatique', models.BooleanField(default=True),
        ),
    ]
