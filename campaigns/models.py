from typing import Any

from django.db import models
from django.utils import timezone
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
import datetime
from datetime import datetime

def calculer_duree_tranches(tranches: str) -> float:
    total_heures = 0.0
    if not tranches:
        return total_heures
    plages = tranches.split(",")
    for plage in plages:
        debut_str, fin_str = plage.split("-")
        debut = datetime.strptime(debut_str.strip(), "%H:%M")
        fin = datetime.strptime(fin_str.strip(), "%H:%M")
        duree = (fin - debut).total_seconds() / 3600
        total_heures += duree
    return total_heures

# On suppose que ces constantes sont définies dans votre fichier ou importées
DUREE_CHOICES = [
    (5, '5 secondes'),
    (10, '10 secondes'),
    (15, '15 secondes'),
    (20, '20 secondes'),
]
# Fréquences typiques pour les campagnes écran (en secondes)
# 
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
    """Contrat signé par un client, avec période et nombre de spots."""

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

    # la fonction du spot utilisé pourrait être ajoutée ici, en fonction des campagnes associées et de leur consommation de spots
    def spots_utilises(self):
        # On suppose que chaque campagne associée consomme un certain nombre de spots, à calculer
        spots_utilises = sum(c.calculer_nombre_spots() for c in self.campagnes.filter(statut__in=['en_cours', 'terminee']))
        return spots_utilises
    
    # la fonction pour savoir le nombre de spots restants pourrait être ajoutée ici, en fonction des campagnes associées et de leur consommation de spots
    def spots_restants(self):
        spots_utilises = self.spots_utilises()
        spots_restants = max(self.nb_spots - spots_utilises, 0)
        return spots_restants
    
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

# UNE class pour faire la reservation des Panneau A un client
class ReservationPanneau(models.Model):
    client = models.ForeignKey(
        'Client', 
        on_delete=models.CASCADE, 
        related_name='reservations'
    )
    support = models.ForeignKey(
        'inventory.Support', 
        on_delete=models.PROTECT, 
        related_name='lignes_client'
    )
    # Spécifique aux Panneaux Statiques
    face = models.ForeignKey(
        'inventory.FacePanneau', 
        on_delete=models.SET_NULL,
        null=True, blank=True, 
        related_name='lignes_client',
        verbose_name="Face (Panneau)"
    )
    date_debut = models.DateTimeField()
    date_fin = models.DateTimeField()

    def __str__(self):
        return f"Réservation de {self.face} du panneau {self.support} pour {self.client}"
    
    def clean(self):
        """Validation métier avant l'enregistrement."""
        if self.support.type_support != 'panneau':
            raise ValidationError(_("La réservation est uniquement applicable aux panneaux statiques."))
        if not self.face:
            raise ValidationError(_("Vous devez sélectionner une face pour un panneau statique."))
        if self.date_debut >= self.date_fin:
            raise ValidationError(_("La date de début doit être antérieure à la date de fin."))
        
        # Vérification de la disponibilité du panneau pour les dates données
        conflits = ReservationPanneau.objects.filter(
            support=self.support,
            face=self.face,
            date_debut__lt=self.date_fin,
            date_fin__gt=self.date_debut
        ).exclude(id=self.id)
        
        if conflits.exists():
            raise ValidationError(_("Ce panneau est déjà réservé pour les dates sélectionnées."))
    
    def save(self, *args, **kwargs):
        # Force l'exécution du clean() lors du save (hors admin)
        self.full_clean()
        super().save(*args, **kwargs)
    
    def est_reservee(self, date_debut=None, date_fin=None):
        """
        Retourne True si la face est réservée sur la période donnée.
        Sans dates → vérifie si elle est réservée maintenant.
        """
        from campaigns.models import ReservationPanneau
        from django.utils import timezone

        qs = ReservationPanneau.objects.filter(face=self)

        if date_debut and date_fin:
            # Chevauchement sur une période
            qs = qs.filter(date_debut__lt=date_fin, date_fin__gt=date_debut)
        else:
            # Réservation active en ce moment
            now = timezone.now()
            qs = qs.filter(date_debut__lte=now, date_fin__gte=now)

        return qs.exists()

