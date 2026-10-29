from django.http import HttpResponse
from django.template.loader import render_to_string


def _error_response(context, status):
    content = render_to_string('errors/error.html', context)
    return HttpResponse(content, status=status)


def permission_denied(request, exception=None):
    return _error_response({
        'error_code': '403',
        'error_title': 'Accès refusé',
        'error_message': "Vous n’avez pas l’autorisation d’accéder à cette page.",
    }, status=403)


def page_not_found(request, exception=None):
    return _error_response({
        'error_code': '404',
        'error_title': 'Page introuvable',
        'error_message': "La page demandée n’existe pas ou a été déplacée.",
    }, status=404)


def server_error(request):
    return _error_response({
        'error_code': '500',
        'error_title': 'Une erreur est survenue',
        'error_message': "Le serveur a rencontré un problème. Veuillez réessayer dans quelques instants.",
    }, status=500)
