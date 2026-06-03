from django.contrib.auth.models import AbstractUser
from django.db import models


def get_choice_label(instance, field_name, choices):
    value = getattr(instance, field_name)
    return dict(choices).get(value, value)


class User(AbstractUser):
    ROLE_ADMIN      = 'admin'
    ROLE_STAFF      = 'staff'
    ROLE_TECHNICIEN = 'technicien'
    ROLE_CLIENT     = 'client'

    ROLE_CHOICES = [
        (ROLE_ADMIN,      'Administrateur'),
        (ROLE_STAFF,      'Staff Régie'),
        (ROLE_TECHNICIEN, 'Technicien'),
        (ROLE_CLIENT,     'Client'),
    ]

    role           = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_STAFF)
    telephone      = models.CharField(max_length=30, blank=True)
    avatar         = models.ImageField(upload_to='avatars/', blank=True, null=True)
    client_profile = models.OneToOneField(
        'campaigns.Client', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='user_account'
    )

    class Meta:
        verbose_name        = 'Utilisateur'
        verbose_name_plural = 'Utilisateurs'

    def get_role_display(self):
        return get_choice_label(self, 'role', self.ROLE_CHOICES)

    def __str__(self):
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"

    @property
    def is_admin(self):
        return self.role == self.ROLE_ADMIN or self.is_superuser

    @property
    def is_staff_regie_role(self):
        return self.role in (self.ROLE_ADMIN, self.ROLE_STAFF) or self.is_superuser

    @property
    def is_staff_regie(self):
        return self.role == self.ROLE_STAFF

    @property
    def is_technicien_role(self):
        return self.role in (self.ROLE_ADMIN, self.ROLE_TECHNICIEN) or self.is_superuser

    @property
    def is_technicien(self):
        return self.role == self.ROLE_TECHNICIEN

    @property
    def is_client_role(self):
        return self.role in (self.ROLE_ADMIN, self.ROLE_CLIENT) or self.is_superuser

    @property
    def is_client(self):
        return self.role == self.ROLE_CLIENT

    def get_role_badge(self):
        return {
            self.ROLE_ADMIN:      'danger',
            self.ROLE_TECHNICIEN: 'warning',
            self.ROLE_STAFF:      'primary',
            self.ROLE_CLIENT:     'info',
        }.get(self.role, 'secondary')

    @property
    def client(self):
        """Raccourci : retourne le Client lié ou None."""
        if self.role == self.ROLE_CLIENT:
            return self.client_profile
        return None


