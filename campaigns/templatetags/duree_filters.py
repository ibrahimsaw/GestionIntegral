# campaigns/templatetags/duree_filters.py
# Usage dans le template :
#   {% load duree_filters %}
#   {{ p.heures_antenne|format_heures }}       -> "1h 30min"
#   {{ p.duree_diffusion_min|format_minutes }} -> "22min 30s"

from django import template

register = template.Library()


@register.filter
def format_temps(valeur):
    """
    Convertit un float d'heures en chaîne lisible.
    Ex : 1.5 -> "1h 30min" | 0.083 -> "5min" | 2.505 -> "2h 30min 18s"
    si cest encore plus grand que 24h, on affiche aussi les jours : 26.5 -> "1j 2h 30min"
    et encore plus grand que 36j : "1mois 5j 2h" 
    """
    try:
        if isinstance(valeur, (int, float)):
            total_sec = round(valeur * 3600)
        else:
            total_sec = round(float(valeur) * 3600)
    except (TypeError, ValueError):
        return valeur
    ans, ans_reste = divmod(total_sec, 31536000)  # 365j
    mois, mois_reste = divmod(ans_reste, 2592000)  # 30j
    jours, jours_reste = divmod(mois_reste, 86400) # 24h
    heures, heures_reste = divmod(jours_reste, 3600) # 60min
    minutes, secondes = divmod(heures_reste, 60) # 60s

    parts = []
    if ans:
        parts.append(f"{ans}ans")
    if mois:
        parts.append(f"{mois}mois")
    if jours:
        parts.append(f"{jours}j")
    if heures:
        parts.append(f"{heures}h")
    if minutes:
        parts.append(f"{minutes}min")
    if secondes and not heures:   # on n'affiche les secondes que si < 1h
        parts.append(f"{secondes}s")

    return " ".join(parts) if parts else "0min"


@register.filter
def format_minutes(valeur):
    """
    Convertit un float de minutes en chaîne lisible.
    Ex : 22.5 -> "22min 30s" | 90.0 -> "1h 30min" | 133.3 -> "2h 13min 18s"
    """
    try:
        total_sec = round(float(valeur) * 60)
    except (TypeError, ValueError):
        return valeur

    heures, reste = divmod(total_sec, 3600)
    minutes, secondes = divmod(reste, 60)

    parts = []
    if heures:
        parts.append(f"{heures}h")
    if minutes:
        parts.append(f"{minutes}min")
    if secondes:
        parts.append(f"{secondes}s")

    return " ".join(parts) if parts else "0s"
