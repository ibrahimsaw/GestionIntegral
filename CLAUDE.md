# CLAUDE.md

Ce fichier fournit des indications à Claude Code (claude.ai/code) pour travailler avec ce dépôt.

## Vue d'ensemble

Integral — Système de gestion de régie publicitaire basé sur Django pour la gestion de panneaux statiques, écrans numériques, campagnes, planning et rapports.

## Commandes de Démarrage

```bash
# Installer les dépendances
pip install -r requirements.txt

# Lancer avec les données de démo (crée utilisateurs, supports, campagnes)
python setup_demo.py

# Démarrage manuel
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

### Identifiants de Démo
| Utilisateur | Mot de passe | Rôle |
|-------------|--------------|------|
| admin | admin123 | Contrôle total |
| staff | staff123 | Opérations quotidiennes |
| technicien | tech123 | Mise à jour états + photos |
| client1 | client123 | Lecture seule (Brakina SA) |

## Architecture

### Structure des Applications
- **accounts** — Modèle User personnalisé avec RBAC (admin/staff/technicien/client), AuditLog
- **inventory** — Support (panneau/écran), FacePanneau (faces A/B), EcranNumerique (métadonnées écran)
- **campaigns** — Client, Contrat, Campagne, LigneCampagne (affectation supports), SpotEcran
- **planning** — LogDiffusion (logs de diffusion / main courante)
- **reports** — Exports PDF (ReportLab) et Excel (openpyxl)

### Logique Métier Clé

**Panneaux Statiques :**
- Réservation indépendante Face A / Face B
- Détection de collision calendaire via `/campaigns/api/disponibilite/?face=X&date_debut=...`

**Écrans Numériques :**
- Spots : 5s, 10s, 15s, 20s, 30s avec fréquence configurable (ex: 20s toutes les 2 minutes)
- Anti-collision : vérifie les secondes libres dans la boucle de rotation
- Horaires configurables par écran (défaut 06:00–22:00)

### Système RBAC (`accounts/decorators.py`)
- `@admin_required` — Gestion utilisateurs, suppressions, finances
- `@staff_required` — Opérations complètes (clients, campagnes, supports, planning)
- `@technicien_required` — Mises à jour statut, upload photos
- Clients : querysets filtrés (lecture seule sur leurs campagnes)

## Configuration

### Design System (`geoad/settings.py`)
Couleurs centralisées dans `DESIGN_CONFIG` :
```python
DESIGN_CONFIG = {
    'COLOR_DISPONIBLE':  '#16a34a',  # Marqueurs disponibles
    'COLOR_OCCUPE':      '#ea580c',  # Marqueurs occupés
    'COLOR_MAINTENANCE': '#dc2626',  # Marqueurs maintenance
    'COLOR_PRIMARY':     '#932E2B',  # Couleur principale UI
    'COLOR_SECONDARY':   '#FDDD48',  # Couleur accent
}
```

### Paramètres Clés
- Auth : `AUTH_USER_MODEL = 'accounts.User'`
- Fuseau : `Africa/Ouagadougou`
- Média : `MEDIA_ROOT = BASE_DIR / 'media'`

## Structure des URLs

| URL | Module | Accès |
|-----|--------|-------|
| `/` | Carte interactive (Leaflet.js) | Tous |
| `/inventory/` | Liste/CRUD supports | Staff+ |
| `/campaigns/` | Dashboard + stats | Tous |
| `/planning/calendrier/` | Calendrier mensuel | Staff+ |
| `/planning/main-courante/` | Timeline horaire | Staff+ |
| `/reports/` | Exports PDF/Excel | Tous |
| `/admin/` | Admin Django | Admin |

### APIs JSON
- `GET /inventory/api/geojson/` — Tous les supports en GeoJSON
- `GET /inventory/api/support/{id}/popup/` — Données popup
- `GET /campaigns/api/disponibilite/` — Vérification disponibilité (face ou écran)
- `GET /planning/api/taux/` — Taux d'occupation

## Vue des Modèles

### inventory/models.py
- **Support** — Unité publicitaire de base (panneau ou écran) avec géolocalisation
- **FacePanneau** — Faces A/B pour panneaux statiques
- **EcranNumerique** — Configuration écran (résolution, horaires, logique rotation)
- **PhotoMaintenance** — Photos de preuve techniciens

### campaigns/models.py
- **Client** — Client avec Contrat optionnel
- **Contrat** — Allocation de spots sur période
- **Campagne** — Campagne avec dates, statut, médias
- **LigneCampagne** — Affectation de support dans campagne (sélection face pour panneaux, config spot pour écrans)

### planning/models.py
- **LogDiffusion** — Log de passage automatique (main courante)

## Notes de Développement

- Script démo (`setup_demo.py`) crée des données réalistes à Ouagadougou
- Templates utilisent Bootstrap 5 avec crispy-forms
- Carte utilise Leaflet.js avec fond CartoDB Dark
- Clic droit sur la carte → ajouter un support aux coordonnées GPS
