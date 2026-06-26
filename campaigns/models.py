import datetime
import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# ══════════════════════════════════════════════════════════════════════════════
# Fonctions utilitaires
# ══════════════════════════════════════════════════════════════════════════════

def calculer_duree_tranches(tranches: str) -> float:
    total_heures = 0.0
    if not tranches:
        return total_heures
    plages = tranches.split(",")
    for plage in plages:
        try:
            debut_str, fin_str = plage.split("-")
            debut = datetime.datetime.strptime(debut_str.strip(), "%H:%M")
            fin = datetime.datetime.strptime(fin_str.strip(), "%H:%M")
            duree = (fin - debut).total_seconds() / 3600
            total_heures += duree
        except ValueError:
            continue
    return total_heures


# ══════════════════════════════════════════════════════════════════════════════
# Constantes & Choix
# ══════════════════════════════════════════════════════════════════════════════

DUREE_CHOICES = [
    (5, '5 secondes'),
    (10, '10 secondes'),
    (15, '15 secondes'),
    (20, '20 secondes'),
]

FREQUENCE_CHOICES = [
    (60, 'Toutes les 1 min'),
    (120, 'Toutes les 2 min'),
    (180, 'Toutes les 3 min'),
    (240, 'Toutes les 4 min'),
    (300, 'Toutes les 5 min'),
    (600, 'Toutes les 10 min'),
    (900, 'Toutes les 15 min'),
    (1200, 'Toutes les 20 min'),
    (1800, 'Toutes les 30 min'),
]

STATUT_BROUILLON = 'brouillon'
STATUT_A_VENIR   = 'a_venir'
STATUT_EN_COURS  = 'en_cours'
STATUT_TERMINEE  = 'terminee'
STATUT_ANNULEE   = 'annulee'

STATUT_CHOICES = [
    (STATUT_BROUILLON, 'Brouillon'),
    (STATUT_A_VENIR,   'À venir'),
    (STATUT_EN_COURS,  'En cours'),
    (STATUT_TERMINEE,  'Terminée'),
    (STATUT_ANNULEE,   'Annulée'),
]

TYPE_CONTRAT_CHOICES = [
    ('annuel',   'Contrat Annuel'),
    ('mensuel',  'Contrat Mensuel'),
    ('ponctuel', 'Ponctuel'),
]

TYPE_SUPPORT_CHOICES = [
    ('panneau', 'Panneau'),
    ('ecran', 'Écran'),
]

STATUT_EN_ATTENTE = 'en_attente'
STATUT_CONFIRMEE  = 'confirmee'
STATUT_EXPIREE    = 'expiree'

STATUT_RESERVATION_CHOICES = [
    (STATUT_EN_ATTENTE, 'En attente'),
    (STATUT_CONFIRMEE,  'Confirmée'),
    (STATUT_ANNULEE,    'Annulée'),
    (STATUT_EXPIREE,    'Expirée'),
]


# ══════════════════════════════════════════════════════════════════════════════
# Modèles de Base (Clients & Contrats)
# ══════════════════════════════════════════════════════════════════════════════

class Client(models.Model):
    """Client de la régie publicitaire."""
    nom           = models.CharField(max_length=200, verbose_name="Raison sociale / Nom")
    contact_nom   = models.CharField(max_length=150, blank=True, verbose_name="Nom du Contact principal")
    telephone     = models.CharField(max_length=30, blank=True)
    email         = models.EmailField(blank=True)
    adresse       = models.TextField(blank=True)
    logo          = models.ImageField(upload_to='logos/', blank=True, null=True)
    notes         = models.TextField(blank=True)
    created_at    = models.DateTimeField(auto_now_add=True)
    actif         = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Client"
        verbose_name_plural = "Clients"
        ordering = ['nom']

    def __str__(self):
        return self.nom

    def get_contrat_actif(self):
        today = timezone.now().date()
        return self.contrats.filter(
            actif=True,
            date_debut__lte=today,
            date_fin__gte=today,
        ).order_by('-date_debut').first()

    def get_type_contrat_display(self):
        contrat = self.get_contrat_actif()
        if contrat:
            return f"{contrat.get_type_contrat_display()} — {contrat.nb_spots} spots"
        return "Aucun contrat actif"

    def campagnes_actives(self):
        today = timezone.now().date()
        return self.campagnes.filter(date_debut__lte=today, date_fin__gte=today, statut__in=['en_cours', 'a_venir'])


