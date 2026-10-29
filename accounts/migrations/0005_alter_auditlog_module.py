from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_alter_auditlog_action'),
    ]

    operations = [
        migrations.AlterField(
            model_name='auditlog',
            name='module',
            field=models.CharField(
                choices=[
                    ('auth', 'Authentification'),
                    ('inventory', 'Inventaire'),
                    ('campaigns', 'Campagnes'),
                    ('clients', 'Clients'),
                    ('users', 'Utilisateurs'),
                    ('planning', 'Planning'),
                    ('map', 'Carte'),
                    ('reports', 'Rapports'),
                    ('piges', 'Piges'),
                    ('portail', 'Portail'),
                    ('core', 'Système'),
                ],
                default='inventory',
                max_length=20,
            ),
        ),
    ]
