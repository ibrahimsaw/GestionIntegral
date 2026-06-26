#!/usr/bin/env python
"""
Integral — Import KML complet
Lit le fichier KML de la Régie INTEGRAL et crée tous les supports + faces en BDD.

Usage : python import_kml.py
        python import_kml.py --dry-run     (simulation sans écriture)
        python import_kml.py --clear       (supprime les supports existants avant import)
"""

import os
import sys
import re
import xml.etree.ElementTree as ET

# ── Django setup ──────────────────────────────────────────────────────────────
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'geoad.settings')

import django
django.setup()

from django.db import transaction
from django.contrib.auth import get_user_model

from inventory.models import Support, FacePanneau, EcranNumerique

User = get_user_model()

# ── Constantes ────────────────────────────────────────────────────────────────
KML_PATH = os.path.join(os.path.dirname(__file__), 'Carte_de_la_Régie_INTEGRAL__1_.kml')
KML_NS   = {'kml': 'http://www.opengis.net/kml/2.2'}

# Mapping dossier KML → (ville, type_support, format_par_defaut)
FOLDER_CONFIG = {
    'OUAGADOUGOU PANNEAUX 12m²':       ('Ouagadougou',    'panneau', '4x3'),
    'OUAGADOUGOU PANNEAUX GEANTS':     ('Ouagadougou',    'panneau', '8x4'),
    'OUAGADOUGOU SUCETTES':            ('Ouagadougou',    'panneau', '1x2'),
    'OUAGADOUGOU SUCETTES NON ECLAIREES': ('Ouagadougou', 'panneau', '1x2'),
    'OUAGADOUGOU 21m² à 24m²':        ('Ouagadougou',    'panneau', '6x4'),
    # 'OUAGADOUGOU ECRANS LED':          ('Ouagadougou',    'ecran',   ''),
    'BOBO DIOULASSO PANNEAUX 12m²':   ('Bobo-Dioulasso', 'panneau', '4x3'),
    'BOBO DIOULASSO PANNEAUX GEANTS': ('Bobo-Dioulasso', 'panneau', '10x4'),
}

# Détection du format depuis le nom du placemark
FORMAT_PATTERNS = [
    (r'40m²|10mx4m|10m×4m',                  '10x4'),
    (r'géant.*48|12mx4|12m×4',               '12x4'),
    (r'géant.*32|8mx4|8m×4',                 '8x4'),
    (r'géant.*24|6mx4|6m×4',                 '6x4'),
    (r'24m²',                                 '6x4'),
    (r'21m²|7mx3|7m×3',                      '7x3'),
    (r'sucette|1,20|1\.2.*1\.8|mobilier',    '1x2'),
    (r'grand.marché.*5mx4|5m×4',             'gm-5x4'),
    (r'grand.marché.*4mx4|4m×4',             'gm-4x4'),
    (r'grand.marché.*4mx3|4m×3',             'gm-4x3'),
    (r'12m²|4mx3|4m×3',                      '4x3'),
]

ETAT_BON         = 'bon'

def detect_format(folder_name: str, placemark_name: str, default_fmt: str) -> str:
    """Déduit le format depuis le nom du dossier et du placemark."""
    text = (folder_name + ' ' + placemark_name).lower()
    for pattern, fmt in FORMAT_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return fmt
    return default_fmt


