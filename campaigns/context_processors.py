from .views import get_cached_alertes
from .models import Campagne
from inventory.models import FormatSupport, normaliser_categorie_format


def campaign_category_nav(request):
    user = request.user
    if not user.is_authenticated:
        return {'campaign_category_nav': []}

    campagnes = Campagne.objects.all()
    if getattr(user, 'is_client', False):
        client_profile = getattr(user, 'client_profile', None)
        if client_profile is None:
            return {'campaign_category_nav': []}
        campagnes = campagnes.filter(client=client_profile)
    if not getattr(user, 'is_admin', False):
        campagnes = campagnes.filter(actif=True)

    campagne_types = list(campagnes.values_list('type_support', 'est_mere').distinct())
    format_categories = {
        code: normaliser_categorie_format(categorie)
        for code, categorie in FormatSupport.objects.filter(
            code__in={
                type_support
                for type_support, est_mere in campagne_types
                if type_support and not est_mere
            }
        ).values_list('code', 'categorie')
        if normaliser_categorie_format(categorie)
    }
    categories_existantes = {
        normaliser_categorie_format(categorie).casefold():
        normaliser_categorie_format(categorie)
        for categorie in FormatSupport.hors_ecran().values_list(
            'categorie', flat=True
        )
        if normaliser_categorie_format(categorie)
    }
    categories = set()
    for type_support, est_mere in campagne_types:
        if est_mere:
            categories.add('Campagnes mères')
        elif normaliser_categorie_format(type_support).casefold() in (
            'ecran', 'écran',
        ):
            categories.add('Écran')
        elif normaliser_categorie_format(type_support).casefold() in (
            'marche', 'marché',
        ):
            categories.add('Marché')
        elif normaliser_categorie_format(type_support).casefold() == 'panneau':
            categories.add('Panneau')
        else:
            categorie = format_categories.get(type_support)
            if not categorie and type_support:
                categorie = categories_existantes.get(
                    normaliser_categorie_format(type_support).casefold()
                )
            if categorie:
                categories.add(categorie)

    return {
        'campaign_category_nav': [
            {'label': categorie, 'value': categorie}
            for categorie in sorted(categories, key=str.casefold)
        ]
    }


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