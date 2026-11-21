from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('inventory', '0035_mode_confirmation_fiche_confrere')]

    operations = [
        migrations.AddField('confrere', 'archive', models.BooleanField(default=False)),
        migrations.AddField('supportconfrere', 'archive', models.BooleanField(default=False)),
    ]
