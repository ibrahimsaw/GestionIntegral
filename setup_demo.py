#!/usr/bin/env python
"""
Integral — Script de démarrage rapide
Crée la base de données, les utilisateurs de démo et des données réalistes.
Usage : python setup_demo.py
"""
import os
import sys
import django
import datetime
import random
from datetime import time

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'geoad.settings')
django.setup()

from django.core.management import call_command


def run():
    print("═" * 60)
    print("  Integral — Initialisation")
    print("═" * 60)

    # ── 1. Migrations ────────────────────────────────────────────
    print("\n⚙️  Application des migrations...")
    call_command('migrate', verbosity=0)
    print("✅ Migrations OK")

    # ── 2. Nettoyage de l'ancienne base ─────────────────────────
    print("\n🧹 Nettoyage de l'ancienne base de données...")
    from accounts.models import User
    from inventory.models import Support, FacePanneau, EcranNumerique, PhotoMaintenance
    from campaigns.models import Client, Contrat, Campagne, LigneCampagne
    from planning.models import LogDiffusion

    # Supprimer tous les logs de diffusion
    LogDiffusion.objects.all().delete()

    # Supprimer toutes les lignes de campagne (foreign key vers Support)
    LigneCampagne.objects.all().delete()

    # Supprimer toutes les campagnes
    Campagne.objects.all().delete()

    # Supprimer tous les contrats
    Contrat.objects.all().delete()

    # Supprimer tous les clients
    Client.objects.all().delete()

    # Supprimer toutes les photos de maintenance
    PhotoMaintenance.objects.all().delete()

    # Supprimer tous les écrans numériques (OneToOne vers Support)
    EcranNumerique.objects.all().delete()

    # Supprimer toutes les faces de panneau (foreign key vers Support)
    FacePanneau.objects.all().delete()

    # Supprimer tous les supports
    Support.objects.all().delete()

    # Supprimer tous les utilisateurs (sauf superuser technique si existe)
    User.objects.all().delete()

    print("  ✅ Base de données nettoyée")

    # ── 3. Utilisateurs ──────────────────────────────────────────
    print("\n👥 Création des utilisateurs...")

    users_to_create = [
        dict(username='admin',      role='admin',      first_name='Admin',     last_name='GeoAd',     email='admin@geoad.bf',      password='admin123'),
        dict(username='staff',      role='staff',      first_name='Salif',     last_name='Ouédraogo', email='staff@geoad.bf',       password='staff123'),
        dict(username='technicien', role='technicien', first_name='Adama',     last_name='Kaboré',    email='tech@geoad.bf',        password='tech123'),
        dict(username='client1',    role='client',     first_name='Directeur', last_name='Brakina',   email='contact@brakina.bf',   password='client123'),
    ]

    for ud in users_to_create:
        pwd = ud.pop('password')
        u = User.objects.create_user(password=pwd, **ud)
        if ud['username'] == 'admin':
            u.is_superuser = True
            u.is_staff = True
            u.save()
        print(f"  ✅ Utilisateur : {ud['username']} / {pwd}  [{ud['role']}]")

    today = datetime.date.today()
    created_admin = User.objects.get(username='admin')

    # ── 4. Supports — 248 total (190 panneaux Ouaga + 48 panneaux Bobo + 10 écrans LED réels) ──
    print("\n📦 Création de 248 supports (Ouagadougou + Bobo-Dioulasso)...")

    # Quartiers de Ouagadougou et environs
    quartiers = [
        'Zogona', 'Ouaga 2000', 'Centre', 'Patte d\'Oie', 'Dapoya',
        'Gounghin', 'ZACA', 'Koulouba', 'Cissin', 'Gampéla',
        'Tampouy', 'Polesgo', 'Saaba', 'Tanghin', 'Koubri',
        'Komki-Ipala', 'Pabré', 'Saaba', 'Noungou', 'Ziniaré'
    ]

    # Coordonnées de base (centre de Ouaga)
    BASE_LAT = 12.3714
    BASE_LNG = -1.5197

    # Lieux notables pour noms
    lieux_panneaux = [
        'Rond-Point des Nations Unies', 'Avenue Kwame Nkrumah', 'Marché Rood Woko',
        'Carrefour Patte d\'Oie', 'Avenue de la Nation', 'Boulevard du 11 Mai',
        'Rond-Point de la Poste', 'Avenue Joseph Ki-Zerbo', 'Rue 12.10',
        'Boulevard de la Révolution', 'Avenue Dimdolobsom', 'Rond-Point de l\'Unité Africaine',
        'Carrefour du 5ème', 'Avenue du Président Yalgado', 'Rond-Point de Kossodo',
        'Marché de Gounghin', 'Carrefour An II', 'Rond-Point de l\'Entente',
        'Avenue de l\'Indépendance', 'Rond-Point de Gampéla', 'Carrefour Cissin',
        'Marché de Tampouy', 'Rond-Point de Tanghin', 'Carrefour Polesgo',
        'Avenue de Koulouba', 'Rond-Point de ZACA', 'Carrefour Ouaga 2000',
        'Boulevard du Grand Sighinoghin', 'Rond-Point de Dapoya', 'Carrefour Gounghin',
    ]

    # Génération des 190 panneaux
    print("  📋 Création de 190 panneaux statiques...")
    panneaux = []
    for i in range(1, 191):
        code = f'PAN-{i:03d}'
        nom = f'Panneau {lieux_panneaux[(i-1) % len(lieux_panneaux)]} #{(i-1)//len(lieux_panneaux)+1}'
        # Variation aléatoire des coordonnées autour du centre
        lat = BASE_LAT + random.uniform(-0.05, 0.05)
        lng = BASE_LNG + random.uniform(-0.05, 0.05)
        quartier = random.choice(quartiers)
        etat = random.choices(['bon', 'maintenance', 'panne'], weights=[85, 10, 5])[0]

        p = Support.objects.create(
            code=code,
            nom=nom,
            type_support='panneau',
            latitude=round(lat, 6),
            longitude=round(lng, 6),
            adresse=f'Quartier {quartier}, Ouagadougou',
            ville='Ouagadougou',
            quartier=quartier,
            etat=etat,
            date_installation=today - datetime.timedelta(days=random.randint(30, 730)),
            created_by=created_admin,
        )
        # Créer les faces A et B
        FacePanneau.objects.create(support=p, label='A', format=random.choice(['4x3', '8x3', '12x4']), eclairage=random.choice(['led', 'backlit', 'non']))
        FacePanneau.objects.create(support=p, label='B', format=random.choice(['4x3', '8x3', '12x4']), eclairage=random.choice(['led', 'frontlit', 'non']))
        panneaux.append(p)

        if i % 50 == 0:
            print(f"    {i}/190 panneaux créés...")

    print("  ✅ 190 panneaux créés (380 faces)")

    # ── Génération des 10 écrans LED — coordonnées GPS réelles (KML INTEGRAL Ouaga) ──
    print("  🖥️  Création de 10 écrans LED (coordonnées GPS réelles Ouagadougou)...")
    lieux_ecrans = [
        {
            'code': 'ECR-001', 'nom': 'Rond point des nations unies',
            'adresse': 'Rond point des nations unies, Ouagadougou',
            'quartier': 'Zogona', 'lat': 12.3713079, 'lng': -1.5194274,
        },
        {
            'code': 'ECR-002', 'nom': 'Croisement Dignité / Route 17-36 Bonheur ville',
            'adresse': 'Croisement av de la dignité / route 17-36 Bonheur ville, Ouagadougou',
            'quartier': 'Bonheur ville', 'lat': 12.3233321, 'lng': -1.5523731,
        },
        {
            'code': 'ECR-003', 'nom': 'Croisement Kadiogo / DSTM Gounghin (face SGBF)',
            'adresse': 'Croisement av Kadiogo / goudron DSTM Gounghin en face de la SGBF, Ouagadougou',
            'quartier': 'Gounghin', 'lat': 12.3596283, 'lng': -1.5428283,
        },
        {
            'code': 'ECR-004', 'nom': 'Av Bassawarga Mogho Naaba',
            'adresse': 'Avenue Bassawarga Mogho Naaba, Ouagadougou',
            'quartier': 'Bassawarga', 'lat': 12.3564001, 'lng': -1.5273224,
        },
        {
            'code': 'ECR-005', 'nom': 'Croisement Bassawarga / Av Lamizana ASECNA',
            'adresse': 'Croisement Bassawarga / Avenue président Lamizana ASECNA, Ouagadougou',
            'quartier': 'Bassawarga', 'lat': 12.3468462, 'lng': -1.5266722,
        },
        {
            'code': 'ECR-006', 'nom': 'Rond point Tanghin - Melkys (côté Schiphra)',
            'adresse': 'Rond point de Tanghin Melkys à côté de Schiphra, Ouagadougou',
            'quartier': 'Tanghin', 'lat': 12.3904270, 'lng': -1.5167459,
        },
        {
            'code': 'ECR-007', 'nom': 'Larlé av Yatenga face station OLA OIL',
            'adresse': 'Larlé avenue du Yatenga face à la station OLA OIL, Ouagadougou',
            'quartier': 'Larlé', 'lat': 12.3754003, 'lng': -1.5418242,
        },
        {
            'code': 'ECR-008', 'nom': 'Blvd Thomas Sankara - Alimentation La Surface',
            'adresse': 'Boulevard Thomas Sankara alimentation La Surface, Ouagadougou',
            'quartier': 'Thomas Sankara', 'lat': 12.3762070, 'lng': -1.4938302,
        },
        {
            'code': 'ECR-009', 'nom': "Carrefour Patte d'Oie — Av Babanguida",
            'adresse': "Carrefour Patte d'Oie, Avenue Babanguida, Ouagadougou",
            'quartier': "Patte d'Oie", 'lat': 12.3780000, 'lng': -1.5380000,
        },
        {
            'code': 'ECR-010', 'nom': "ZACA — Avenue de l'Indépendance",
            'adresse': "ZACA, Avenue de l'Indépendance, Ouagadougou",
            'quartier': 'ZACA', 'lat': 12.3640000, 'lng': -1.5160000,
        },
    ]

    ecrans = []
    for ed in lieux_ecrans:
        e = Support.objects.create(
            code=ed['code'],
            nom=ed['nom'],
            type_support='ecran',
            latitude=ed['lat'],
            longitude=ed['lng'],
            adresse=ed['adresse'],
            ville='Ouagadougou',
            quartier=ed['quartier'],
            etat=random.choices(['bon', 'panne'], weights=[90, 10])[0],
            date_installation=today - datetime.timedelta(days=random.randint(60, 365)),
            created_by=created_admin,
        )
        EcranNumerique.objects.create(
            support=e,
            resolution=random.choice(['hd', 'fullhd', '4k']),
            taille_pouces=random.choice([55, 65, 75, 85]),
            heure_allumage=time(6, 0, 0),
            heure_extinction=time(22, 0, 0),
        )
        ecrans.append(e)
        print(f"    ✅ {ed['code']} — {ed['nom']}")

    print("  ✅ 10 écrans LED créés (coordonnées GPS réelles)")

    # ── Panneaux Bobo-Dioulasso — données réelles (KML Carte de la Régie INTEGRAL) ──
    print("\n  🏙️  Création des panneaux réels de Bobo-Dioulasso...")
    panneaux_bobo = [
        # ── PANNEAUX 12m² ──────────────────────────────────────────────────────────
        ("BOB-001", "Av unité, Place TIEFO AMORO - mur RAN Hôtel (face SITARAIL) A",        "Av de l'unité, Place TIEFO AMORO",              11.1778333, -4.3058889, "Centre"),
        ("BOB-002", "Av unité, Place TIEFO AMORO - entrée RAN Hôtel (face SITARAIL) B",     "Av de l'unité, Place TIEFO AMORO",              11.1774444, -4.3060833, "Centre"),
        ("BOB-003", "Av Liberté, Rond point Paysan - face station Petrofa A&B",             "Avenue de la Liberté, rond point place du Paysan", 11.1839722, -4.2961389, "Paysan"),
        ("BOB-004", "Av Liberté, Rond point Paysan - côté gouverneur Binger A&B",           "Avenue de la liberté, rond point place du Paysan", 11.1841457, -4.2958700, "Paysan"),
        ("BOB-005", "Rt Dédougou / Rue NAZI BONI - après station Amira Oil A&B",            "Route de Dédougou - Rue NAZI BONI",             11.2067500, -4.2888611, "Dédougou"),
        ("BOB-006", "Rt Dédougou / Rue NAZI BONI - mur marché des ânes (Access Oil) A&B",  "Route de Dédougou - Rue NAZI BONI",             11.2056021, -4.2891786, "Dédougou"),
        ("BOB-007", "Rt Faramana - face station Access Oil Forces vives A&B",               "Route de Faramana",                             11.1974218, -4.3010163, "Faramana"),
        ("BOB-008", "Rt Faramana - face clinique dentaire Yéguéré (avant passage à niveau)","Route de Faramana",                             11.2007500, -4.3025556, "Faramana"),
        ("BOB-009", "Blvd Révolution - après feu station SKI / avant banque Atlantique A&B","Boulevard de la révolution",                    11.1860278, -4.2985757, "Révolution"),
        ("BOB-010", "Blvd Révolution - après société transport Staff / mosquée A&B",        "Boulevard de la révolution",                    11.1867080, -4.3036577, "Révolution"),
        ("BOB-011", "Blvd Révolution / Rue 21.01 - feu mur lycée mixte Accart ville A&B",  "Intersection Boulevard de la Révolution-Rue 21.01", 11.1879444, -4.3148056, "Accart ville"),
        ("BOB-012", "Blvd Révolution - face station Access (avant feu) A&B",               "Boulevard de la révolution",                    11.1883611, -4.3180833, "Révolution"),
        ("BOB-013", "Blvd Révolution - face entrée stade Gal Lamizana A&B",                "Boulevard de la révolution",                    11.1902222, -4.3292778, "Stade"),
        ("BOB-014", "Rue Belle Ville - avant station PEFAN A&B",                           "Rue belle ville",                               11.1776944, -4.3331389, "Belle Ville"),
        ("BOB-015", "Rue Belle Ville - mur base aérienne / dépôt Prestige Oil A&B",        "Rue belle ville",                               11.1762778, -4.3295556, "Belle Ville"),
        ("BOB-016", "Rt Aéroport / Carrefour Blaise-Kadhafi - mur aéroport 1er panneau",   "Route de l'aéroport, carrefour Blaise-Kadhafi", 11.1656519, -4.3147996, "Aéroport"),
        ("BOB-017", "Rt Aéroport / Carrefour Blaise-Kadhafi - mur aéroport 2e panneau",    "Route de l'aéroport, carrefour Blaise-Kadhafi", 11.1660190, -4.3145571, "Aéroport"),
        ("BOB-018", "Av Indépendance - face usine BRAKINA Bobo A&B",                       "Avenue de l'indépendance",                      11.1668611, -4.3095278, "Indépendance"),
        ("BOB-019", "Av Indépendance - feu camp militaire / face cimetière militaire A&B",  "Avenue de l'indépendance",                      11.1687778, -4.3036111, "Indépendance"),
        ("BOB-020", "Rt Banfora Lafiabougou - face SONABEL A&B",                           "Route de Banfora, Lafiabougou",                 11.1507500, -4.3155556, "Banfora"),
        ("BOB-021", "Rt Banfora - avant feu intersection / côté EERI-BF A&B",              "Route de Banfora",                              11.1467778, -4.3164167, "Banfora"),
        ("BOB-022", "Rt Banfora sortie Bobo - après Access Oil / lycée Ki Zerbo I A&B",    "Route de Banfora, sortie Bobo",                 11.1306667, -4.3236389, "Banfora"),
        ("BOB-023", "Rt Orodara - mur aéroport / après pharmacie Harmonie A&B",            "Route de Orodara",                              11.1581111, -4.3209444, "Orodara"),
        ("BOB-024", "Rt Orodara - après usine Twelium de Samanga A&B",                     "Route de Orodara",                              11.1466111, -4.3739167, "Orodara"),
        ("BOB-025", "Av Gal de Gaulle - avant intersection Rue Vincens / complexe scolaire A&B","Avenue du Général de Gaulle",               11.1720833, -4.2751389, "De Gaulle"),
        ("BOB-026", "Blvd Chalone Champagne - feu face Rp place de la Femme / Shell A&B",  "Boulevard Chalone en champagne",                11.1713333, -4.2686111, "Champagne"),
        ("BOB-027", "Blvd Chalone Champagne - côté alimentation Watta / maison culture A", "Boulevard Chalone en champagne",                11.1686111, -4.2717778, "Champagne"),
        ("BOB-028", "Blvd Chalone Champagne - entre maison culture et mur mairie A&B",     "Boulevard Chalone en champagne",                11.1623056, -4.2789167, "Champagne"),
        ("BOB-029", "Blvd Chalone Champagne - face maison culture avant entrée mairie A&B","Boulevard Chalone en champagne",                11.1618889, -4.2789722, "Champagne"),
        ("BOB-030", "Av Louveau - avant Rp du cinquantenaire / bar VIP A&B",               "Avenue Louveau",                                11.1562778, -4.2853333, "Louveau"),
        ("BOB-031", "Av Louveau - 30m du Rp cinquantenaire / laboratoire ONEA A&B",        "Avenue Louveau",                                11.1569632, -4.2861648, "Louveau"),
        ("BOB-032", "Av Louveau - après station / mur gendarmerie A&B",                    "Avenue Louveau",                                11.1613333, -4.2904167, "Louveau"),
        ("BOB-033", "Rue Pepin Malherbe Sikasso Sira - après PN / maquis crépuscule A&B",  "Rue Pepin Malherbe, Sikasso Sira, Petit Paris",  11.1746667, -4.3106389, "Sikasso Sira"),
        ("BOB-034", "Rue Vincens - intersection Blvd De Gaulle / résidence 4 horizons A",  "Rue Vincens",                                   11.1721083, -4.2736333, "Vincens"),
        ("BOB-035", "Rue Vincens - après intersection De Gaulle / côté maquis 25h A&B",    "Rue Vincens",                                   11.1723611, -4.2737500, "Vincens"),
        ("BOB-036", "Rue Vincens - après intersection De Gaulle / lycée Ouézzin A&B",      "Rue Vincens",                                   11.1726070, -4.2743615, "Vincens"),
        ("BOB-037", "Rue Vincens - mur lycée Ouézzin Coulibaly A&B",                       "Rue Vincens",                                   11.1726085, -4.2746590, "Vincens"),
        ("BOB-038", "Rue Vincens - face station Petrodis / avant pharmacie Sya A&B",       "Rue Vincens",                                   11.1739722, -4.2777222, "Vincens"),
        ("BOB-039", "Rt Ouaga entrée Bobo - côté Wendkuni Bank / face Shell A&B",          "Route de Ouaga, entrée de Bobo",                11.1703398, -4.2532413, "Entrée Ouaga"),
        ("BOB-040", "Rt Ouaga entrée Bobo - côté douane / carrière A&B",                   "Route de Ouaga, entrée de Bobo",                11.1580278, -4.2004722, "Entrée Ouaga"),
        ("BOB-041", "Blvd Nelson Mandela - face commissariat de Dafra A&B",                "Boulevard Nelson Mandela",                      11.1827500, -4.2671111, "Dafra"),
        ("BOB-042", "Blvd Révolution Léguéma - face feu station Shell A",                  "Boulevard de la révolution route de Léguéma",   11.1833611, -4.2789167, "Révolution"),
        ("BOB-043", "Blvd Révolution - après station SKI / lycée Tounouma A&B",            "Boulevard de la révolution",                    11.1839444, -4.2837222, "Révolution"),
        # ── PANNEAUX GÉANTS ────────────────────────────────────────────────────────
        ("BOB-044", "Blvd Chalone Champagne - face Rp place Femme géant 40m² A&B",         "Boulevard Chalone en champagne",                11.1713611, -4.2690000, "Champagne"),
        ("BOB-045", "Blvd Chalone Champagne - après mairie/maison culture / gare Elitis A&B","Boulevard Chalone en champagne",              11.1601389, -4.2807222, "Champagne"),
        ("BOB-046", "Blvd Révolution - après station SKI / lycée Tounouma géant A&B",      "Boulevard de la révolution",                    11.1839722, -4.2832778, "Révolution"),
        ("BOB-047", "Rt Banfora / Carrefour Blaise-Kadhafi - station Shell / CARFO A&B",   "Route de Banfora, carrefour Blaise-Kadhafi",    11.1649167, -4.3141111, "Banfora"),
        ("BOB-048", "Rt Aéroport / Carrefour Blaise-Kadhafi - mur aéroport 24m²",         "Route de l'aéroport, carrefour Blaise-Kadhafi", 11.1658333, -4.3146389, "Aéroport"),
    ]

    for code, nom, adresse, lat, lng, quartier in panneaux_bobo:
        # Déterminer le format selon la catégorie (géants = 12x4)
        fmt = '12x4' if 'géant' in nom.lower() or '40m²' in nom or '24m²' in nom else random.choice(['4x3', '8x3', '12x4'])
        p = Support.objects.create(
            code=code,
            nom=nom,
            type_support='panneau',
            latitude=round(lat, 7),
            longitude=round(lng, 7),
            adresse=adresse + ', Bobo-Dioulasso',
            ville='Bobo-Dioulasso',
            quartier=quartier,
            etat=random.choices(['bon', 'maintenance', 'panne'], weights=[85, 10, 5])[0],
            date_installation=today - datetime.timedelta(days=random.randint(30, 730)),
            created_by=created_admin,
        )
        FacePanneau.objects.create(support=p, label='A', format=fmt, eclairage=random.choice(['led', 'backlit', 'non']))
        FacePanneau.objects.create(support=p, label='B', format=fmt, eclairage=random.choice(['led', 'frontlit', 'non']))
        panneaux.append(p)

    print(f"  ✅ 48 panneaux réels Bobo-Dioulasso créés (43 × 12m² + 5 géants)")
    print(f"  📊 Total supports : {Support.objects.count()} "
          f"({Support.objects.filter(type_support='panneau').count()} panneaux + "
          f"{Support.objects.filter(type_support='ecran').count()} écrans)")

    # ── 5. Clients — 10 clients ──────────────────────────────────
    print("\n👥 Création de 10 clients...")
    from campaigns.models import Client, Contrat

    clients_data = [
        dict(nom='Brakina SA', contact_nom='Seydou Traoré', telephone='+226 25 30 10 10', email='commercial@brakina.bf', type_contrat='annuel'),
        dict(nom='MTN Burkina Faso', contact_nom='Aminata Ouédraogo', telephone='+226 25 50 60 00', email='marketing@mtn.bf', type_contrat='annuel'),
        dict(nom='Coris Bank', contact_nom='Ibrahim Kaboré', telephone='+226 25 33 88 00', email='communication@corisbank.bf', type_contrat='annuel'),
        dict(nom='Air Burkina', contact_nom='Fatoumata Diallo', telephone='+226 25 49 23 60', email='commercial@airbf.bf', type_contrat='mensuel'),
        dict(nom='SONABHY', contact_nom='Moussa Sawadogo', telephone='+226 25 30 60 00', email='direction@sonabhy.bf', type_contrat='annuel'),
        dict(nom='Orange Burkina', contact_nom='Aïssata Koné', telephone='+226 25 50 70 00', email='marketing@orange.bf', type_contrat='annuel'),
        dict(nom='Canal+ Burkina', contact_nom='Jean-Baptiste Somé', telephone='+226 25 31 40 00', email='pro@canalplus.bf', type_contrat='mensuel'),
        dict(nom='Ecobank', contact_nom='Mariam Sawadogo', telephone='+226 25 30 80 00', email='com@ecobank.bf', type_contrat='annuel'),
        dict(nom='Société Générale', contact_nom='Paul Ilboudo', telephone='+226 25 32 50 00', email='marketing@sg.bf', type_contrat='ponctuel'),
        dict(nom='Léopard Transports', contact_nom='Ramatou Ouédraogo', telephone='+226 25 35 20 00', email='contact@leopard.bf', type_contrat='ponctuel'),
    ]

    contract_defaults = {
        'annuel': {'delta_debut': -60, 'delta_fin': 305, 'nb_spots': 150},
        'mensuel': {'delta_debut': -20, 'delta_fin': 10, 'nb_spots': 50},
        'ponctuel': {'delta_debut': -5, 'delta_fin': 10, 'nb_spots': 15},
    }

    clients = {}
    for cd in clients_data:
        contract_type = cd.pop('type_contrat')
        c = Client.objects.create(**cd)
        clients[c.nom] = c
        defaults = contract_defaults[contract_type]
        Contrat.objects.create(
            client=c,
            type_contrat=contract_type,
            date_debut=today + datetime.timedelta(days=defaults['delta_debut']),
            date_fin=today + datetime.timedelta(days=defaults['delta_fin']),
            nb_spots=defaults['nb_spots'],
        )
        print(f"  ✅ Client : {c.nom} ({contract_type})")

    # Lier client1 au profil Brakina
    u_client = User.objects.get(username='client1')
    u_client.client_profile = clients['Brakina SA']
    u_client.save()

    # ── 6. Campagnes — 60 campagnes ─────────────────────────────
    print("\n📢 Création de 60 campagnes...")
    from campaigns.models import Campagne, LigneCampagne

    # Noms de campagnes par type
    noms_campagnes_panneau = [
        'Campagne Promotionnelle', 'Affichage Institutionnel', 'Campagne Produit',
        'Lancement Nouvelle Gamme', 'Campagne Sensibilisation', 'Publicité Commerciale',
        'Campagne Événementielle', 'Affichage Marque', 'Campagne Saisonnière',
    ]

    noms_campagnes_ecran = [
        'Spot Dynamique', 'Animation Digitale', 'Campagne Vidéo',
        'Publicité Interactive', 'Spot Institutionnel', 'Campagne Flash',
        'Animation Commerciale', 'Spot Promotionnel', 'Campagne Multi-Supports',
    ]

    # Statuts et périodes
    statuts_config = {
        'terminee': {'debut_jours': (-120, -30), 'duree_jours': (15, 45)},
        'en_cours': {'debut_jours': (-30, 10), 'duree_jours': (20, 60)},
        'a_venir': {'debut_jours': (5, 30), 'duree_jours': (15, 45)},
    }

    # Répartition : 40 panneaux, 20 écrans
    # Parmi lesquelles : 20 terminées, 25 en cours, 15 à venir

    campagne_count = 0
    faces_panneaux = list(FacePanneau.objects.all()[:100])  # Utiliser 100 faces différentes

    # ── 6a. 20 campagnes terminées (15 panneaux + 5 écrans)
    print("  📋 Campagnes terminées...")
    for i in range(20):
        client_nom = random.choice(list(clients.keys()))
        client = clients[client_nom]

        if i < 15:  # Panneaux
            nom = f"{random.choice(noms_campagnes_panneau)} #{campagne_count+1}"
            type_support = 'panneau'
            supports_selectionnes = random.sample(panneaux, random.randint(1, 5))
            lignes_data = []
            for s in supports_selectionnes:
                faces = list(s.faces.all())
                if faces:
                    lignes_data.append({'support': s, 'face': random.choice(faces)})
        else:  # Écrans
            nom = f"{random.choice(noms_campagnes_ecran)} #{campagne_count+1}"
            type_support = 'ecran'
            supports_selectionnes = random.sample(ecrans, random.randint(1, 3))
            lignes_data = [{'support': s, 'face': None} for s in supports_selectionnes]

        config = statuts_config['terminee']
        date_debut = today + datetime.timedelta(days=random.randint(*config['debut_jours']))
        duree = random.randint(*config['duree_jours'])
        date_fin = date_debut + datetime.timedelta(days=duree)

        # Ajuster pour que ce soit vraiment dans le passé
        if date_fin >= today:
            delta = (date_fin - today).days + 5
            date_debut -= datetime.timedelta(days=delta)
            date_fin -= datetime.timedelta(days=delta)

        camp = Campagne.objects.create(
            client=client,
            nom=nom,
            date_debut=date_debut,
            date_fin=date_fin,
            statut='terminee',
            type_support=type_support,
            created_by=created_admin,
            frequence=random.choice([60, 120, 180]) if type_support == 'ecran' else None,
            duree_passage=random.choice([5, 10, 15, 20]) if type_support == 'ecran' else None,
        )

        for ld in lignes_data:
            ligne = LigneCampagne.objects.create(
                campagne=camp,
                support=ld['support'],
                face=ld.get('face'),
            )
            if type_support == 'ecran':
                ligne.save()

        campagne_count += 1

    # ── 6b. 25 campagnes en cours (15 panneaux + 10 écrans)
    print("  📋 Campagnes en cours...")
    for i in range(25):
        client_nom = random.choice(list(clients.keys()))
        client = clients[client_nom]

        if i < 15:  # Panneaux
            nom = f"{random.choice(noms_campagnes_panneau)} #{campagne_count+1}"
            type_support = 'panneau'
            supports_selectionnes = random.sample(panneaux, random.randint(1, 5))
            lignes_data = []
            for s in supports_selectionnes:
                faces = list(s.faces.all())
                if faces:
                    lignes_data.append({'support': s, 'face': random.choice(faces)})
        else:  # Écrans
            nom = f"{random.choice(noms_campagnes_ecran)} #{campagne_count+1}"
            type_support = 'ecran'
            supports_selectionnes = random.sample(ecrans, random.randint(1, 3))
            lignes_data = [{'support': s, 'face': None} for s in supports_selectionnes]

        config = statuts_config['en_cours']
        date_debut = today + datetime.timedelta(days=random.randint(*config['debut_jours']))
        duree = random.randint(*config['duree_jours'])
        date_fin = date_debut + datetime.timedelta(days=duree)

        # S'assurer que la campagne est en cours (pas dans le futur lointain)
        if date_debut > today:
            date_debut = today - datetime.timedelta(days=random.randint(0, 10))
            date_fin = date_debut + datetime.timedelta(days=duree)

        camp = Campagne.objects.create(
            client=client,
            nom=nom,
            date_debut=date_debut,
            date_fin=date_fin,
            statut='en_cours',
            type_support=type_support,
            created_by=created_admin,
            frequence=random.choice([60, 120, 180]) if type_support == 'ecran' else None,
            duree_passage=random.choice([5, 10, 15, 20]) if type_support == 'ecran' else None,
        )

        for ld in lignes_data:
            ligne = LigneCampagne.objects.create(
                campagne=camp,
                support=ld['support'],
                face=ld.get('face'),
            )
            if type_support == 'ecran':
                ligne.save()

        campagne_count += 1

    # ── 6c. 15 campagnes à venir (10 panneaux + 5 écrans)
    print("  📋 Campagnes à venir...")
    for i in range(15):
        client_nom = random.choice(list(clients.keys()))
        client = clients[client_nom]

        if i < 10:  # Panneaux
            nom = f"{random.choice(noms_campagnes_panneau)} #{campagne_count+1}"
            type_support = 'panneau'
            supports_selectionnes = random.sample(panneaux, random.randint(1, 5))
            lignes_data = []
            for s in supports_selectionnes:
                faces = list(s.faces.all())
                if faces:
                    lignes_data.append({'support': s, 'face': random.choice(faces)})
        else:  # Écrans
            nom = f"{random.choice(noms_campagnes_ecran)} #{campagne_count+1}"
            type_support = 'ecran'
            supports_selectionnes = random.sample(ecrans, random.randint(1, 3))
            lignes_data = [{'support': s, 'face': None} for s in supports_selectionnes]

        config = statuts_config['a_venir']
        date_debut = today + datetime.timedelta(days=random.randint(*config['debut_jours']))
        duree = random.randint(*config['duree_jours'])
        date_fin = date_debut + datetime.timedelta(days=duree)

        camp = Campagne.objects.create(
            client=client,
            nom=nom,
            date_debut=date_debut,
            date_fin=date_fin,
            statut='a_venir',
            type_support=type_support,
            created_by=created_admin,
            frequence=random.choice([60, 120, 180, 240, 600, 1200]) if type_support == 'ecran' else None,
            duree_passage=random.choice([5, 10, 15, 20]) if type_support == 'ecran' else None,
        )

        for ld in lignes_data:
            ligne = LigneCampagne.objects.create(
                campagne=camp,
                support=ld['support'],
                face=ld.get('face'),
            )
            if type_support == 'ecran':
                ligne.save()

        campagne_count += 1

    print(f"  ✅ 60 campagnes créées ({campagne_count} total)")

    # ── Résumé ───────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  📊 RÉCAPITULATIF DES DONNÉES CRÉÉES")
    print("═" * 60)
    print(f"""
  👥 Utilisateurs     : 4
  📍 Supports         : {Support.objects.count()}
     ├─ Panneaux Ouaga  : {Support.objects.filter(type_support='panneau', ville='Ouagadougou').count()} ({FacePanneau.objects.filter(support__ville='Ouagadougou').count()} faces)
     ├─ Panneaux Bobo   : {Support.objects.filter(type_support='panneau', ville='Bobo-Dioulasso').count()} ({FacePanneau.objects.filter(support__ville='Bobo-Dioulasso').count()} faces — données GPS réelles)
     └─ Écrans LED      : {Support.objects.filter(type_support='ecran').count()} (coordonnées GPS réelles Ouagadougou)
  👔 Clients          : {Client.objects.count()}
  📑 Contrats         : {Contrat.objects.count()}
  📢 Campagnes        : {Campagne.objects.count()}
     ├─ Terminées    : {Campagne.objects.filter(statut='terminee').count()}
     ├─ En cours     : {Campagne.objects.filter(statut='en_cours').count()}
     └─ À venir      : {Campagne.objects.filter(statut='a_venir').count()}
  📋 Lignes campagne  : {LigneCampagne.objects.count()}
""")

    print("═" * 60)
    print("  🚀 DÉMARRAGE DU SERVEUR")
    print("═" * 60)

    # Récupérer l'adresse IP du PC
    import socket
    pc_ip = socket.gethostbyname(socket.gethostname())

    print(f"""
  ✅ Serveur lancé sur 0.0.0.0:8000

  📱 Accès par localhost :
     URL principale : http://127.0.0.1:8000/
     Dashboard      : http://127.0.0.1:8000/
     Admin Django   : http://127.0.0.1:8000/admin/

  🌐 Accès par l'adresse IP du PC ({pc_ip}) :
     URL principale : http://{pc_ip}:8000/
     Dashboard      : http://{pc_ip}:8000/
     Admin Django   : http://{pc_ip}:8000/admin/

  ┌─────────────────┬────────────┬──────────────────┐
  │ Utilisateur     │ Mot de passe│ Rôle            │
  ├─────────────────┼────────────┼──────────────────┤
  │ admin           │ admin123   │ Administrateur   │
  │ staff           │ staff123   │ Staff Régie      │
  │ technicien      │ tech123    │ Technicien       │
  │ client1         │ client123  │ Client (Brakina) │
  └─────────────────┴────────────┴──────────────────┘

  ⏹️  Appuyez sur Ctrl+C pour arrêter le serveur
""")
    call_command('runserver', '0.0.0.0:8000')


if __name__ == '__main__':
    run()