class Contrat(models.Model):
    """Contrat signé par un client."""
    client       = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='contrats')
    type_contrat = models.CharField(max_length=20, choices=TYPE_CONTRAT_CHOICES, default='ponctuel')
    nom          = models.CharField(max_length=200, blank=True, verbose_name="Nom du contrat (optionnel)")
    date_debut   = models.DateField(verbose_name="Date de début")
    date_fin     = models.DateField(verbose_name="Date de fin")
    nb_spots     = models.PositiveIntegerField(default=0, verbose_name="Nombre de spots")
    actif        = models.BooleanField(default=True)
    archive      = models.BooleanField(default=True)
    notes        = models.TextField(blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Contrat"
        verbose_name_plural = "Contrats"
        ordering = ['-date_debut']

    def __str__(self):
        return f"{self.client.nom} — {self.get_type_contrat_display()} ({self.date_debut:%d/%m/%Y} → {self.date_fin:%d/%m/%Y})"
    
    def get_nom(self):
        if self.nom:
            return f"{self.nom} \n ({self.date_debut:%d/%m/%Y} → {self.date_fin:%d/%m/%Y})"
        return self.__str__()
    
    def nombre_jours(self):
        if self.date_debut and self.date_fin:
            return (self.date_fin - self.date_debut).days + 1
        return 0

    def spots_utilises(self):
        return sum(c.calculer_nombre_spots() for c in self.campagnes.filter(statut__in=['en_cours', 'terminee']))
    
    def spots_restants(self):
        return max(self.nb_spots - self.spots_utilises(), 0)
    
    def get_detail(self):
        if self.nom:
            return f"{self.nom} — {self.spots_restants()} spots"
        return f"{self.client.nom} — {self.spots_restants()} spots"

    def duree_jours(self):
        if self.date_debut and self.date_fin:
            return (self.date_fin - self.date_debut).days
        return 0

    def is_actif(self):
        today = timezone.now().date()
        return self.actif and self.date_debut <= today <= self.date_fin


# ══════════════════════════════════════════════════════════════════════════════
# Modèles de Réservations (Ordre de dépendance strict : 1. Reservation -> 2. Lignes)
# ══════════════════════════════════════════════════════════════════════════════

class Reservation(models.Model):
    """Groupe de réservation globale pour un client."""
    uuid = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        db_index=True,
        verbose_name="Identifiant public (UUID)",
    )
    reference = models.CharField(
        max_length=50,
        unique=True,
        blank=True,
        verbose_name="Référence",
    )
    nom = models.CharField(
        max_length=200,
        verbose_name="Nom de la réservation",
    )
    client = models.ForeignKey(
        Client,
        on_delete=models.CASCADE,
        related_name='reservations_globales',
        verbose_name="Client",
    )
    contrat = models.ForeignKey(
        Contrat,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='reservations',
        verbose_name="Contrat associé (optionnel)",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='reservations_creees',
        verbose_name="Créé par",
    )
    date_debut = models.DateTimeField(verbose_name="Date de début")
    date_fin   = models.DateTimeField(verbose_name="Date de fin")
    statut = models.CharField(
        max_length=20,
        choices=STATUT_RESERVATION_CHOICES,
        default=STATUT_EN_ATTENTE,
        verbose_name="Statut",
    )
    notes      = models.TextField(blank=True, verbose_name="Notes internes")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name          = "Réservation"
        verbose_name_plural   = "Réservations"
        ordering              = ['-date_debut']

    def __str__(self):
        return f"{self.reference} — {self.client.nom} ({self.date_debut:%d/%m/%Y} → {self.date_fin:%d/%m/%Y})"

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = f"RES-{timezone.now().year}-{str(uuid.uuid4())[:6].upper()}"
        super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.date_debut and self.date_fin:
            if self.date_fin <= self.date_debut:
                raise ValidationError({
                    'date_fin': _("La date de fin doit être postérieure à la date de début.")
                })
            if (self.date_fin - self.date_debut).days > 366:
                raise ValidationError(_("La durée d'une réservation ne peut pas dépasser 1 an."))

    def duree_jours(self) -> int:
        if self.date_debut and self.date_fin:
            return (self.date_fin.date() - self.date_debut.date()).days + 1
        return 0

    def nb_faces(self) -> int:
        return self.lignes.count()

    def is_active(self) -> bool:
        now = timezone.now()
        return self.statut in (STATUT_EN_ATTENTE, STATUT_CONFIRMEE) and self.date_debut <= now <= self.date_fin

    def is_a_venir(self) -> bool:
        return self.statut in (STATUT_EN_ATTENTE, STATUT_CONFIRMEE) and timezone.now() < self.date_debut

    def is_expiree(self) -> bool:
        return timezone.now() > self.date_fin

    def get_statut_badge(self) -> str:
        return {
            STATUT_EN_ATTENTE: 'warning',
            STATUT_CONFIRMEE:  'success',
            STATUT_ANNULEE:    'danger',
            STATUT_EXPIREE:    'secondary',
        }.get(self.statut, 'secondary')

    def auto_update_statut(self):
        if self.statut in (STATUT_ANNULEE,):
            return
        if self.is_expiree():
            self.statut = STATUT_EXPIREE
            self.save(update_fields=['statut'])


