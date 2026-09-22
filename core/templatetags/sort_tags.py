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
    request = context.get('request')
    page_obj = context.get('page_obj')
    paginator = context.get('paginator')
    is_paginated = context.get('is_paginated')

    if page_obj is None:
        for key in ('object_list', 'clients', 'campagnes', 'supports', 'logs', 'maintenances', 'formats', 'reservations'):
            val = context.get(key)
            if hasattr(val, 'has_other_pages'):
                page_obj = val
                paginator = paginator or getattr(val, 'paginator', None)
                break

    if is_paginated is None and page_obj is not None:
        is_paginated = page_obj.has_other_pages() if hasattr(page_obj, 'has_other_pages') else False

    if paginator is None and page_obj is not None:
        paginator = getattr(page_obj, 'paginator', None)

    base_params = request.GET.copy() if request else {}

    def build_url(page_number):
        params = base_params.copy()
        params['page'] = page_number
        return params.urlencode()

    prev_url = build_url(page_obj.previous_page_number()) if page_obj and hasattr(page_obj, 'has_previous') and page_obj.has_previous() else None
    next_url = build_url(page_obj.next_page_number()) if page_obj and hasattr(page_obj, 'has_next') and page_obj.has_next() else None

    return {
        'is_paginated': is_paginated,
        'page_obj': page_obj,
        'paginator': paginator,
        'prev_url': prev_url,
        'next_url': next_url,
    }