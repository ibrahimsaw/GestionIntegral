import re
import unicodedata

from django.db import migrations, models


def remplir_references(apps, schema_editor):
    support_model = apps.get_model('inventory', 'SupportConfrere')
    supports = support_model.objects.using(schema_editor.connection.alias)

    def mots(texte):
        ascii_text = unicodedata.normalize('NFKD', texte).encode('ascii', 'ignore').decode()
        return re.findall(r'[A-Z0-9]+', ascii_text.upper())

    for support in supports.filter(reference='').select_related('confrere').iterator():
        societe = mots(support.confrere.nom)
        prefixe = ''.join(mot[0] for mot in societe[:12]) if len(societe) > 1 else (societe[0][:3] if societe else 'SUP')
        ville = ''.join(mots(support.ville))[:3]
        reference = '-'.join(part for part in (prefixe, ville, f'{support.pk:03d}') if part)
        supports.filter(pk=support.pk).update(reference=reference)


class Migration(migrations.Migration):
    dependencies = [('inventory', '0026_coordonnees_confrere')]

    operations = [
        migrations.AddField(
            'supportconfrere', 'nom', models.CharField("Nom du support", max_length=150, blank=True),
        ),
        migrations.AddField(
            'supportconfrere', 'type_support',
            models.CharField(
                "Type", max_length=10, default='panneau',
                choices=[('panneau', 'Panneau statique'), ('ecran', 'Écran numérique')],
            ),
            preserve_default=False,
        ),
        migrations.AddField('supportconfrere', 'notes', models.TextField("Notes", blank=True)),
        migrations.AlterField('supportconfrere', 'ville', models.CharField("Ville", max_length=150, blank=True)),
        migrations.AlterField(
            'supportconfrere', 'emplacement', models.CharField("Emplacement précis", max_length=250, blank=True),
        ),
        migrations.AlterField('supportconfrere', 'format', models.CharField("Format", max_length=80, blank=True)),
        migrations.RunPython(remplir_references, migrations.RunPython.noop),
        migrations.AlterField(
            'supportconfrere', 'reference', models.CharField("Référence", max_length=80, editable=False),
        ),
    ]