def detect_quartier(nom: str, ville: str) -> str:
    """Extrait le quartier depuis le nom du placemark."""
    nom_lower = nom.lower()
    # 1. Extraction dynamique par Expressions Régulières (Avenues, Routes, Rues, Boulevards)
    # Cette regex cherche un mot clé comme avenue/route/rue suivi de "de la", "de", "du" etc.


    if ville == 'Ouagadougou':
        QUARTIERS_OUAGA = [
            ('bassawarga',      'Bassawarga'),
            ('france-afrique',  'Boulevard France-Afrique '),
            ('thomas sankara',  'Thomas Sankara'),
            ('jeunesse',        'Boulevard de la Jeunesse'),
            ('père wrinsky',    'Père Wrinsky'),
            ('lamizana',        'Lamizana'),
            ('tanghin',         'Tanghin'),
            ('boincé',          'Boincé Yarr'),
            ('somgandé',        'Somgandé'),
            ('wayalghin',       'Wayalghin'),
            ('kossoghin',       'Kossoghin'),
            ('tampouy',         'Tampouy'),
            ('nonsin',          'Nonsin'),
            ('zagtouli',        'Zagtouli'),
            ('pissy',           'Pissy'),
            ('kouritenga',      'Kouritenga'),
            ('nagrin',          'Nagrin'),
            ('dapoya',          'Dapoya'),
            ('kargondin',       'Kalgondin'),
            ('kalgonin',        'Kalgondin'),
            ('wemtenga',        'Wemtenga'),
            ('tabtenga',        'Tabtenga'),
            ('bendogo',         'Bendogo'),
            ('dagnoen',         'Dagnoen'),
            ('ouidi',           'Ouidi'),
            ('gounghin',        'Gounghin'),
            ('dignité',         'Dignité'),
            ('kadiogo',         'Kadiogo'),
            ('yatenga',         'Yatenga'),
            ('conseil de l\'entente', 'Conseil de l\'Entente'),
            ('naaba sombré',    'Naaba Sombré'),
            ('patte d\'oie',    'Patte d\'Oie'),
            ('ouaga 2000',      'Ouaga 2000'),
            ('route de pô',     'Route de Pô'),
            ('route de kaya',   'Route de Kaya'),
            ('route de sapone', 'Route de Saponé'),
            ('karpala',         'Karpala'),
            ('zagre',           'Zagré'),
            ('nation',          'Centre ville'),
            ('stade municipal', 'Stade Municipal'),
            ('mogho naaba',     'Mogho Naaba'),
            ('brafaso',         'Brafaso'),
            ('bonaam',          'Bonaam'),
        ]
        for keyword, quartier in QUARTIERS_OUAGA:
            if keyword in nom_lower:
                return quartier

    elif ville == 'Bobo-Dioulasso':
        QUARTIERS_BOBO = [
            ('tiefo amoro',         'Centre'),
            ('paysan',              'Paysan'),
            ('dédougou',            'Dédougou'),
            ('faramana',            'Faramana'),
            ('révolution',          'Révolution'),
            ('accart ville',        'Accart Ville'),
            ('stade',               'Stade'),
            ('belle ville',         'Belle Ville'),
            ('aéroport',            'Aéroport'),
            ('indépendance',        'Indépendance'),
            ('banfora',             'Banfora'),
            ('lafiabougou',         'Lafiabougou'),
            ('orodara',             'Orodara'),
            ('de gaulle',           'De Gaulle'),
            ('champagne',           'Champagne'),
            ('louveau',             'Louveau'),
            ('sikasso',             'Sikasso Sira'),
            ('vincens',             'Vincens'),
            ('ouaga',               'Entrée Ouaga'),
            ('mandela',             'Dafra'),
            ('léguéma',             'Léguéma'),
            ('tounouma',            'Tounouma'),
            ('carfo',               'Banfora'),
        ]
        for keyword, quartier in QUARTIERS_BOBO:
            if keyword in nom_lower:
                return quartier

    return ''


def detect_eclairage(nom: str) -> str:
    """Détecte le type d'éclairage depuis le nom."""
    nom_lower = nom.lower()
    if 'led' in nom_lower:
        return 'led'
    if 'backlit' in nom_lower or 'rétro' in nom_lower:
        return 'backlit'
    if 'éclairé' in nom_lower or 'frontlit' in nom_lower:
        return 'frontlit'
    return 'non'


def detect_faces(nom: str) -> list:
    """Détermine les faces à créer."""
    nom_upper = nom.upper()
    has_ab = bool(re.search(r'FACE\s+A\s*[&ET]\s*B|FACE\s+A&B|A\s*&\s*B', nom_upper))
    has_a  = bool(re.search(r'FACE\s+A\b', nom_upper))
    has_b  = bool(re.search(r'FACE\s+B\b', nom_upper))

    if has_ab:
        return ['A', 'B']
    if has_a and has_b:
        return ['A', 'B']
    if has_a:
        return ['A']
    if has_b:
        return ['B']
    return ['A', 'B']


