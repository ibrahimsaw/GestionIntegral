"""
utils/audit.py
--------------
Fonctions centralisées pour enregistrer toutes les actions dans AuditLog.

Utilisation dans vos views :

    from utils.audit import log_action, log_changes
    from users.models import AuditLog

    # Création simple
    log_action(request, AuditLog.ACTION_CREATE, AuditLog.MODULE_INVENTORY, obj=support)

    # Modification avec détail des champs changés
    old = Support.objects.get(pk=pk)
    support = form.save()
    changes = log_changes(old, support, ['nom', 'etat', 'adresse'])
    log_action(request, AuditLog.ACTION_UPDATE, AuditLog.MODULE_INVENTORY,
               obj=support, changes=changes)

    # Action métier
    log_action(request, AuditLog.ACTION_ETAT_CHANGE, AuditLog.MODULE_INVENTORY,
               obj=support, detail="Passage en panne suite à intervention terrain",
               changes={"etat": {"avant": "bon", "apres": "panne"}})
"""

from .models import AuditLog


# ── IP ────────────────────────────────────────────────────────────────────────

def get_client_ip(request):
    """Récupère la vraie IP du client même derrière un proxy / nginx."""
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


# ── Niveau automatique ────────────────────────────────────────────────────────

def _get_level(action):
    """
    Déduit automatiquement le niveau de sévérité selon l'action.
    Peut être surchargé en passant level= à log_action().
    """
    if action in (
        AuditLog.ACTION_DELETE,
        AuditLog.ACTION_LOGIN_FAILED,
    ):
        return AuditLog.LEVEL_CRITICAL

    if action in (
        AuditLog.ACTION_ETAT_CHANGE,
        AuditLog.ACTION_ROLE_CHANGE,
        AuditLog.ACTION_PASSWORD_CHANGE,
        AuditLog.ACTION_PAUSE,
        AuditLog.ACTION_CLOSE,
    ):
        return AuditLog.LEVEL_WARNING

    return AuditLog.LEVEL_INFO


# ── URL de l'objet ────────────────────────────────────────────────────────────

def _get_object_url(obj):
    """Tente de récupérer l'URL de l'objet via get_absolute_url()."""
    if obj is None:
        return ''
    try:
        return obj.get_absolute_url()
    except AttributeError:
        return ''


# ── Fonctions principales ─────────────────────────────────────────────────────

def log_action(request, action, module, obj=None, detail='', changes=None, level=None):
    """
    Enregistre une action dans le journal d'audit.

    Paramètres :
        request  : HttpRequest Django
        action   : constante AuditLog.ACTION_*
        module   : constante AuditLog.MODULE_*
        obj      : instance du modèle concerné (optionnel)
        detail   : description textuelle libre (optionnel)
        changes  : dict {"champ": {"avant": ..., "apres": ...}} (optionnel)
        level    : forcer un niveau — sinon déduit automatiquement par _get_level()
    """
    user = request.user if request.user.is_authenticated else None

    AuditLog.objects.create(
        user        = user,
        action      = action,
        module      = module,
        level       = level or _get_level(action),
        object_id   = obj.pk   if obj else None,
        object_repr = str(obj) if obj else '',
        object_url  = _get_object_url(obj),
        detail      = detail,
        changes     = changes,
        ip_address  = get_client_ip(request),
        user_agent  = request.META.get('HTTP_USER_AGENT', '')[:300],
    )


def log_changes(old_obj, new_obj, fields):
    """
    Compare deux instances d'un modèle sur les champs donnés.
    Retourne un dict des champs modifiés, ou None si rien n'a changé.

    Utilisation typique dans une view UPDATE :
        old = Support.objects.get(pk=pk)
        support = form.save()
        changes = log_changes(old, support, ['nom', 'etat', 'adresse', 'quartier'])
        log_action(request, AuditLog.ACTION_UPDATE, AuditLog.MODULE_INVENTORY,
                   obj=support, changes=changes)

    Retourne par exemple :
        {
            "etat":    {"avant": "bon",          "apres": "panne"},
            "adresse": {"avant": "Av. Kwame N.", "apres": "Rue du Commerce"},
        }
    """
    diff = {}
    for field in fields:
        avant = getattr(old_obj, field, None)
        apres = getattr(new_obj, field, None)
        if avant != apres:
            diff[field] = {
                'avant': str(avant) if avant is not None else '',
                'apres': str(apres) if apres is not None else '',
            }
    return diff or None