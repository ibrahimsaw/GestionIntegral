import os
import django

# Configuration de l'environnement Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'geoad.settings') # Remplace 'geoad_erp' par le nom de ton dossier de configuration principale
django.setup()

from inventory.models import FormatSupport

FORMATS_A_INSERER = [
    # Code, Libellé, Dimensions, Superficie, Catégorie
    ('4x3', '4m × 3m (12m²) — Standard', '4m × 3m', 12.0, 'Standard'),
    ('4x5', '4m × 5m (20m²) — Géant', '4m × 5m', 20.0, 'Géant'),
    ('7x3', '7m × 3m (21m²) — Géant', '7m × 3m', 21.0, 'Géant'),
    ('6x4', '6m × 4m (24m²) — Géant', '6m × 4m', 24.0, 'Géant'),
    ('8x3', '8m × 3m (24m²) — Géant', '8m × 3m', 24.0, 'Géant'),
    ('8x6', '8m × 6m (48m²) — Géant', '8m × 6m', 48.0, 'Géant'),
    ('6x8', '6m × 8m (48m²) — Géant', '6m × 8m', 48.0, 'Géant'),
    ('10x4', '10m × 4m (40m²) — Géant', '10m × 4m', 40.0, 'Géant'),
    ('12x4', '12m × 4m (48m²) — Géant', '12m × 4m', 48.0, 'Géant'),
    ('12x5', '12m × 5m (60m²) — Géant', '12m × 5m', 60.0, 'Géant'),
    ('24x4', '24m × 4m (96m²) — Géant', '24m × 4m', 96.0, 'Géant'),
    ('9x5', '9m × 5m (45m²) — Géant', '9m × 5m', 45.0, 'Géant'),
    ('10x4.14', '10m × 4.14m (41.4m²) — Géant', '10m × 4.14m', 41.4, 'Géant'),
    ('4.5x5.5', '4.5m × 5.5m (24.75m²) — Géant', '4.5m × 5.5m', 24.75, 'Géant'),
    ('1x2', '1,20m × 1,80m (2,16m²) — Sucette', '1.20m × 1.80m', 2.16, 'Sucette'),
    ('gm-4x3', '4m × 3m (12m²) — Marché', '4m × 3m', 12.0, 'Marché'),
    ('gm-4x4', '4m × 4m (16m²) — Marché', '4m × 4m', 16.0, 'Marché'),
    ('gm-5x4', '5m × 4m (20m²) — Marché', '5m × 4m', 20.0, 'Marché'),
    ('gm-12x3', '12m × 3m (36m²) — Marché', '12m × 3m', 36.0, 'Marché'),
    ('custom', 'Personnalisé (voir notes)', '', None, 'Personnalisé'),
]

def run():
    print("Début de l'importation des formats...")
    for code, libelle, dim, sup, cat in FORMATS_A_INSERER:
        obj, created = FormatSupport.objects.get_or_create(
            code=code,
            defaults={
                'libelle': libelle,
                'dimensions': dim,
                'superficie': sup,
                'categorie': cat
            }
        )
        if created:
            print(f"✅ Format créé : {libelle}")
        else:
            print(f"ℹ️ Format déjà existant : {libelle}")
    print("Importation terminée avec succès !")

if __name__ == '__main__':
    run()