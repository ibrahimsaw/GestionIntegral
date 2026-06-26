from django.conf import settings

from inventory.models import Support


def design_config(request):
    """Injecte le design system dans tous les templates."""
    return {
        'DESIGN': settings.DESIGN_CONFIG,
        'APP_NAME': 'Integral',
    }


def inventory_villes(request):
    """Injecte la liste des villes présentes dans l'inventaire pour la navigation dynamique."""
    villes = sorted({v.strip() for v in Support.objects.values_list('ville', flat=True) if v and v.strip()})
    return {
        'inventory_villes': villes,
    }