class Campagne(models.Model):
    """Campagne publicitaire avec ses supports et visuels."""
    STATUT_BROUILLON = STATUT_BROUILLON
    STATUT_A_VENIR   = STATUT_A_VENIR
    STATUT_EN_COURS  = STATUT_EN_COURS
    STATUT_TERMINEE  = STATUT_TERMINEE
    STATUT_ANNULEE   = STATUT_ANNULEE
    STATUT_CHOICES = STATUT_CHOICES
    
    TYPE_SUPPORT_CHOICES = TYPE_SUPPORT_CHOICES
    
    client       = models.ForeignKey(Client, on_delete=models.PROTECT, related_name='campagnes')
    nom          = models.CharField(max_length=200, verbose_name="Nom de la campagne")
    reference    = models.CharField(max_length=50, unique=True, blank=True)
    date_debut   = models.DateField(verbose_name="Date de début")
    date_fin     = models.DateField(verbose_name="Date de fin")
    statut       = models.CharField(max_length=20, choices=STATUT_CHOICES, default=STATUT_EN_COURS)
    type_support = models.CharField(max_length=10, choices=TYPE_SUPPORT_CHOICES, default='panneau', verbose_name="Type de support")
    actif        = models.BooleanField(default=True)
    # Plusieurs visuels possibles pour une campagne, gérés via les lignes de campagne (LigneCampagne.visuel)
    # Mettre plusieurs visuels
    # visuel       = models.FileField(upload_to='visuels/', blank=True, null=True, verbose_name="Visuel / Affiche (photo ou vidéo)")
    
    # Champs spécifiques aux campagnes écran
    duree_passage = models.PositiveIntegerField(
        choices=DUREE_CHOICES, 
        null=True, blank=True, 
        verbose_name="Durée de passage (secondes)",
        help_text="Durée d'affichage du spot sur les écrans"
    )
    frequence = models.PositiveIntegerField(
        choices=FREQUENCE_CHOICES,
        default=120,
        null=True, blank=True, 
        verbose_name="Fréquence de diffusion",
        help_text="Intervalle entre deux diffusions du spot"
    )
    tranches_horaires = models.CharField(
        max_length=500, 
        default = '08:00-12:00',
        blank=True, 
        verbose_name="Tranches horaires de diffusion",
        help_text="Ex: 08:00-12:00, 14:00-18:00 (séparées par des virgules)"
    )
    
    notes        = models.TextField(blank=True)
    created_by   = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)
    # Ajouter un champ contrat dans le modèle Campagne (foreignKey vers Contrat, nullable)
    contrat = models.ForeignKey(
        'Contrat', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='campagnes',
        help_text="Contrat associé à cette diffusion sur écran"
    )

    class Meta:
        verbose_name = "Campagne"
        verbose_name_plural = "Campagnes"
        ordering = ['-date_debut']

    def __str__(self):
        return f"{self.nom} — {self.client.nom}"

    def save(self, *args, **kwargs):
        if not self.reference:
            import uuid
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
    
    # Calcul du nombre total de spots pour la campagne par ecran

    def calculer_nombre_spots_ecran(self):
        """
        Calcule le nombre total de spots pour la campagne.

        Panneau : nombre de faces
        Écran : (3600 / fréquence_en_secondes) × heures_tranches × durée_jours × nb_écrans
        """
        if self.type_support == 'panneau':
            # Pour les panneaux, on compte le nombre de faces affectées
            total = 0
            for ligne in self.lignes.select_related('face').all():
                if ligne.face:
                    total += 1
            return total

        elif self.type_support == 'ecran':
            if not self.frequence:
                return 0
            return int(self.nombre_spots_jour() * self.duree_jours())
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
            # Pour les panneaux, on compte le nombre de faces affectées
            total = 0
            for ligne in self.lignes.select_related('face').all():
                if ligne.face:
                    total += 1
            return total
        elif self.type_support == 'ecran':
            if not self.frequence:
                return 0
            return self.calculer_nombre_spots_ecran() * self.lignes.count()
        return 0
    
    def calculer_nombre_spotsjour(self):
        """
        Calcule le nombre de spots pour une journée de la campagne.
        """
        if self.type_support == 'ecran':
            return self.calculer_nombre_spots() // max(self.duree_jours(), 1)
        return 0
    
    def calculer_nombre_spotsjourecran(self):
        """
        Calcule le nombre de spots pour une journée de la campagne sur un écran.
        """
        if self.type_support == 'ecran':
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
    campagne = models.ForeignKey(
        Campagne, 
        related_name='visuels', 
        on_delete=models.CASCADE
    )
    fichier = models.FileField(
        upload_to='visuels/', 
        verbose_name="Visuel / Affiche (photo ou vidéo)"
    )
    class Meta:
        verbose_name = "Visuel de campagne"
        verbose_name_plural = "Visuels de campagne"

    def __str__(self):
        return f"Visuel pour {self.campagne.nom}"



