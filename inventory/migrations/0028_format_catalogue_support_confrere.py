import re
from collections import defaultdict

from django.db import migrations, models


def associer_formats(apps, schema_editor):
    database = schema_editor.connection.alias
    formats = apps.get_model('inventory', 'FormatSupport').objects.using(database)
    supports = apps.get_model('inventory', 'SupportConfrere').objects.using(database)

    def cle(texte):
        texte = texte.casefold().replace('×', 'x').replace(',', '.')
        return re.sub(r'\s+|(?<=\d)m(?=\s*(?:x|$))', '', texte)

    codes = {}
    dimensions = defaultdict(set)
    for pk, code, mesure in formats.values_list('pk', 'code', 'dimensions'):
        codes[code.strip().casefold()] = pk
        if mesure:
            dimensions[cle(mesure)].add(pk)
        dimensions[cle(code)].add(pk)

    for pk, format_libre in supports.exclude(format_libre='').values_list('pk', 'format_libre').iterator():
        format_id = codes.get(format_libre.strip().casefold())
        correspondances = dimensions.get(cle(format_libre), set())
        if format_id is None and len(correspondances) == 1:
            format_id = next(iter(correspondances))
        if format_id is not None:
            supports.filter(pk=pk).update(format_id=format_id)


class Migration(migrations.Migration):
    dependencies = [('inventory', '0027_fiche_support_confrere')]

    operations = [
        migrations.RenameField('supportconfrere', 'format', 'format_libre'),
        migrations.AlterField(
            'supportconfrere', 'format_libre',
            models.CharField("Ancien format", max_length=80, blank=True, editable=False),
        ),
        migrations.AddField(
            'supportconfrere', 'format',
            models.ForeignKey(
                to='inventory.formatsupport', on_delete=models.PROTECT,
                blank=True, null=True, verbose_name="Format",
            ),
        ),
        migrations.RunPython(associer_formats, migrations.RunPython.noop),
        migrations.RemoveField('supportconfrere', 'type_support'),
    ]
