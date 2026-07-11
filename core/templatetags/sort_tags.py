from django import template

register = template.Library()


@register.inclusion_tag('core/_sortable_th.html')
def sortable_th(sort_links, sort_key, sort_dir, column_key, label):
    """
    Affiche un lien d'en-tête de colonne triable.
    Usage dans le template :
        {% load sort_tags %}
        {% sortable_th sort_links sort_key sort_dir 'nom' 'Campagne' %}
    """
    return {
        'url': sort_links.get(column_key, '#'),
        'is_active': sort_key == column_key,
        'sort_dir': sort_dir,
        'label': label,
    }


@register.inclusion_tag('core/_pagination.html', takes_context=True)
def pagination_nav(context):
    """
    Affiche une pagination qui conserve TOUS les paramètres GET actuels
    (filtres, recherche, tri...), en ne changeant que 'page'.
    Usage : {% pagination_nav %}
    """
    request = context['request']
    page_obj = context.get('page_obj')
    paginator = context.get('paginator')
    is_paginated = context.get('is_paginated')

    base_params = request.GET.copy()

    def build_url(page_number):
        params = base_params.copy()
        params['page'] = page_number
        return params.urlencode()

    prev_url = build_url(page_obj.previous_page_number()) if page_obj and page_obj.has_previous() else None
    next_url = build_url(page_obj.next_page_number()) if page_obj and page_obj.has_next() else None

    return {
        'is_paginated': is_paginated,
        'page_obj': page_obj,
        'paginator': paginator,
        'prev_url': prev_url,
        'next_url': next_url,
    }