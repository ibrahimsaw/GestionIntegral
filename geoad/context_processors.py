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
    categories = [
        {
            'key': 'panneau',
            'label': 'Panneaux',
            'icon': 'bi-sign-merge-right',
            'type': Support.TYPE_PANNEAU,
            'format': '',
            'exclude_format': '1x2',
        },
        {
            'key': 'ecran',
            'label': 'Écrans',
            'icon': 'bi-display',
            'type': Support.TYPE_ECRAN,
            'format': '',
            'exclude_format': '',
        },
        {
            'key': 'sucette',
            'label': 'Sucettes',
            'icon': 'bi-shop-window',
            'type': Support.TYPE_PANNEAU,
            'format': '1x2',
            'exclude_format': '',
        },
    ]
    for category in categories:
        queryset = Support.objects.filter(type_support=category['type'])
        if category['format']:
            queryset = queryset.filter(format=category['format'])
        if category['exclude_format']:
            queryset = queryset.exclude(format=category['exclude_format'])
        category['villes'] = sorted({
            ville.strip()
            for ville in queryset.values_list('ville', flat=True)
            if ville and ville.strip()
        })
    categories = [category for category in categories if category['villes']]
    return {
        'inventory_villes': villes,
        'inventory_categories': categories,
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
        'CONTACT_TEL01': getattr(settings, 'CONTACT_TEL01', ''),
        'CONTACT_TEL02': getattr(settings, 'CONTACT_TEL02', ''),
        'CONTACT_TEL03': getattr(settings, 'CONTACT_TEL03', ''),
    }