class ReservationLigne(models.Model):
    """Une face réservée liée à un groupe de Réservation parent (Désormais défini APRÈS Reservation)."""
    reservation = models.ForeignKey(Reservation, on_delete=models.CASCADE, related_name='lignes', verbose_name="Réservation")
    support = models.ForeignKey('inventory.Support', on_delete=models.PROTECT, related_name='lignes_reservation', verbose_name="Support")
    face = models.ForeignKey('inventory.FacePanneau', on_delete=models.SET_NULL, null=True, blank=True, related_name='lignes_reservation', verbose_name="Face")
    notes      = models.TextField(blank=True, verbose_name="Notes")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = "Ligne de réservation"
        verbose_name_plural = "Lignes de réservation"
        ordering            = ['support__code', 'face__label']
        unique_together     = [('reservation', 'face')]

    def __str__(self):
        face_label = f"Face {self.face.label}" if self.face else "—"
        return f"{self.reservation.reference} | {self.support.code} ({face_label})"

    def clean(self):
        super().clean()
        if self.support.type_support != 'panneau':
            raise ValidationError(_("Les réservations sont uniquement applicables aux panneaux statiques."))
        if not self.face:
            raise ValidationError(_("Vous devez sélectionner une face pour un panneau statique."))
        if self.face.support_id != self.support_id:
            raise ValidationError(_("La face sélectionnée n'appartient pas au support choisi."))

        date_debut = self.reservation.date_debut
        date_fin   = self.reservation.date_fin

        conflit_resa = (
            ReservationLigne.objects.filter(
                face=self.face,
                reservation__date_debut__lt=date_fin,
                reservation__date_fin__gt=date_debut,
                reservation__statut__in=[STATUT_EN_ATTENTE, STATUT_CONFIRMEE],
            )
            .exclude(reservation=self.reservation)
            .select_related('reservation__client')
        )
        if conflit_resa.exists():
            conflit = conflit_resa.first()
            raise ValidationError(_(f"La face {self.face} est déjà réservée du {conflit.reservation.date_debut:%d/%m/%Y} au {conflit.reservation.date_fin:%d/%m/%Y} (client : {conflit.reservation.client.nom})."))

        from campaigns.models import LigneCampagne
        conflit_camp = LigneCampagne.objects.filter(
            face=self.face,
            campagne__date_debut__lte=date_fin.date(),
            campagne__date_fin__gte=date_debut.date(),
            campagne__statut__in=['en_cours', 'a_venir'],
        ).select_related('campagne__client')

        if conflit_camp.exists():
            conflit = conflit_camp.first()
            raise ValidationError(_(f"La face {self.face} est occupée par la campagne « {conflit.campagne.nom} » (client : {conflit.campagne.client.nom}) du {conflit.campagne.date_debut:%d/%m/%Y} au {conflit.campagne.date_fin:%d/%m/%Y}."))

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def est_active(self) -> bool:
        return self.reservation.is_active()

    @property
    def date_debut(self):
        return self.reservation.date_debut

    @property
    def date_fin(self):
        return self.reservation.date_fin




# ══════════════════════════════════════════════════════════════════════════════
# À AJOUTER À LA FIN DE campaigns/models.py
# (après la classe LigneCampagne existante)
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# DemandeReservation — Demande publique (visiteur non connecté)
# ══════════════════════════════════════════════════════════════════════════════