class LigneCampagne(models.Model):
    """Lien entre une campagne et un support (Panneau ou Écran) avec ses paramètres."""

    # Liens fondamentaux
    campagne = models.ForeignKey(
        'Campagne', 
        on_delete=models.CASCADE, 
        related_name='lignes'
    )
    support = models.ForeignKey(
        'inventory.Support', 
        on_delete=models.PROTECT, 
        related_name='lignes_campagne'
    )
    # Spécifique aux Panneaux Statiques
    face = models.ForeignKey(
        'inventory.FacePanneau', 
        on_delete=models.SET_NULL,
        null=True, blank=True, 
        related_name='lignes_campagne',
        verbose_name="Face (Panneau)"
    )
    # Média et Organisation
    visuel = models.FileField(
        upload_to='visuels/', 
        blank=True, null=True,
        verbose_name="Visuel / Média"
    )
    ordre_dans_boucle = models.PositiveIntegerField(
        default=0, 
        verbose_name="Priorité/Ordre"
    )
    notes = models.TextField(blank=True, verbose_name="Notes internes")

    class Meta:
        verbose_name = "Ligne de Campagne"
        verbose_name_plural = "Lignes de Campagne"
        ordering = ['support__code']

    def __str__(self):
        type_label = "Face " + self.face.label if self.face else "Écran"
        return f"{self.campagne.reference} | {self.support.code} ({type_label})"

    def clean(self):
        duree_spot_sec = self.campagne.duree_passage if self.campagne else None
        frequence_toutes = self.campagne.frequence if self.campagne else None
        """Validation métier avant l'enregistrement."""
        # 1. Validation pour les PANNEAUX
        if self.support.type_support == 'panneau' and not self.face:
            raise ValidationError(_("Vous devez sélectionner une face pour un panneau statique."))

        # 2. Validation pour les ÉCRANS
        if self.support.type_support == 'ecran':
            if not duree_spot_sec or not frequence_toutes:
                raise ValidationError(_("La durée et la fréquence sont obligatoires pour un écran."))
            
            # Vérification de la disponibilité réelle sur l'écran
            if hasattr(self.support, 'ecran_info'):
                ecran = self.support.ecran_info
                # On convertit frequence_toutes en minutes pour correspondre à la méthode de l'écran
                disponible, message = ecran.peut_accueillir_spot(
                    duree_sec=duree_spot_sec,
                    frequence_min=(frequence_toutes / 60),
                    date_debut=self.campagne.date_debut,
                    date_fin=self.campagne.date_fin
                )
                if not disponible:
                    raise ValidationError(message)

    def save(self, *args, **kwargs):
        # Force l'exécution du clean() lors du save (hors admin)
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def taux_occupation_individuel(self):
        """Calcule la part de temps consommée par ce spot unique sur l'écran."""
        if self.support.type_support == 'ecran' and self.frequence_toutes:
            return round((self.campagne.duree_passage / self.frequence_toutes) * 100, 2)
        return 0

    @property
    def passages_estimes_par_jour(self):
        """Estime le nombre de passages quotidiens selon la plage de l'écran."""
        if self.support.type_support == 'ecran' and self.frequence_toutes:
            if hasattr(self.support, 'ecran_info'):
                sec_jour = self.support.ecran_info.secondes_totales_disponibles_jour
                return int(sec_jour // self.frequence_toutes)
        return 0
