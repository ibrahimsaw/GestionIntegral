from django.conf import settings


def design_config(request):
    """Injecte le design system dans tous les templates."""
    return {
        'DESIGN': settings.DESIGN_CONFIG,
        'APP_NAME': 'Integral',
    }
