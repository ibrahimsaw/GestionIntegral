from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('inventory', '0025_reseau_confrere')]

    operations = [
        migrations.RenameField('confrere', 'contact', 'nom_contact'),
        migrations.AlterField(
            'confrere', 'nom_contact',
            models.CharField("Nom du contact", blank=True, max_length=150),
        ),
        migrations.AddField(
            'confrere', 'prenom_contact',
            models.CharField("Prénom du contact", blank=True, max_length=150),
        ),
        migrations.AddField(
            'confrere', 'whatsapp',
            models.CharField("Numéro WhatsApp", blank=True, max_length=40),
        ),
        migrations.AlterField(
            'confrere', 'nom',
            models.CharField("Nom de la société", max_length=150, unique=True),
        ),
        migrations.AlterField(
            'confrere', 'telephone',
            models.CharField("Numéro de téléphone", blank=True, max_length=40),
        ),
        migrations.AlterField(
            'confrere', 'email',
            models.EmailField("Adresse e-mail", blank=True, max_length=254),
        ),
    ]
