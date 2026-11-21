from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('inventory', '0028_format_catalogue_support_confrere')]

    operations = [
        migrations.AddField(
            'supportconfrere', 'categorie',
            models.CharField("Catégorie", max_length=50, blank=True),
        ),
    ]
