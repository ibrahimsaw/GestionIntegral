# 🗺️ Integral — Système de Gestion de Régie Publicitaire

Plateforme Web Full-Stack Django pour la gestion complète d'un parc publicitaire : panneaux statiques, écrans numériques, campagnes, planning et exports.

---

## ✨ Fonctionnalités Complètes

### 👥 RBAC — Contrôle d'accès par rôle
| Rôle | Accès |
|------|-------|
| **ADMIN** | Contrôle total, gestion utilisateurs, suppressions, finances |
| **STAFF** | Gestion quotidienne : clients, campagnes, supports, planning |
| **TECHNICIEN** | Mise à jour états + upload photos de preuve |
| **CLIENT** | Lecture seule : ses campagnes, historique, rapports |

### 🗺️ Carte Interactive
- OpenStreetMap + Leaflet.js avec fond CartoDB Dark épuré
- Marqueurs SVG colorés dynamiquement selon disponibilité
- **Panneau latéral** au clic : photos, faces A/B, spots actifs, taux d'occupation
- **Clic droit** → Ajouter un support aux coordonnées GPS cliquées
- Filtres temps réel (type, état) sans rechargement de page

### 📋 Inventaire — Logique Métier
**Panneaux Statiques :**
- Gestion par **Face A / Face B** indépendantes
- **Verrouillage calendaire** : collision détectée en temps réel avant réservation
- L'API `/campaigns/api/disponibilite/?face=X&date_debut=...` vérifie instantanément

**Écrans Numériques :**
- Spots de **5s, 10s, 15s, 20s, 30s**
- **Fréquence paramétrable** (ex: 20s toutes les 2 minutes)
- **Calcul anti-collision** : vérification des secondes libres dans la boucle
- Tranches horaires configurables (ex: 06h–22h)
- Taux d'occupation calculé en temps réel

### 📅 Planning
- **Calendrier mensuel** avec toutes les campagnes actives en couleur
- **Taux d'occupation par support** sur le mois sélectionné
- **Main Courante** : timeline heure par heure (00:00 → 22:00) de chaque passage
- Alertes automatiques (campagnes finissant dans 7 jours, supports en panne)

### 📊 Rapports & Exports
- **Export PDF** : rapport de campagne avec ReportLab (design sombre/professionnel)
- **Export Excel** : rapport campagne + onglet Main Courante avec openpyxl
- Accessible aux clients (lecture seule sur leurs propres campagnes)

### 🎨 Design System Centralisé
```python
# geoad/settings.py — Modifiez ces valeurs pour personnaliser toute l'interface
DESIGN_CONFIG = {
    'COLOR_DISPONIBLE':  '#22c55e',   # Vert marqueurs libres
    'COLOR_OCCUPE':      '#f97316',   # Orange marqueurs occupés
    'COLOR_MAINTENANCE': '#ef4444',   # Rouge pannes
    'COLOR_PRIMARY':     '#00d4ff',   # Cyan — identité visuelle
    'COLOR_SECONDARY':   '#7c3aed',   # Violet — accent
    ...
}
```
Les variables CSS sont synchronisées automatiquement via `context_processors.py`.

---

## 🚀 Installation & Démarrage

### Prérequis
```
Python >= 3.10
```

### 1. Installer les dépendances
```bash
cd geoad
pip install -r requirements.txt
```

### 2. Démarrer avec données de démo
```bash
python setup_demo.py
```

Ce script :
- Applique les migrations
- Crée 4 utilisateurs (admin/staff/technicien/client)
- Crée 8 supports géolocalisés à Ouagadougou (5 panneaux + 3 écrans)
- Crée 5 clients réalistes
- Crée 4 campagnes avec supports, faces et spots configurés
- Lance le serveur

