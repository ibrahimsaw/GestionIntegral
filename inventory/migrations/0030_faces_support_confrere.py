from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('inventory', '0029_categorie_support_confrere')]

    operations = [
        migrations.AddField(
            'supportconfrere', 'faces_selectionnees', models.JSONField(default=list, blank=True),
        ),
    ]
