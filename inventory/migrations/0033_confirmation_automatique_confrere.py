from django.db import migrations, models
from django.utils import timezone


def confirmer_dossiers_en_attente(apps, schema_editor):
    dossiers = apps.get_model('inventory', 'DossierConfrere').objects.using(schema_editor.connection.alias)
    dossiers.filter(actif=True, reponse_confrere='en_attente').update(
        reponse_confrere='automatique', repondu_le=timezone.now(),
    )


def remettre_dossiers_en_attente(apps, schema_editor):
    dossiers = apps.get_model('inventory', 'DossierConfrere').objects.using(schema_editor.connection.alias)
    dossiers.filter(reponse_confrere='automatique').update(reponse_confrere='en_attente', repondu_le=None)


class Migration(migrations.Migration):
    dependencies = [('inventory', '0032_validation_dossier_confrere')]

    operations = [
        migrations.AlterField(
            'dossierconfrere', 'reponse_confrere',
            models.CharField(max_length=12, default='en_attente', choices=[
                ('en_attente', 'En attente du confrère'), ('confirmee', 'Confirmée par le confrère'),
                ('refusee', 'Refusée par le confrère'), ('automatique', 'Confirmée automatiquement'),
            ]),
        ),
        migrations.RunPython(confirmer_dossiers_en_attente, remettre_dossiers_en_attente),
    ]