class DemandeReservation(models.Model):
    """
    Demande de réservation soumise depuis le portail public par un visiteur
    non authentifié.

    Workflow :
        Visiteur soumet → statut='nouvelle'
        Staff traite   → statut='en_cours'
        Staff valide   → crée Client + Reservation → statut='validee'
        Staff refuse   → statut='refusee'

    IMPORTANT : Ce modèle ne crée PAS automatiquement de Client ni de
    Reservation. C'est le staff qui effectue ces opérations manuellement
    depuis la vue de traitement (/staff/demandes/<uuid>/).
    """

    # ── Statuts ───────────────────────────────────────────────────────────────
    STATUT_NOUVELLE  = 'nouvelle'
    STATUT_EN_COURS  = 'en_cours'
    STATUT_VALIDEE   = 'validee'
    STATUT_REFUSEE   = 'refusee'

    STATUT_CHOICES = [
        (STATUT_NOUVELLE,  'Nouvelle'),
        (STATUT_EN_COURS,  'En cours de traitement'),
        (STATUT_VALIDEE,   'Validée — Réservation créée'),
        (STATUT_REFUSEE,   'Refusée'),
    ]

    # ── Identité ──────────────────────────────────────────────────────────────
    uuid = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        db_index=True,
        verbose_name="Identifiant public",
    )
    reference = models.CharField(
        max_length=50,
        unique=True,
        blank=True,
        verbose_name="Référence",
        help_text="Générée automatiquement. Ex: DEM-2026-A3F9K2",
    )

    # ── Coordonnées du visiteur ───────────────────────────────────────────────
    nom_contact = models.CharField(
        max_length=200,
        verbose_name="Nom complet",
    )
    societe = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Société / Organisation",
    )
    email = models.EmailField(
        verbose_name="Email",
    )
    telephone = models.CharField(
        max_length=30,
        verbose_name="Téléphone",
    )
    accepte_contact = models.BooleanField(
        default=False,
        verbose_name="Accepte d'être recontacté",
        help_text="Le visiteur a coché la case d'acceptation de contact commercial.",
    )

    # ── Supports souhaités ────────────────────────────────────────────────────
    # ManyToMany vers FacePanneau : les faces sélectionnées sur la carte
    faces_souhaitees = models.ManyToManyField(
        'inventory.FacePanneau',
        blank=True,
        related_name='demandes_reservation',
        verbose_name="Faces souhaitées",
    )
    # ManyToMany vers Support : pour les écrans (pas de face)
    supports_souhaites = models.ManyToManyField(
        'inventory.Support',
        blank=True,
        related_name='demandes_reservation',
        verbose_name="Supports souhaités (écrans)",
    )

    # ── Période et projet ─────────────────────────────────────────────────────
    date_debut_souhaitee = models.DateField(
        verbose_name="Date de début souhaitée",
    )
    date_fin_souhaitee = models.DateField(
        verbose_name="Date de fin souhaitée",
    )
    nom_campagne = models.CharField(
        max_length=200,
        blank=True,
        verbose_name="Nom de la campagne / projet",
    )
    message = models.TextField(
        blank=True,
        verbose_name="Message libre",
    )

    # ── Traitement staff ──────────────────────────────────────────────────────
    statut = models.CharField(
        max_length=20,
        choices=STATUT_CHOICES,
        default=STATUT_NOUVELLE,
        verbose_name="Statut",
        db_index=True,
    )
    notes_staff = models.TextField(
        blank=True,
        verbose_name="Notes internes staff",
        help_text="Visible uniquement par le staff. Motif de refus, remarques…",
    )
    traite_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='demandes_traitees',
        verbose_name="Traité par",
    )
    traite_le = models.DateTimeField(
        null=True, blank=True,
        verbose_name="Traité le",
    )

    # ── Associations créées après validation ──────────────────────────────────
    # Remplies par le staff depuis la vue de traitement
    client = models.ForeignKey(
        Client,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='demandes_reservation',
        verbose_name="Client associé",
        help_text="Renseigné par le staff après validation.",
    )
    reservation = models.ForeignKey(
        Reservation,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='demande_origine',
        verbose_name="Réservation créée",
        help_text="Réservation officielle créée par le staff après validation.",
    )

    # ── Horodatage ────────────────────────────────────────────────────────────
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Créée le",
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Mise à jour le",
    )

    # ── Meta ──────────────────────────────────────────────────────────────────
    class Meta:
        verbose_name          = "Demande de réservation"
        verbose_name_plural   = "Demandes de réservation"
        ordering              = ['-created_at']
        indexes               = [
            models.Index(fields=['statut', '-created_at']),
            models.Index(fields=['email']),
        ]

    def __str__(self):
        return f"{self.reference} — {self.nom_contact} ({self.get_statut_display()})"

    # ── Sauvegarde ────────────────────────────────────────────────────────────

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = f"DEM-{timezone.now().year}-{str(uuid.uuid4())[:6].upper()}"
        super().save(*args, **kwargs)

    # ── Validation ────────────────────────────────────────────────────────────

    def clean(self):
        super().clean()
        if self.date_debut_souhaitee and self.date_fin_souhaitee:
            if self.date_fin_souhaitee <= self.date_debut_souhaitee:
                raise ValidationError({
                    'date_fin_souhaitee': _(
                        "La date de fin doit être postérieure à la date de début."
                    )
                })
            duree = (self.date_fin_souhaitee - self.date_debut_souhaitee).days
            if duree > 366:
                raise ValidationError(
                    _("La durée souhaitée ne peut pas dépasser 1 an.")
                )

    # ── Propriétés ────────────────────────────────────────────────────────────

    @property
    def duree_jours(self) -> int:
        if self.date_debut_souhaitee and self.date_fin_souhaitee:
            return (self.date_fin_souhaitee - self.date_debut_souhaitee).days + 1
        return 0

    @property
    def est_nouvelle(self) -> bool:
        return self.statut == self.STATUT_NOUVELLE

    @property
    def est_en_cours(self) -> bool:
        return self.statut == self.STATUT_EN_COURS

    @property
    def est_validee(self) -> bool:
        return self.statut == self.STATUT_VALIDEE

    @property
    def est_refusee(self) -> bool:
        return self.statut == self.STATUT_REFUSEE

    @property
    def peut_etre_traitee(self) -> bool:
        """True si la demande peut encore être validée ou refusée."""
        return self.statut in (self.STATUT_NOUVELLE, self.STATUT_EN_COURS)

    def get_statut_badge(self) -> str:
        """Retourne la classe Bootstrap pour le badge de statut."""
        return {
            self.STATUT_NOUVELLE:  'warning',
            self.STATUT_EN_COURS:  'info',
            self.STATUT_VALIDEE:   'success',
            self.STATUT_REFUSEE:   'danger',
        }.get(self.statut, 'secondary')

    def get_statut_icon(self) -> str:
        """Retourne l'icône Bootstrap Icons pour le statut."""
        return {
            self.STATUT_NOUVELLE:  'bi-clock',
            self.STATUT_EN_COURS:  'bi-hourglass-split',
            self.STATUT_VALIDEE:   'bi-check-circle-fill',
            self.STATUT_REFUSEE:   'bi-x-circle-fill',
        }.get(self.statut, 'bi-question-circle')

    def marquer_en_cours(self, user=None):
        """Passe la demande en statut 'en_cours'."""
        self.statut = self.STATUT_EN_COURS
        if user:
            self.traite_par = user
        self.save(update_fields=['statut', 'traite_par', 'updated_at'])

    def marquer_validee(self, client, reservation, user):
        """
        Marque la demande comme validée et associe le client et la réservation.
        À appeler APRÈS la création atomique de Client + Reservation.
        """
        self.statut      = self.STATUT_VALIDEE
        self.client      = client
        self.reservation = reservation
        self.traite_par  = user
        self.traite_le   = timezone.now()
        self.save(update_fields=[
            'statut', 'client', 'reservation',
            'traite_par', 'traite_le', 'updated_at',
        ])

    def marquer_refusee(self, user, notes: str = ''):
        """Marque la demande comme refusée."""
        self.statut     = self.STATUT_REFUSEE
        self.traite_par = user
        self.traite_le  = timezone.now()
        if notes:
            self.notes_staff = notes
        self.save(update_fields=[
            'statut', 'traite_par', 'traite_le',
            'notes_staff', 'updated_at',
        ])

    def get_resume_emplacements(self) -> str:
        """
        Retourne un résumé textuel des emplacements demandés.
        Ex: "OUA-001 Face A, BOB-012 Face B"
        Utilisé dans les emails et la liste staff.
        """
        parties = []
        for face in self.faces_souhaitees.select_related('support').all():
            parties.append(f"{face.support.code} Face {face.label}")
        for support in self.supports_souhaites.all():
            parties.append(f"{support.code} (Écran)")
        return ", ".join(parties) if parties else "Aucun emplacement"

    def nb_emplacements(self) -> int:
        """Nombre total d'emplacements demandés (faces + écrans)."""
        return self.faces_souhaitees.count() + self.supports_souhaites.count()



