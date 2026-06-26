# portail/templatetags/portail_tags.py
from django import template
from django.utils.safestring import mark_safe

register = template.Library()

# ── Dimensions réelles par code format ───────────────────────────────────────
# (largeur_m, hauteur_m)
FORMAT_DIMS = {
    '4x3':    (4,  3),
    '4x5':    (4,  5),
    '7x3':    (7,  3),
    '6x4':    (6,  4),
    '8x4':    (8,  4),
    '10x4':   (10, 4),
    '12x4':   (12, 4),
    '12x5':   (12, 5),
    '24x4':   (24, 4),
    '9x5':    (9,  5),
    '1x2':    (1.2, 1.8),
    'gm-4x3': (4,  3),
    'gm-4x4': (4,  4),
    'gm-5x4': (5,  4),
    'gm-12x3':(12, 3),
    'custom': (4,  3),   # fallback
}

MAX_W = 180   # px largeur max du SVG
MAX_H = 140   # px hauteur max du SVG
PADDING = 28  # espace pour les annotations


@register.simple_tag
def svg_panneau(format_code, width=None, show_dims=True):
    """
    Génère un SVG inline représentant le format d'un panneau de façon
    proportionnelle aux vraies dimensions.

    Usage :
        {% svg_panneau "4x3" %}
        {% svg_panneau "10x4" width=240 %}
        {% svg_panneau support.format show_dims=False %}
    """
    dims = FORMAT_DIMS.get(format_code, FORMAT_DIMS['custom'])
    larg_m, haut_m = dims

    # ── Calcul du viewBox proportionnel ───────────────────────────────────
    max_w = (width or MAX_W) - PADDING
    max_h = MAX_H - PADDING

    ratio = larg_m / haut_m
    if ratio >= max_w / max_h:
        rect_w = max_w
        rect_h = round(max_w / ratio, 1)
    else:
        rect_h = max_h
        rect_w = round(max_h * ratio, 1)

    total_w = rect_w + PADDING
    total_h = rect_h + PADDING

    # Position du rectangle (centré avec marge pour annotations)
    rx = PADDING / 2
    ry = PADDING / 2

    # ── Couleurs ──────────────────────────────────────────────────────────
    bg        = '#1a3c6b'    # bleu marine
    border    = '#e8b84b'    # or
    txt_color = '#ffffff'
    ann_color = '#374151'    # gris foncé pour annotations

    # ── Annotations dimensions ────────────────────────────────────────────
    dim_larg = f"{larg_m:g}m"
    dim_haut = f"{haut_m:g}m"

    if format_code.startswith('gm-'):
        label_type = "Marché"
    elif format_code == '1x2':
        label_type = "Sucette"
    elif larg_m >= 9 or (larg_m * haut_m) >= 20:
        label_type = "Géant"
    else:
        label_type = "Standard"

    surface = round(larg_m * haut_m, 1)

    annotations = ''
    if show_dims:
        # Flèche largeur (en bas)
        arr_y = ry + rect_h + 14
        annotations += f'''
  <!-- Annotation largeur -->
  <line x1="{rx}" y1="{arr_y}" x2="{rx + rect_w}" y2="{arr_y}"
        stroke="{ann_color}" stroke-width="1.2" marker-start="url(#arr)" marker-end="url(#arr)"/>
  <text x="{rx + rect_w/2}" y="{arr_y + 11}" text-anchor="middle"
        font-size="9" fill="{ann_color}" font-family="sans-serif">{dim_larg}</text>

  <!-- Annotation hauteur (à droite) -->
  <line x1="{rx + rect_w + 14}" y1="{ry}" x2="{rx + rect_w + 14}" y2="{ry + rect_h}"
        stroke="{ann_color}" stroke-width="1.2" marker-start="url(#arr)" marker-end="url(#arr)"/>
  <text x="{rx + rect_w + 20}" y="{ry + rect_h/2 + 4}" text-anchor="start"
        font-size="9" fill="{ann_color}" font-family="sans-serif">{dim_haut}</text>
'''
        total_w += 10  # extra pour annotation droite

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg"
     viewBox="0 0 {total_w:.1f} {total_h:.1f}"
     width="{total_w:.0f}" height="{total_h:.0f}"
     role="img" aria-label="Schéma panneau {format_code}">

  <defs>
    <marker id="arr" markerWidth="6" markerHeight="6"
            refX="3" refY="3" orient="auto">
      <path d="M0,0 L0,6 L6,3 z" fill="{ann_color}"/>
    </marker>
  </defs>

  <!-- Fond panneau -->
  <rect x="{rx}" y="{ry}" width="{rect_w}" height="{rect_h}"
        fill="{bg}" rx="4" ry="4"
        stroke="{border}" stroke-width="2"/>

  <!-- Texte centré : type + surface -->
  <text x="{rx + rect_w/2}" y="{ry + rect_h/2 - 6}" text-anchor="middle"
        font-size="11" font-weight="bold" fill="{txt_color}"
        font-family="sans-serif">{label_type}</text>
  <text x="{rx + rect_w/2}" y="{ry + rect_h/2 + 10}" text-anchor="middle"
        font-size="10" fill="{border}" font-family="sans-serif">{surface} m²</text>

  {annotations}
