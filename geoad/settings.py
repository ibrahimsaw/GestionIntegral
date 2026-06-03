"""
Integral — Système de Gestion de Régie Publicitaire
Settings Django
"""
import os  # <--- CRITIQUE : Ne pas oublier d'importer os pour os.environ
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Mode de débogage (Mettre à False en production)
DEBUG = True  # <--- CRITIQUE : Toujours désactiver le mode debug en production

if not DEBUG:
    SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY')
    # En production, spécifiez les vrais domaines ou IP de votre serveur (ex: ['geoad.votre-regie.bf'])
    ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '').split(',')
    
    # Sécurité HTTPS / SSL
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
else:
    SECRET_KEY = 'Integral-secret-key-change-in-production-2024'
    ALLOWED_HOSTS = ['*']  # Autorise tout uniquement en développement local

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION DES APPLICATIONS
# ─────────────────────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Apps tierces
    'crispy_forms',
    'crispy_bootstrap5',
    # Apps GeoAd / Integral
    'accounts',
    'inventory',
    'campaigns',
    'planning',
    'reports',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'geoad.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'geoad.context_processors.design_config',
                'campaigns.context_processors.alerts_data'
            ],
        },
    },
]

WSGI_APPLICATION = 'geoad.wsgi.application'

# ─────────────────────────────────────────────────────────────────────────────
# BASE DE DONNÉES
# ─────────────────────────────────────────────────────────────────────────────
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# Configuration de l'authentification personnalisée
AUTH_USER_MODEL = 'accounts.User'
LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

# ─────────────────────────────────────────────────────────────────────────────
# INTERNATIONALISATION & HORAIRES
# ─────────────────────────────────────────────────────────────────────────────
LANGUAGE_CODE = 'fr-fr'
TIME_ZONE = 'Africa/Ouagadougou'  # Alignement parfait pour la gestion locale
USE_I18N = True
USE_TZ = True

# ─────────────────────────────────────────────────────────────────────────────
# FICHIERS STATIQUES ET MÉDIAS
# ─────────────────────────────────────────────────────────────────────────────
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ─────────────────────────────────────────────────────────────────────────────
# DESIGN SYSTEM — Configuration centralisée des couleurs
# Modifiez ces valeurs pour personnaliser toute l'interface
# ─────────────────────────────────────────────────────────────────────────────
DESIGN_CONFIG = {
    # Couleurs des marqueurs carte (utilisées par Leaflet JS)
    'COLOR_DISPONIBLE':  '#16a34a',   # Vert — support libre
    'COLOR_OCCUPE':      '#FDDD48',   # Jaune — support sous contrat
    'COLOR_MAINTENANCE': '#dc2626',   # Rouge — support en panne
    'COLOR_BIENTOT':     '#7c3aed',   # Violet — campagne à venir
    # Identité visuelle de l'application (Charte Promo Pub Integral)
    'COLOR_PRIMARY':     '#932E2B',   # Marron / Rouge bordeaux — couleur principale UI
    'COLOR_SECONDARY':   '#FDDD48',   # Jaune — accent secondaire
    'COLOR_NOIR':        '#231f20',   # Noir — texte principal
    # Fonds et surfaces (thème CLAIR)
    'COLOR_BG_DARK':     '#f1f5f9',   # Fond principal (gris très clair)
    'COLOR_BG_CARD':     '#ffffff',   # Fond des cartes (blanc)
    'COLOR_BORDER':      '#e2e8f0',   # Bordures (gris clair)
    # Taux d'occupation
    'TAUX_ALERTE':       75,          # % au-delà duquel afficher une alerte orange
    'TAUX_CRITIQUE':     90,          # % au-delà duquel afficher une alerte rouge
}

# Horaires de diffusion par défaut pour le planning des panneaux
DIFFUSION_HEURE_DEBUT = 6    # 06:00
DIFFUSION_HEURE_FIN   = 22   # 22:00

# Configuration de Django Crispy Forms
CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
CRISPY_TEMPLATE_PACK = "bootstrap5"