# ══════════════════════════════════════════════════════════════════════════════
# Modèles de Campagnes (Écrans & Panneaux)
# ══════════════════════════════════════════════════════════════════════════════

class Campagne(models.Model):
    """Campagne publicitaire globale."""
    STATUT_BROUILLON = STATUT_BROUILLON
    STATUT_A_VENIR   = STATUT_A_VENIR
    STATUT_EN_COURS  = STATUT_EN_COURS
    STATUT_TERMINEE  = STATUT_TERMINEE
    STATUT_ANNULEE   = STATUT_ANNULEE
    STATUT_CHOICES   = STATUT_CHOICES
    TYPE_SUPPORT_CHOICES = TYPE_SUPPORT_CHOICES
    
    client       = models.ForeignKey(Client, on_delete=models.PROTECT, related_name='campagnes')
    nom          = models.CharField(max_length=200, verbose_name="Nom de la campagne")
    reference    = models.CharField(max_length=50, unique=True, blank=True)
    date_debut   = models.DateField(verbose_name="Date de début")
    date_fin     = models.DateField(verbose_name="Date de fin")
    statut       = models.CharField(max_length=20, choices=STATUT_CHOICES, default=STATUT_EN_COURS)
    type_support = models.CharField(max_length=10, choices=TYPE_SUPPORT_CHOICES, default='panneau', verbose_name="Type de support")
    actif        = models.BooleanField(default=True)
    
    duree_passage = models.PositiveIntegerField(choices=DUREE_CHOICES, null=True, blank=True, verbose_name="Durée de passage (secondes)")
    frequence = models.PositiveIntegerField(choices=FREQUENCE_CHOICES, default=120, null=True, blank=True, verbose_name="Fréquence de diffusion")
    tranches_horaires = models.CharField(max_length=500, default='08:00-12:00', blank=True, verbose_name="Tranches horaires de diffusion")
    
    notes        = models.TextField(blank=True)
    created_by   = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)
    contrat      = models.ForeignKey(Contrat, on_delete=models.SET_NULL, null=True, blank=True, related_name='campagnes')

    class Meta:
        verbose_name = "Campagne"
        verbose_name_plural = "Campagnes"
        ordering = ['-date_debut']

    def __str__(self):
        return f"{self.nom} — {self.client.nom}"

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = f"CAM-{timezone.now().year}-{str(uuid.uuid4())[:6].upper()}"
        super().save(*args, **kwargs)

    def get_statut_badge(self):
        badges = {
            self.STATUT_BROUILLON: 'secondary',
            self.STATUT_A_VENIR:   'info',
            self.STATUT_EN_COURS:  'success',
            self.STATUT_TERMINEE:  'dark',
            self.STATUT_ANNULEE:   'danger',
        }
        return badges.get(self.statut, 'secondary')

    def nb_supports(self):
        return self.lignes.count()
    
    def calculer_nombre_spots_ecran(self):
        total_jours = self.duree_jours()
        if total_jours == 0:
            return 0

        if self.type_support == 'panneau':
            total = 0
            for ligne in self.lignes.select_related('face__support').all():
                if not ligne.face:
                    continue
                support     = ligne.face.support
                jours_dispo = support.jours_disponibles_sur_periode(self.date_debut, self.date_fin)
                total += jours_dispo / total_jours
            return round(total, 2)

        elif self.type_support == 'ecran':
            if not self.frequence or not self.duree_passage:
                return 0

            spots_par_heure = 3600 / self.frequence
            heures_tranches = calculer_duree_tranches(self.tranches_horaires)
            spots_par_jour  = spots_par_heure * heures_tranches

            total = 0
            for ligne in self.lignes.select_related('support').all():
                support     = ligne.support
                jours_dispo = support.jours_disponibles_sur_periode(self.date_debut, self.date_fin)
                total += spots_par_jour * jours_dispo
            return round(total)
        return 0

    def diffusions_par_heure(self):
        if self.type_support == 'ecran' and self.frequence:
            return 3600 / self.frequence
        return 0
    
    def nombre_spots_jour(self):
        return self.diffusions_par_heure() * self.calculer_duree_tranches()
    
    def duree_jours(self):
        if self.date_debut and self.date_fin:
            return (self.date_fin - self.date_debut).days + 1
        return 0
    
    def calculer_duree_tranches(self):
        return calculer_duree_tranches(self.tranches_horaires)
    
    def calculer_nombre_spots(self):
        if self.type_support == 'panneau':
            total_jours = self.duree_jours()
            if total_jours == 0:
                return 0
            total = 0
            for ligne in self.lignes.select_related('face__support').all():
                if not ligne.face:
                    continue
                support = ligne.face.support
                jours_dispo = support.jours_disponibles_sur_periode(self.date_debut, self.date_fin)
                total += jours_dispo / total_jours
            return round(total, 2)

        elif self.type_support == 'ecran':
            return sum(getattr(ligne, 'calculer_spots', lambda: 0)() for ligne in self.lignes.all())
        return 0
    
    def calculer_nombre_spots24(self):
        if self.type_support == 'ecran':
            return self.calculer_nombre_spots() // max(self.duree_jours(), 1)
        return 0
    
    def calculer_nombre_spotsjourecran(self):
        if self.type_support == 'ecran' and self.lignes.count() > 0:
            return (self.calculer_nombre_spots() // max(self.duree_jours(), 1)) // self.lignes.count()
        return 0

    def auto_update_statut(self):
        today = timezone.now().date()
        if self.statut in (self.STATUT_BROUILLON, self.STATUT_ANNULEE):
            return
        if today < self.date_debut:
            self.statut = self.STATUT_A_VENIR
        elif self.date_debut <= today <= self.date_fin:
            self.statut = self.STATUT_EN_COURS
        else:
            self.statut = self.STATUT_TERMINEE
        self.save(update_fields=['statut'])

    def clean(self):
        super().clean()
        if self.type_support == 'ecran':
            if not self.duree_passage:
                raise ValidationError({'duree_passage': 'Ce champ est obligatoire pour les campagnes écran.'})
            if not self.frequence:
                raise ValidationError({'frequence': 'Ce champ est obligatoire pour les campagnes écran.'})
            if not self.tranches_horaires:
                raise ValidationError({'tranches_horaires': 'Ce champ est obligatoire pour les campagnes écran.'})


class CampagneVisuel(models.Model):
    campagne = models.ForeignKey(Campagne, related_name='visuels', on_delete=models.CASCADE)
    fichier = models.FileField(upload_to='visuels/', verbose_name="Visuel / Affiche (photo ou vidéo)")

    class Meta:
        verbose_name = "Visuel de campagne"
        verbose_name_plural = "Visuels de campagne"

    def __str__(self):
        return f"Visuel pour {self.campagne.nom}"


class LigneCampagne(models.Model):
    """Lien entre une campagne et un support avec ses surcharges spécifiques."""
    campagne = models.ForeignKey(Campagne, on_delete=models.CASCADE, related_name='lignes')
    support = models.ForeignKey('inventory.Support', on_delete=models.PROTECT, related_name='lignes_campagne')
    face = models.ForeignKey('inventory.FacePanneau', on_delete=models.SET_NULL, null=True, blank=True, related_name='lignes_campagne', verbose_name="Face (Panneau)")
    visuel = models.FileField(upload_to='visuels/', blank=True, null=True, verbose_name="Visuel / Média")
    ordre_dans_boucle = models.PositiveIntegerField(default=0, verbose_name="Priorité/Ordre")
    notes = models.TextField(blank=True, verbose_name="Notes internes")

    date_debut = models.DateField(null=True, blank=True, verbose_name="Date de début (écran)")
    date_fin = models.DateField(null=True, blank=True, verbose_name="Date de fin (écran)")
    duree_passage = models.PositiveIntegerField(choices=DUREE_CHOICES, null=True, blank=True, verbose_name="Durée de passage (secondes)")
    frequence = models.PositiveIntegerField(choices=FREQUENCE_CHOICES, null=True, blank=True, verbose_name="Fréquence de diffusion")
    tranches_horaires = models.CharField(max_length=500, blank=True, verbose_name="Tranches horaires spécifiques")

    class Meta:
        verbose_name = "Ligne de campagne"
        verbose_name_plural = "Lignes de campagne"

    def __str__(self):
        return f"{self.campagne.nom} -> {self.support.code}"