</svg>'''

    return mark_safe(svg)


@register.simple_tag
def svg_ecran(width=180, height=120):
    """
    Génère un SVG d'écran LED avec animation CSS simulant les pixels.

    Usage :
        {% svg_ecran %}
        {% svg_ecran width=200 height=140 %}
    """
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg"
     viewBox="0 0 {width} {height}"
     width="{width}" height="{height}"
     role="img" aria-label="Schéma écran LED">
  <style>
    .px {{ animation: blink 1.8s infinite; }}
    .px:nth-child(2n)  {{ animation-delay: 0.3s; }}
    .px:nth-child(3n)  {{ animation-delay: 0.6s; }}
    .px:nth-child(5n)  {{ animation-delay: 0.9s; }}
    .px:nth-child(7n)  {{ animation-delay: 1.2s; }}
    @keyframes blink {{
      0%, 100% {{ opacity: 1; }}
      50%       {{ opacity: 0.2; }}
    }}
  </style>

  <!-- Boîtier écran -->
  <rect x="4" y="4" width="{width-8}" height="{height-18}"
        fill="#111827" rx="6" stroke="#374151" stroke-width="2"/>

  <!-- Grille de pixels LED simulés -->
  {''.join(
      f'<rect class="px" x="{10 + (i % 14) * 11}" y="{10 + (i // 14) * 11}" '
      f'width="8" height="8" rx="1" '
      f'fill="{"#22d3ee" if i % 5 == 0 else "#a78bfa" if i % 7 == 0 else "#34d399"}"/>'
      for i in range(70)
  )}

  <!-- Pied de l'écran -->
  <rect x="{width//2 - 12}" y="{height-16}" width="24" height="8"
        fill="#374151" rx="2"/>
  <rect x="{width//2 - 20}" y="{height-10}" width="40" height="6"
        fill="#374151" rx="3"/>
</svg>'''

    return mark_safe(svg)


@register.filter
def statut_badge_class(statut: str) -> str:
    """Retourne la classe Bootstrap pour un statut de face."""
    return {
        'libre':   'success',
        'occupe':  'danger',
        'reserve': 'warning',
        'panne':   'secondary',
    }.get(statut, 'secondary')


@register.filter
def statut_label(statut: str) -> str:
    """Retourne le label lisible d'un statut de face."""
    return {
        'libre':   'Libre',
        'occupe':  'Occupée',
        'reserve': 'Réservée',
        'panne':   'En panne',
    }.get(statut, statut)


@register.simple_tag
def svg_panneau_mini(format_code):
    """Version miniature sans annotations pour les listes."""
    return svg_panneau(format_code, width=80, show_dims=False)
