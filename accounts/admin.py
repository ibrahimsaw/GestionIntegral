from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.html import format_html
from .models import User, AuditLog


# ── Couleurs Bootstrap → hex ──────────────────────────────────────────────────
BADGE_COLORS = {
    'success':   '#198754',
    'secondary': '#6c757d',
    'primary':   '#932E2B',
    'warning':   '#c77c00',   # assombri pour lisibilité sur blanc
    'danger':    '#dc3545',
    'info':      '#0a7d8c',   # assombri pour lisibilité sur blanc
    'dark':      '#343a40',
}


def make_badge(text, badge_key):
    color = BADGE_COLORS.get(badge_key, '#6c757d')
    return format_html(
        '<span style="background:{};color:white;padding:3px 10px;'
        'border-radius:10px;font-size:11px;font-weight:600;">{}</span>',
        color, text
    )


# ── Admin Utilisateur ─────────────────────────────────────────────────────────
@admin.register(User)
class CustomUserAdmin(UserAdmin):

    list_display  = ('username', 'full_name', 'email', 'role_badge', 'telephone', 'est_actif', 'date_joined')
    list_filter   = ('role', 'is_active', 'date_joined')
    search_fields = ('username', 'first_name', 'last_name', 'email', 'telephone')
    ordering      = ('-date_joined',)

    fieldsets = UserAdmin.fieldsets + (
        ('Informations Integral', {
            'fields': ('role', 'telephone', 'avatar', 'client_profile')
        }),
    )

    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Informations Integral', {
            'fields': ('first_name', 'last_name', 'email', 'role', 'telephone', 'avatar', 'client_profile')
        }),
    )

    def full_name(self, obj):
        return obj.get_full_name() or '—'
    full_name.short_description = 'Nom complet'

    def role_badge(self, obj):
        return make_badge(obj.get_role_display(), obj.get_role_badge())
    role_badge.short_description = 'Rôle'

    def est_actif(self, obj):
        if obj.is_active:
            return format_html('<span style="color:#198754;font-weight:600;">✔ Actif</span>')
        return format_html('<span style="color:#dc3545;font-weight:600;">✘ Inactif</span>')
    est_actif.short_description = 'Statut'


# ── Admin Audit Log ───────────────────────────────────────────────────────────
@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):

    list_display  = (
        'created_at', 'user', 'action_badge', 'level_badge',
        'module', 'object_repr', 'object_link', 'ip_address',
    )
    list_filter   = ('action', 'module', 'level', 'created_at')
    search_fields = ('user__username', 'user__first_name', 'object_repr', 'detail', 'ip_address')
    ordering      = ('-created_at',)
    date_hierarchy = 'created_at'

    readonly_fields = (
        'user', 'action', 'module', 'level',
        'object_id', 'object_repr', 'object_url', 'object_link',
        'detail', 'changes_display',
        'ip_address', 'user_agent', 'created_at',
    )

    # Affichage de la page de détail
    fieldsets = (
        ('Qui / Quand', {
            'fields': ('user', 'created_at', 'ip_address', 'user_agent')
        }),
        ('Action', {
            'fields': ('action', 'module', 'level')
        }),
        ('Objet concerné', {
            'fields': ('object_repr', 'object_link', 'object_id')
        }),
        ('Détails', {
            'fields': ('detail', 'changes_display'),
        }),
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        # Seuls les admins peuvent supprimer des logs
        return request.user.is_admin

    def has_change_permission(self, request, obj=None):
        # Le journal est en lecture seule
        return False

    # ── Colonnes personnalisées ───────────────────────────────────────────────

    def action_badge(self, obj):
        return make_badge(obj.get_action_display(), obj.get_action_badge())
    action_badge.short_description = 'Action'

    def level_badge(self, obj):
        return make_badge(obj.get_level_display(), obj.get_level_badge())
    level_badge.short_description = 'Niveau'

    def object_link(self, obj):
        """Lien cliquable vers l'objet si object_url est renseigné."""
        if obj.object_url:
            return format_html(
                '<a href="{}" target="_blank" style="font-size:11px;">↗ Voir</a>',
                obj.object_url
            )
        return '—'
    object_link.short_description = 'Lien'

    def changes_display(self, obj):
        """Affiche le dict changes de façon lisible dans la page de détail."""
        if not obj.changes:
            return '—'
        rows = []
        for field, vals in obj.changes.items():
            rows.append(
                f'<tr>'
                f'<td style="padding:4px 10px;font-weight:600;color:#555">{field}</td>'
                f'<td style="padding:4px 10px;color:#dc3545;text-decoration:line-through">{vals.get("avant", "")}</td>'
                f'<td style="padding:4px 10px;color:#198754">{vals.get("apres", "")}</td>'
                f'</tr>'
            )
        return format_html(
            '<table style="border-collapse:collapse;font-size:12px;font-family:monospace">'
            '<thead><tr>'
            '<th style="padding:4px 10px;text-align:left;color:#888">Champ</th>'
            '<th style="padding:4px 10px;text-align:left;color:#888">Avant</th>'
            '<th style="padding:4px 10px;text-align:left;color:#888">Après</th>'
            '</tr></thead><tbody>{}</tbody></table>',
            format_html(''.join(rows))
        )
    changes_display.short_description = 'Modifications'