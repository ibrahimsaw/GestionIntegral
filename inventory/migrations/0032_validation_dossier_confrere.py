import uuid

from django.db import migrations, models


def initialiser_validations(apps, schema_editor):
    dossiers = apps.get_model('inventory', 'DossierConfrere').objects.using(schema_editor.connection.alias)
    for dossier in dossiers.select_related('demande').iterator():
        demande = dossier.demande
        dossiers.filter(pk=dossier.pk).update(
            token_validation=uuid.uuid4(),
            periode_validation=f'{demande.date_debut_souhaitee}/{demande.date_fin_souhaitee}',
        )


class Migration(migrations.Migration):
    dependencies = [('inventory', '0031_dossiers_confreres')]

    operations = [
        migrations.AddField('dossierconfrere', 'token_validation', models.UUIDField(null=True, editable=False)),
        migrations.AddField('dossierconfrere', 'periode_validation', models.CharField(max_length=21, blank=True, editable=False)),
        migrations.AddField(
            'dossierconfrere', 'reponse_confrere',
            models.CharField(max_length=12, default='en_attente', choices=[
                ('en_attente', 'En attente du confrère'), ('confirmee', 'Confirmée par le confrère'),
                ('refusee', 'Refusée par le confrère'),
            ]),
        ),
        migrations.AddField('dossierconfrere', 'repondu_le', models.DateTimeField(null=True, blank=True)),
        migrations.RunPython(initialiser_validations, migrations.RunPython.noop),
        migrations.AlterField(
            'dossierconfrere', 'token_validation',
            models.UUIDField(default=uuid.uuid4, unique=True, editable=False),
        ),
    ]
