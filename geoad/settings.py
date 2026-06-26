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
    ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', 'integralcarte.regies.tech').split(',')
    CSRF_TRUSTED_ORIGINS = os.environ.get(
        'CSRF_TRUSTED_ORIGINS',
        'https://integralcarte.regies.tech'
    ).split(',')

    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = False  # Nginx Proxy Manager gère le SSL
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
else:
    SECRET_KEY = 'Integral-secret-key-change-in-production-2024'
    ALLOWED_HOSTS = ['*', 'localhost', '127.0.0.1', '187.124.54.132']
    CSRF_TRUSTED_ORIGINS = [
        'https://integralcarte.regies.tech',
        'http://integralcarte.regies.tech',
        'http://187.124.54.132:8086',
    ]

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
    'django.contrib.humanize',
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
    'whitenoise.middleware.WhiteNoiseMiddleware',
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
                'geoad.context_processors.inventory_villes',
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
    'COLOR_DISPONIBLE':  "#16a34a",   # gris — support libre
    'COLOR_OCCUPE':      '#dc2626',   # Vert — support sous contrat
    'COLOR_RESERVE':     "#7c3aed",   # Orange — support réservé (en négociation) #6a8068
    'COLOR_PANNE': '#6c757d',   # Rouge — support en panne
    
    'COLOR_DISPONIBLE_BG':  "#e2f1ff",   
    'COLOR_OCCUPE_BG':      "#d8ffe6",   
    'COLOR_RESERVE_BG':     "#f2eaff",   
    'COLOR_PANNE_BG': '#ffe6e6',  
    
    'COLOR_MAINTENANCE': '#dc2626',
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