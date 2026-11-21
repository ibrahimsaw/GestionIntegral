from django.db import migrations, models


def conserver_mode_manuel(apps, schema_editor):
    dossiers = apps.get_model('inventory', 'DossierConfrere').objects.using(schema_editor.connection.alias)
    dossiers.filter(reponse_confrere__in=['confirmee', 'refusee']).update(confirmation_automatique=False)


class Migration(migrations.Migration):
    dependencies = [('inventory', '0033_confirmation_automatique_confrere')]

    operations = [
        migrations.AddField(
            'dossierconfrere', 'confirmation_automatique', models.BooleanField(default=True),
        ),
        migrations.RunPython(conserver_mode_manuel, migrations.RunPython.noop),
    ]
