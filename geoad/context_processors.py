from django.conf import settings

from inventory.models import FormatSupport, Support, format_sort_key


def design_config(request):
    """Injecte le design system dans tous les templates."""
    return {
        'DESIGN': settings.DESIGN_CONFIG,
        'APP_NAME': 'Integral',
    }


def inventory_villes(request):
    """Construit le menu par ville pour les panneaux standards et géants."""
    formats = {
        format_support.code: format_support
        for format_support in FormatSupport.objects.all()
    }
    villes = {}
    ville_support_counts = {}
    global_categories = {}
    global_category_labels = set()

    supports = Support.objects.select_related('ecran_info').values(
        'ville',
        'type_support',
        'format',
        'ecran_info__cellule',
    )
    for support in supports:
        ville = (support['ville'] or '').strip()
        is_screen = support['type_support'] == Support.TYPE_ECRAN
        format_code = (
            support['ecran_info__cellule'] if is_screen else support['format']
        )
        format_support = formats.get(format_code)
        if is_screen:
            category = 'Ecran'
        elif format_support and (format_support.categorie or '').casefold() == 'sucette':
            category = 'Sucette'
        else:
            category = ''

        if category:
            global_category_labels.add(category)
            if format_code:
                global_categories.setdefault(category, set()).add(format_code)

        if not ville or is_screen or not format_support:
            continue

        source_category = (format_support.categorie or '').casefold()
        if source_category in ('standard', '12m2', '12m²'):
            category = '12m2'
        elif source_category in ('geant', 'géant'):
            category = 'Géant'
        else:
            continue

        villes.setdefault(ville, {}).setdefault(category, set()).add(format_code)
        ville_support_counts[ville] = ville_support_counts.get(ville, 0) + 1

    inventory_villes = sorted(
        villes,
        key=lambda ville: (-ville_support_counts[ville], ville.casefold()),
    )
    inventory_categories = []
    for ville in inventory_villes:
        categories = []
        for label in ('12m2', 'Géant'):
            if label not in villes[ville]:
                continue
            format_codes = sorted(
                villes[ville][label],
                key=lambda code: format_sort_key(formats.get(code) or code),
            )
            category_formats = [
                {
                    'code': code,
                    'label': (
                        f"{formats[code].dimensions} "
                        f"({formats[code].superficie:g} "
                        "m²)"
                        if formats.get(code) and formats[code].superficie
                        else formats[code].dimensions if formats.get(code)
                        else code
                    ),
                }
                for code in format_codes
            ]
            categories.append({
                'label': label,
                'formats': category_formats,
            })
        inventory_categories.append({
            'ville': ville,
            'support_count': ville_support_counts[ville],
            'categories': categories,
        })

    inventory_global_categories = []
    for label in ('Sucette', 'Ecran'):
        if label not in global_category_labels:
            continue
        codes = global_categories.get(label, set())
        category_formats = [
            {
                'code': code,
                'label': (
                    f"{formats[code].dimensions} "
                    f"({formats[code].superficie:g} "
                    f"{'Cellules' if label == 'Ecran' else 'm²'})"
                    if formats.get(code) and formats[code].superficie
                    else formats[code].dimensions if formats.get(code)
                    else code
                ),
            }
            for code in sorted(
                codes,
                key=lambda code: format_sort_key(formats.get(code) or code),
            )
        ]
        inventory_global_categories.append({
            'label': label,
            'formats': category_formats,
        })

    return {
        'inventory_villes': inventory_villes,
        'inventory_categories': inventory_categories,
        'inventory_global_categories': inventory_global_categories,
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