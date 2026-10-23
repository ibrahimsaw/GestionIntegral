from .views import get_cached_alertes


def alerts_data(request):
    alertes = get_cached_alertes()
    alertes_visibles = []
    for alerte in alertes:
        if alerte.get('staff_only') and (
            not request.user.is_authenticated
            or not request.user.is_staff_regie_role
        ):
            continue
        if (
            request.user.is_authenticated
            and alerte.get('exclude_user_id') == request.user.pk
        ):
            continue
        alertes_visibles.append(alerte)
    return {'alertes': alertes_visibles}