### Ou démarrage manuel
```bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

---

## 🌐 URLs Principales

| URL | Description | Accès |
|-----|-------------|-------|
| `/` | Carte Interactive | Tous |
| `/` (dashboard) | Tableau de bord | Tous |
| `/inventory/` | Liste des supports | Staff+ |
| `/inventory/ajouter/` | Ajouter un support | Staff+ |
| `/campaigns/` | Dashboard + stats | Tous |
| `/campaigns/clients/` | Liste des clients | Staff+ |
| `/campaigns/campaigns/` | Liste des campagnes | Tous |
| `/planning/calendrier/` | Calendrier mensuel | Staff+ |
| `/planning/main-courante/` | Timeline de diffusion | Staff+ |
| `/reports/` | Exports PDF/Excel | Tous |
| `/accounts/users/` | Gestion utilisateurs | Admin |
| `/admin/` | Back-office Django | Admin |

### API JSON
| Endpoint | Description |
|----------|-------------|
| `GET /inventory/api/geojson/` | GeoJSON de tous les supports |
| `GET /inventory/api/support/{id}/popup/` | Données popup panneau latéral |
| `GET /inventory/api/support/{id}/faces/` | Faces d'un panneau |
| `GET /campaigns/api/disponibilite/?face=X&date_debut=...` | Vérif. disponibilité face |
| `GET /campaigns/api/disponibilite/?ecran=X&date_debut=...&duree=10` | Vérif. capacité écran |
| `GET /planning/api/taux/?date_debut=...&date_fin=...` | Taux d'occupation globaux |

---

## 📁 Architecture du Projet

```
geoad/
├── manage.py
├── setup_demo.py              ← Script de démarrage
├── requirements.txt
├── geoad/
│   ├── settings.py            ← DESIGN_CONFIG centralisé ici
│   ├── context_processors.py  ← Inject design vars dans templates
│   └── urls.py
├── accounts/                  ← RBAC : User, rôles, décorateurs
├── inventory/                 ← Support, FacePanneau, EcranNumerique
│   ├── models.py              ← Logique disponibilité + anti-collision
│   └── views.py               ← API GeoJSON + popup + CRUD
├── campaigns/                 ← Client, Campagne, LigneCampagne, SpotEcran
│   ├── models.py              ← Logique métier + calcul secondes boucle
│   └── views.py               ← CRUD + API disponibilité
├── planning/                  ← Calendrier + Main Courante
│   └── views.py               ← Génération timeline automatique
├── reports/                   ← Export PDF (ReportLab) + Excel (openpyxl)
├── templates/
│   ├── base.html              ← Layout dark + CSS variables design system
│   ├── accounts/
│   ├── inventory/
│   │   ├── carte.html         ← Carte Leaflet + panneau latéral
│   │   └── support_detail.html
│   ├── campaigns/
│   ├── planning/
│   └── reports/
└── media/                     ← Photos, visuels, preuves uploadés
```

---

## 🔧 Configuration Production

```python
# geoad/settings.py
DEBUG = False
SECRET_KEY = 'votre-clé-secrète-aléatoire-et-longue'
ALLOWED_HOSTS = ['votre-domaine.com']

# PostgreSQL recommandé
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'geoad_db',
        'USER': 'geoad_user',
        'PASSWORD': '...',
        'HOST': 'localhost',
    }
}
```

```bash
pip install psycopg2-binary gunicorn
python manage.py collectstatic
gunicorn geoad.wsgi:application --bind 0.0.0.0:8000 --workers 3
```

---

## 🎨 Personnaliser les Couleurs

Tout le design est piloté depuis `geoad/settings.py` :

```python
DESIGN_CONFIG = {
    'COLOR_DISPONIBLE':  '#22c55e',  # → Changer ici affecte : marqueurs carte + badges + barres
    'COLOR_OCCUPE':      '#f97316',  # → Supports sous contrat
    'COLOR_MAINTENANCE': '#ef4444',  # → Supports en panne
    'COLOR_PRIMARY':     '#00d4ff',  # → Sidebar, boutons, accents
    'COLOR_BG_DARK':     '#0a0d12',  # → Fond général
    'TAUX_ALERTE':       75,         # → % déclenchant alerte orange
    'TAUX_CRITIQUE':     90,         # → % déclenchant alerte rouge
}
```

---

*Integral — Développé avec Django 4.2 · Leaflet.js · Bootstrap 5 · ReportLab · openpyxl*
python manage.py runserver 0.0.0.0:8080