def parse_kml(kml_path: str) -> list:
    """Parse le KML et retourne une liste de dicts prêts à insérer."""
    if not os.path.exists(kml_path):
        print(f"❌ Erreur : Le fichier KML est introuvable à l'emplacement : {kml_path}")
        sys.exit(1)

    tree = ET.parse(kml_path)
    root = tree.getroot()

    records = []
    counters = {'OU': 0, 'BO': 0, 'ECR-OUA': 0, 'ECR-BOB': 0}

    for folder in root.findall('.//kml:Folder', KML_NS):
        folder_name_el = folder.find('kml:name', KML_NS)
        if folder_name_el is None:
            continue
        folder_name = folder_name_el.text.strip()
        # --- AJOUTEZ CES DEUX LIGNES POUR EXCLURE LE DOSSIER ---
        if folder_name == 'OUAGADOUGOU ECRANS LED':
            continue # Passe directement au dossier suivant sans lire les placemarks

        config = FOLDER_CONFIG.get(folder_name)
        if config is None:
            continue

        ville, type_support, default_fmt = config

        for pm in folder.findall('kml:Placemark', KML_NS):
            name_el = pm.find('kml:name', KML_NS)
            nom = name_el.text.strip() if name_el is not None else ''
            nom = re.sub(r'\s+', ' ', nom)

            coords_el = pm.find('.//kml:coordinates', KML_NS)
            if coords_el is None:
                continue
            parts = coords_el.text.strip().split(',')
            if len(parts) < 2:
                continue
            lng = round(float(parts[0].strip()), 7)
            lat = round(float(parts[1].strip()), 7)

            fmt = detect_format(folder_name, nom, default_fmt)

            if type_support == 'ecran':
                key = 'ECR-OUA' if ville == 'Ouagadougou' else 'ECR-BOB'
            else:
                key = 'OU' if ville == 'Ouagadougou' else 'BO'
            counters[key] += 1
            code = f"{key}-{counters[key]:03d}"

            quartier = detect_quartier(nom, ville)
            faces = detect_faces(nom) if type_support == 'panneau' else []
            eclairage = detect_eclairage(nom)

            records.append({
                'code':         code,
                'nom':          nom,
                'type_support': type_support,
                'ville':        ville,
                'quartier':     quartier,
                'latitude':     lat,
                'longitude':    lng,
                'format':       fmt,
                'faces':        faces,
                'eclairage':    eclairage,
                'folder':       folder_name,
            })

    return records


def import_records(records, dry_run=False, clear_existing=False):
    print("\n" + "="*70)
    print(f"{'🔍 DRY-RUN — simulation sans écriture' if dry_run else '🚀 IMPORT EN BASE DE DONNÉES'}")
    print("="*70)

    created_supports = 0
    created_faces    = 0
    skipped          = 0
    errors           = []

    for data in records:
        try:
            if dry_run:
                print(f"  [DRY] {data['code']} — {data['nom'][:50]} ({data['ville']}) {data['faces']}")
                continue

            with transaction.atomic():
                # ── Support ────────────────────────────────────────────
                support, created = Support.objects.get_or_create(
                    code=data['code'],
                    defaults={
                        'nom':          data['nom'],
                        'type_support': data['type_support'],
                        'ville':        data['ville'],
                        'quartier':     data['quartier'],
                        'latitude':     data['latitude'],
                        'longitude':    data['longitude'],
                        'format':       data['format'],
                        'etat':         ETAT_BON,
                    }
                )

                if not created:
                    skipped += 1
                    print(f"  ⚠️  Ignoré (existe déjà) : {data['code']}")
                    continue

                created_supports += 1

                # ── Faces (panneaux uniquement) ────────────────────────
                for label in data['faces']:
                    FacePanneau.objects.get_or_create(
                        support=support,
                        label=label,
                        defaults={'eclairage': data['eclairage'], 'etat': ETAT_BON}
                    )
                    created_faces += 1

                # ── Écran numérique ────────────────────────────────────
                if data['type_support'] == 'ecran':
                    EcranNumerique.objects.get_or_create(support=support)

                print(f"  ✅ {data['code']} — {data['nom'][:50]} | faces: {data['faces']}")

        except Exception as e:
            errors.append((data['code'], str(e)))
            print(f"  ❌ Erreur {data['code']} : {e}")

    print("\n" + "="*70)
    print(f"📊 BILAN :")
    print(f"   ✅ Supports créés  : {created_supports}")
    print(f"   👥 Faces créées    : {created_faces}")
    print(f"   ⚠️  Ignorés        : {skipped}")
    print(f"   ❌ Erreurs         : {len(errors)}")
    if errors:
        for code, err in errors:
            print(f"      → {code} : {err}")
    print("="*70)


# ── Point d'entrée principal ──────────────────────────────────────────────────

if __name__ == '__main__':
    args = sys.argv[1:]
    dry_run        = '--dry-run' in args
    clear_existing = '--clear'   in args

    print("🚀 Lecture et traitement du fichier KML...")
    extracted_data = parse_kml(KML_PATH)
    print(f"📋 {len(extracted_data)} enregistrements localisés.")

    import_records(extracted_data, dry_run=dry_run, clear_existing=clear_existing)