class AuditLog(models.Model):
    """
    Journal d'audit de toutes les interactions sur le site.

    Principe : ACTION + MODULE donnent toute l'information.
        ACTION_CREATE + module='campaigns'  → création d'une campagne
        ACTION_DELETE + module='inventory'  → suppression d'un support
        ACTION_ETAT_CHANGE + module='inventory' → passage d'un support en panne
    """

    # ── Actions CRUD — valables pour tous les modules ─────────────────────────
    ACTION_LOGIN           = 'login'
    ACTION_LOGOUT          = 'logout'
    ACTION_LOGIN_FAILED    = 'login_failed'
    ACTION_PASSWORD_CHANGE = 'password_change'
    ACTION_CREATE          = 'create'
    ACTION_UPDATE          = 'update'
    ACTION_DELETE          = 'delete'
    ACTION_VIEW            = 'view'
    ACTION_EXPORT          = 'export'
    ACTION_IMPORT          = 'import'

    # ── Actions métier — impossibles à exprimer avec CRUD seul ───────────────
    ACTION_ACTIVATE    = 'activate'     # campagne → en_cours
    ACTION_PAUSE       = 'pause'        # campagne → en_pause
    ACTION_CLOSE       = 'close'        # campagne → terminée
    ACTION_VALIDATE    = 'validate'     # validation devis / bon de commande
    ACTION_ETAT_CHANGE = 'etat_change'  # support → panne / maintenance / bon
    ACTION_ROLE_CHANGE = 'role_change'  # changement de rôle utilisateur
    ACTION_ADD   = 'add'    # ajout
    ACTION_MAP_CLICK   = 'map_click'    # clic sur un support sur la carte

    ACTION_CHOICES = [
        # Authentification
        (ACTION_LOGIN,           'Connexion'),
        (ACTION_LOGOUT,          'Déconnexion'),
        (ACTION_LOGIN_FAILED,    'Tentative de connexion échouée'),
        (ACTION_PASSWORD_CHANGE, 'Changement de mot de passe'),
        # CRUD
        (ACTION_CREATE, 'Création'),
        (ACTION_UPDATE, 'Modification'),
        (ACTION_DELETE, 'Suppression'),
        (ACTION_VIEW,   'Consultation'),
        (ACTION_EXPORT, 'Export'),
        (ACTION_IMPORT, 'Import'),
        # Métier
        (ACTION_ACTIVATE,    'Activation'),
        (ACTION_PAUSE,       'Mise en pause'),
        (ACTION_CLOSE,       'Clôture'),
        (ACTION_VALIDATE,    'Validation'),
        (ACTION_ETAT_CHANGE, 'Changement d\'état'),
        (ACTION_ROLE_CHANGE, 'Changement de rôle'),
        (ACTION_ADD,   'Ajout'),
        (ACTION_MAP_CLICK,   'Clic carte'),
    ]

    # ── Modules ───────────────────────────────────────────────────────────────
    MODULE_AUTH      = 'auth'
    MODULE_INVENTORY = 'inventory'
    MODULE_CAMPAIGNS = 'campaigns'
    MODULE_CLIENTS   = 'clients'
    MODULE_USERS     = 'users'
    MODULE_PLANNING  = 'planning'
    MODULE_MAP       = 'map'
    MODULE_REPORTS   = 'reports'

    MODULE_CHOICES = [
        (MODULE_AUTH,      'Authentification'),
        (MODULE_INVENTORY, 'Inventaire'),
        (MODULE_CAMPAIGNS, 'Campagnes'),
        (MODULE_CLIENTS,   'Clients'),
        (MODULE_USERS,     'Utilisateurs'),
        (MODULE_PLANNING,  'Planning'),
        (MODULE_MAP,       'Carte'),
        (MODULE_REPORTS,   'Rapports'),
    ]

    # ── Niveaux de sévérité ───────────────────────────────────────────────────
    LEVEL_INFO     = 'info'
    LEVEL_WARNING  = 'warning'
    LEVEL_CRITICAL = 'critical'

    LEVEL_CHOICES = [
        (LEVEL_INFO,     'Info'),
        (LEVEL_WARNING,  'Avertissement'),
        (LEVEL_CRITICAL, 'Critique'),
    ]

    # ── Champs ────────────────────────────────────────────────────────────────
    user   = models.ForeignKey(
        User, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='audit_logs'
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    module = models.CharField(max_length=20, choices=MODULE_CHOICES, default=MODULE_INVENTORY)
    level  = models.CharField(max_length=10, choices=LEVEL_CHOICES, default=LEVEL_INFO)

    # Objet concerné
    object_id   = models.PositiveIntegerField(null=True, blank=True)
    object_repr = models.CharField(max_length=200, blank=True)  # ex: "Campagne Coca-Cola #42"
    object_url  = models.CharField(max_length=300, blank=True)  # lien direct vers l'objet

    # Détail
    detail  = models.TextField(blank=True)
    changes = models.JSONField(null=True, blank=True)
    # Format du champ changes :
    # {
    #   "etat":    {"avant": "bon",          "apres": "panne"},
    #   "adresse": {"avant": "Av. Kwame N.", "apres": "Rue du Commerce"},
    # }

    # Contexte réseau
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = 'Journal d\'audit'
        verbose_name_plural = 'Journal d\'audit'
        ordering            = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['action', 'created_at']),
            models.Index(fields=['module', 'created_at']),
            models.Index(fields=['level']),
        ]

    def __str__(self):
        return (
            f"[{self.created_at:%d/%m/%Y %H:%M}] "
            f"{self.user} — {self.get_action_display()} — {self.module}"
        )

    def get_action_display(self) -> str:
        return dict(self.ACTION_CHOICES).get(self.action, self.action)

    def get_action_badge(self):
        return {
            self.ACTION_LOGIN:           'success',
            self.ACTION_LOGOUT:          'secondary',
            self.ACTION_LOGIN_FAILED:    'danger',
            self.ACTION_PASSWORD_CHANGE: 'warning',
            self.ACTION_CREATE:          'primary',
            self.ACTION_UPDATE:          'warning',
            self.ACTION_DELETE:          'danger',
            self.ACTION_VIEW:            'info',
            self.ACTION_EXPORT:          'dark',
            self.ACTION_IMPORT:          'dark',
            self.ACTION_ACTIVATE:        'success',
            self.ACTION_VALIDATE:        'success',
            self.ACTION_PAUSE:           'secondary',
            self.ACTION_CLOSE:           'secondary',
            self.ACTION_ETAT_CHANGE:     'warning',
            self.ACTION_ROLE_CHANGE:     'warning',
            self.ACTION_ADD:             'info',
            self.ACTION_MAP_CLICK:       'info',
        }.get(self.action, 'secondary')

    def get_level_badge(self):
        return {
            self.LEVEL_INFO:     'info',
            self.LEVEL_WARNING:  'warning',
            self.LEVEL_CRITICAL: 'danger',
        }.get(self.level, 'secondary')

    @property
    def has_changes(self):
        """True si des modifications de champs sont enregistrées."""
        return bool(self.changes)

    @property
    def is_critical(self):
        return self.level == self.LEVEL_CRITICAL
    
    
