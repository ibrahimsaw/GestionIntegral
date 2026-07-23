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


def contact_info(request):
    """Injecte les coordonnées de contact dans tous les templates."""
    return {
        'CONTACT_EMAIL': getattr(settings, 'CONTACT_EMAIL', ''),
        'CONTACT_EMAIL1': getattr(settings, 'CONTACT_EMAIL1', ''),
        'CONTACT_EMAIL2': getattr(settings, 'CONTACT_EMAIL2', ''),
        'CONTACT_TEL1': getattr(settings, 'CONTACT_TEL1', ''),
        'CONTACT_TEL2': getattr(settings, 'CONTACT_TEL2', ''),
        'CONTACT_TEL3': getattr(settings, 'CONTACT_TEL3', ''),
    }