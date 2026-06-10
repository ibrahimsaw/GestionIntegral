from django.db import models
from django.utils import timezone
from django.conf import settings
from django.core.exceptions import ValidationError
from datetime import datetime, timedelta
from datetime import datetime
from typing import Optional

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

    
# ── FORMAT — défini au niveau du Support car identique sur les deux faces ──
FORMAT_CHOICES = [
    # ── Standard ────────────────────────────────────────────
    ('4x3',    '4m × 3m (12m²) — Standard'),        # 458 supports
    ('4x5',    '4m × 5m (20m²) — Standard'),        #   4 supports
    ('7x3',    '7m × 3m (21m²) — Standard'),        #  14 supports
    # ── Géants ──────────────────────────────────────────────
    ('6x4',    '6m × 4m (24m²) — Géant'),          #  28 supports
    ('8x4',    '8m × 4m (32m²) — Géant'),          #  19 supports
    ('10x4',   '10m × 4m (40m²) — Géant'),          #  15 supports
    ('12x4',   '12m × 4m (48m²) — Géant'),          #  12 supports
    ('12x5',   '12m × 5m (60m²) — Géant'),          #   2 supports
    ('24x4',   '24m × 4m (96m²) — Géant'),          #   2 supports
    ('9x5',    '9m × 5m (45m²) — Géant'),           #   1 support
    # ── Sucettes ────────────────────────────────────────────
    ('1x2',    '1,20m × 1,80m (2,16m²) — Sucette'), #  64 supports
    # ── Grand Marché ─────────────────────────────────────────
    ('gm-4x3', '4m × 3m (12m²) — Grand Marché'),    #   8 supports
    ('gm-4x4', '4m × 4m (16m²) — Grand Marché'),    #  12 supports
    ('gm-5x4', '5m × 4m (20m²) — Grand Marché'),    #  47 supports
    ('gm-12x3','12m × 3m (36m²) — Grand Marché'),   #   1 support
    # ── Personnalisé ─────────────────────────────────────────
    ('custom', 'Personnalisé (voir notes)'),
]


TYPE_PANNEAU = 'panneau'
TYPE_ECRAN   = 'ecran'
TYPE_CHOICES = [
    (TYPE_PANNEAU, 'Panneau Statique'),
    (TYPE_ECRAN,   'Écran Numérique'),
]
ETAT_BON         = 'bon'
ETAT_MAINTENANCE = 'maintenance'
ETAT_PANNE       = 'panne'
ETAT_CHOICES = [
    (ETAT_BON,         'Bon état'),
    (ETAT_MAINTENANCE, 'En maintenance'),
    (ETAT_PANNE,       'En panne'),
]



class Support(models.Model):
    """Support publicitaire géolocalisé (Panneau ou Écran)."""
    FORMAT_CHOICES = FORMAT_CHOICES
    TYPE_PANNEAU = TYPE_PANNEAU
    TYPE_ECRAN = TYPE_ECRAN
    TYPE_CHOICES = TYPE_CHOICES
    ETAT_BON = ETAT_BON
    ETAT_MAINTENANCE = ETAT_MAINTENANCE
    ETAT_PANNE = ETAT_PANNE
    ETAT_CHOICES = ETAT_CHOICES
    
        # Identité

    # Identité
    code        = models.CharField(max_length=30, unique=True, verbose_name="Code interne")
    nom         = models.CharField(max_length=200, verbose_name="Nom / Libellé")
    type_support = models.CharField(max_length=10, choices=TYPE_CHOICES, verbose_name="Type")
    actif        = models.BooleanField(default=True)
    
    # ── Format (panneaux uniquement) ──────────────────────────────────────
    format = models.CharField(
        max_length=20,
        choices=FORMAT_CHOICES,
        blank=True, default='',
        verbose_name="Format & Type",
        help_text="Renseigner uniquement pour les panneaux. Commun aux deux faces.",
    )

    # Géolocalisation
    latitude    = models.DecimalField(max_digits=10, decimal_places=7)
    longitude   = models.DecimalField(max_digits=10, decimal_places=7)
    adresse     = models.CharField(max_length=300, blank=True)
    ville       = models.CharField(max_length=100, blank=True, default='Ouagadougou')
    quartier    = models.CharField(max_length=100, blank=True)

    # État opérationnel
    etat        = models.CharField(max_length=20, choices=ETAT_CHOICES, default=ETAT_BON)
    date_installation = models.DateField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)
    notes       = models.TextField(blank=True)

    # Photos
    photo_principale = models.ImageField(upload_to='photos/', blank=True, null=True, verbose_name="Photo principale")

    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)
    created_by  = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='supports_created'
    )

    class Meta:
        verbose_name = "Support Publicitaire"
        verbose_name_plural = "Supports Publicitaires"
        ordering = ['code']
    
    def __str__(self):
        if self.type_support == self.TYPE_PANNEAU and self.format:
            return f"{self.code} — {self.nom} ({self.get_format_display()})"
        return f"{self.code} — {self.nom}"
    
    def clean(self):
        """Le format est obligatoire pour les panneaux, interdit pour les écrans."""
        from django.core.exceptions import ValidationError
        if self.type_support == self.TYPE_PANNEAU and not self.format:
            raise ValidationError({'format': 'Le format est obligatoire pour un panneau.'})
        if self.type_support == self.TYPE_ECRAN and self.format:
            raise ValidationError({'format': 'Le format ne s\'applique pas aux écrans numériques.'})

    def save(self, *args, **kwargs):
        if not self.code:
            prefix = 'PAN' if self.type_support == self.TYPE_PANNEAU else 'ECR'
            last_code = Support.objects.filter(type_support=self.type_support).aggregate(
                max_id=models.Max('id')
            )['max_id'] or 0
            self.code = f"{prefix}-{last_code + 1:03d}"
        super().save(*args, **kwargs)

    def get_etat_color(self):
        cfg = settings.DESIGN_CONFIG
        if self.type_support == self.TYPE_PANNEAU:
            # Panneau en panne SEULEMENT si TOUTES les faces sont en panne
            all_faces = self.faces.all()
            if all_faces.exists() and all_faces.filter(etat=self.ETAT_PANNE).count() == all_faces.count():
                return cfg['COLOR_MAINTENANCE']
        elif self.etat in [self.ETAT_PANNE, self.ETAT_MAINTENANCE]:
            return cfg['COLOR_MAINTENANCE']

        # Vérifier si au moins une face/slot est occupé(e)
        if self.is_occupe():
            return cfg['COLOR_OCCUPE']
        return cfg['COLOR_DISPONIBLE']

    def is_occupe(self):
        """Retourne True si une campagne active couvre ce support aujourd'hui."""
        from campaigns.models import LigneCampagne
        today = timezone.now().date()
        return LigneCampagne.objects.filter(
            support=self,
            campagne__date_debut__lte=today,
            campagne__date_fin__gte=today,
            campagne__statut__in=['en_cours', 'a_venir'],
        ).exists()

    def disponibilite_json(self):
        """Retourne un dict résumant la disponibilité pour le popup carte."""
        result = {
            'id': self.pk,
            'code': self.code,
            'nom': self.nom,
            'type': self.type_support,
            'etat': self.etat,
            'adresse': self.adresse,
            'color': self.get_etat_color(),
            'type_panneau': self.type_panneau if self.type_support == self.TYPE_PANNEAU else None,
        }
        if self.type_support == self.TYPE_PANNEAU:
            faces = []
            for face in self.faces.all():
                faces.append({'id': face.pk, 'label': face.get_label_display(), 'dispo': face.is_disponible(), 'disponibles': face.is_disponibles()})
            result['faces'] = faces
        return result

    def intervalles_panne_bon(self):
        """Retourne les périodes panne → bon pour ce support."""
        return Maintenance.intervalles_panne_bon_for_support(self)

    def duree_totale_panne_bon(self):
        """Retourne la durée totale cumulée des périodes panne → bon pour ce support."""
        return Maintenance.duree_totale_panne_bon_for_support(self)

        # ── Propriétés format ─────────────────────────────────────────────────
    @property
    def format_detail(self) -> dict:
        """
        Décompose le format en dimensions, surface et type.
 
        Exemple :
            support.format_detail
            → {'dimensions': '4m × 3m', 'surface': '12m²', 'type': 'Standard'}
        """
        label  = self.get_format_display()
        result = {'dimensions': label, 'surface': '', 'type': ''}
 
        if ' — ' in label:
            left, result['type'] = label.split(' — ', 1)
        else:
            left = label
 
        if '(' in left and ')' in left:
            result['dimensions'] = left[:left.index('(')].strip()
            result['surface']    = left[left.index('(')+1 : left.index(')')].strip()
        else:
            result['dimensions'] = left.strip()
 
        return result
    
    @property
    def type_panneau(self) -> str:
        """'Standard', 'Géant', 'Sucette', 'Grand Marché', etc."""
        return self.format_detail['type']
 
    @property
    def surface_m2(self) -> str:
        """'12m²', '48m²', etc."""
        return self.format_detail['surface']
 
    @property
    def dimensions(self) -> str:
        """'4m × 3m', '12m × 4m', etc."""
        return self.format_detail['dimensions']
    
    # Sur le modèle Support (ou FacePanneau selon ton archi)
    def is_reserve(self):
        """True si au moins une face a une réservation active ou à venir."""
        from django.utils import timezone
        now = timezone.now()
        return self.lignes_client.filter(date_fin__gte=now).exists()
    
    def is_libre(self):
        """True si au moins une face n'est pas occupée ni réservée."""
        return any(face.is_disponible() for face in self.faces.all())

    # comment de la avoir le fonction de taux_occupation_pourcentage DE LE EcranNumerique dans le Support pour les écrans numériques ?
    # on peut faire une méthode dans le Support qui délègue au EcranNumerique si le type est 'ecran' et retourne None ou 0 pour les panneaux. Par exemple :
    def taux_occupation_pourcentage(self, date_test=None):
        """Retourne le pourcentage d'occupation (0 à 100) pour les écrans et les panneaux."""
        if self.type_support == self.TYPE_ECRAN and hasattr(self, 'ecran_info'):
            return self.ecran_info.taux_occupation_pourcentage(date_test)
        if self.type_support == self.TYPE_PANNEAU: 
            # On récupère toutes les faces liées à ce support
            toutes_les_faces = self.faces.all()
            total_faces = toutes_les_faces.count()
            if total_faces == 0:
                return 0
            # On compte le nombre de faces qui ne sont PAS disponibles
            faces_occupees = sum(1 for face in toutes_les_faces if not face.is_disponible(date_test, date_test))
            pourcentage = (faces_occupees / total_faces) * 100
            return round(pourcentage, 2)
        return None
    def taux_occupation_pourcentages(self, date_test=None):
        """Retourne le pourcentage d'occupation (0 à 100) pour les écrans et les panneaux."""
        if self.type_support == self.TYPE_ECRAN and hasattr(self, 'ecran_info'):
            return self.ecran_info.taux_occupation_pourcentage(date_test)
        if self.type_support == self.TYPE_PANNEAU: 
            # On récupère toutes les faces liées à ce support
            toutes_les_faces = self.faces.all()
            total_faces = toutes_les_faces.count()
            if total_faces == 0:
                return 0
            # On compte le nombre de faces qui ne sont PAS disponibles
            faces_occupees = sum(1 for face in toutes_les_faces if not face.is_disponibles(date_test, date_test))
            pourcentage = (faces_occupees / total_faces) * 100
            return round(pourcentage, 2)
        return None
    
    def get_periodes_panne(self):
        """
        Retourne la liste des périodes de panne pour ce support.
        Une période = intervalle entre un état 'panne' et le prochain état 'bon'.
        
        Pour un panneau : analyse les maintenances par face.
        Pour un écran   : analyse les maintenances du support directement.
        
        Retourne une liste de dicts :
        [
            {
                'debut'   : datetime,   # date de la mise en panne
                'fin'     : datetime,   # date du retour en bon état (None si toujours en panne)
                'duree'   : timedelta,  # durée de la panne (None si toujours en panne)
                'face'    : FacePanneau | None,
                'resolue' : bool,
            },
            ...
        ]
        """
        periodes = []

        if self.type_support == self.TYPE_PANNEAU:
            # ── Analyse par face ──────────────────────────────────────
            for face in self.faces.all():
                maints = (
                    Maintenance.objects
                    .filter(face=face)
                    .order_by('date_intervention')
                )
                panne_debut = None

                for maint in maints:
                    if maint.etat_apres == ETAT_PANNE and panne_debut is None:
                        # Début d'une période de panne
                        panne_debut = maint.date_intervention

                    elif maint.etat_apres == ETAT_BON and panne_debut is not None:
                        # Fin de la période de panne
                        periodes.append({
                            'debut'   : panne_debut,
                            'fin'     : maint.date_intervention,
                            'duree'   : maint.date_intervention - panne_debut,
                            'face'    : face,
                            'resolue' : True,
                        })
                        panne_debut = None

                # Panne toujours ouverte (pas encore résolue)
                if panne_debut is not None:
                    periodes.append({
                        'debut'   : panne_debut,
                        'fin'     : None,
                        'duree'   : timezone.now() - panne_debut,
                        'face'    : face,
                        'resolue' : False,
                    })

        else:
            # ── Analyse directe sur le support (écran) ────────────────
            maints = (
                Maintenance.objects
                .filter(support=self, face__isnull=True)
                .order_by('date_intervention')
            )
            panne_debut = None

            for maint in maints:
                if maint.etat_apres == ETAT_PANNE and panne_debut is None:
                    panne_debut = maint.date_intervention

                elif maint.etat_apres == ETAT_BON and panne_debut is not None:
                    periodes.append({
                        'debut'   : panne_debut,
                        'fin'     : maint.date_intervention,
                        'duree'   : maint.date_intervention - panne_debut,
                        'face'    : None,
                        'resolue' : True,
                    })
                    panne_debut = None

            if panne_debut is not None:
                periodes.append({
                    'debut'   : panne_debut,
                    'fin'     : None,
                    'duree'   : timezone.now() - panne_debut,
                    'face'    : None,
                    'resolue' : False,
                })

        # Tri chronologique
        return sorted(periodes, key=lambda p: p['debut'])

class FacePanneau(models.Model):
    """Face A ou B d'un panneau statique."""

    LABEL_A = 'A'
    LABEL_B = 'B'
    LABEL_CHOICES = [('A', 'Face A'), ('B', 'Face B')]


    ECLAIRAGE_CHOICES = [
        ('non',      'Non éclairé'),
        ('backlit',  'Rétroéclairé'),
        ('frontlit', 'Éclairage avant'),
        ('led',      'LED'),
    ]

    support  = models.ForeignKey(Support, on_delete=models.CASCADE, related_name='faces')
    label    = models.CharField(max_length=1, choices=LABEL_CHOICES, default=LABEL_A)

    eclairage = models.CharField(max_length=10, choices=ECLAIRAGE_CHOICES, default='non')
    etat      = models.CharField(max_length=20, choices=ETAT_CHOICES, default=ETAT_BON, verbose_name="État de la face")
    notes    = models.TextField(blank=True)

    class Meta:
        unique_together = [('support', 'label')]
        verbose_name = "Face de Panneau"
        verbose_name_plural = "Faces de Panneau"
        ordering = ['label']

    def __str__(self):
        return f"{self.support.code} — Face {self.label}"

    def is_disponible(self, date_debut=None, date_fin=None):
        """Vérifie si la face est libre sur une période donnée."""
        from campaigns.models import LigneCampagne
        from django.utils import timezone
        if date_debut is None:
            date_debut = timezone.now().date()
        if date_fin is None:
            date_fin = date_debut
        return not LigneCampagne.objects.filter(
            face=self,
            campagne__date_debut__lte=date_fin,
            campagne__date_fin__gte=date_debut,
            campagne__statut__in=['en_cours', 'a_venir'],
        ).exists()
        
    def is_disponibles(self, date_debut=None, date_fin=None):
        """Vérifie si la face est libre sur une période donnée (campagnes + réservations)."""
        from campaigns.models import LigneCampagne, ReservationPanneau
        from django.utils import timezone

        if date_debut is None:
            date_debut = timezone.now()
        if date_fin is None:
            date_fin = date_debut

        # 1. Vérification via les LigneCampagne (campagnes actives/à venir)
        occupee_campagne = LigneCampagne.objects.filter(
            face=self,
            campagne__date_debut__lte=date_fin,
            campagne__date_fin__gte=date_debut,
            campagne__statut__in=['en_cours', 'a_venir'],
        ).exists()

        # 2. Vérification via les ReservationPanneau (réservations directes)
        occupee_reservation = ReservationPanneau.objects.filter(
            face=self,
            date_debut__lt=date_fin,
            date_fin__gt=date_debut,
        ).exists()

        return not occupee_campagne and not occupee_reservation

    def get_campagne_active(self):
        from campaigns.models import LigneCampagne
        today = timezone.now().date()
        lc = LigneCampagne.objects.filter(
            face=self,
            campagne__date_debut__lte=today,
            campagne__date_fin__gte=today,
        ).select_related('campagne__client').first()
        return lc.campagne if lc else None
    
    def get_statut(self, date_debut=None, date_fin=None, client=None):
        """
        Retourne le statut consolidé de la face :
        - 'panne'   : la face est physiquement en panne
        - 'occupe'  : une campagne est en cours sur cette période
        - 'reserve' : une réservation existe mais pas de campagne
        - 'libre'   : ni campagne ni réservation
        """
        from campaigns.models import LigneCampagne, ReservationPanneau
        from django.utils import timezone

        # État physique en priorité absolue
        if self.etat == ETAT_PANNE:
            return 'panne'

        if date_debut is None:
            date_debut = timezone.now()
        if date_fin is None:
            date_fin = date_debut

        # Occupé = campagne active/à venir sur la période
        occupee = LigneCampagne.objects.filter(
            face=self,
            campagne__date_debut__lte=date_fin,
            campagne__date_fin__gte=date_debut,
            campagne__statut__in=['en_cours', 'a_venir'],
        ).exists()

        if occupee:
            return 'occupe'

        # Réservé = réservation directe sur la période
        qs_reserve = ReservationPanneau.objects.filter(
            face=self,
            date_debut__lt=date_fin,
            date_fin__gt=date_debut,
        )

        if qs_reserve.exists():
            if client and qs_reserve.filter(client=client).exists():
                return 'libre'
            return 'reserve'

        return 'libre'


class EcranNumerique(models.Model):
    """Métadonnées et logique de diffusion pour un écran numérique."""

    TYPE_CHOICES = [
        ('interieur', 'Intérieur'),
        ('exterieur', 'Extérieur'),
        ('mobilier', 'Mobilier Urbain'),
    ]

    RESOLUTION_CHOICES = [
        ('hd',     'HD 1280×720'),
        ('fullhd', 'Full HD 1920×1080'),
        ('4k',     '4K 3840×2160'),
    ]
    CELLULE = [
        ('8x5', '40'), # Nations Unis
        ('6x4', '24'), # ASECNA , Moro Naaba, Thomas Sankara,
        ('5x4', '20'), # Gounghin, Babassy
        ('4x3', '12'), # Melkys, Larle, Bonheur Ville, RNB
        
    ]

    # Relation directe avec le Support
    support = models.OneToOneField(
        'Support', 
        on_delete=models.CASCADE, 
        related_name='ecran_info'
    )

    # Caractéristiques techniques
    type_ecran = models.CharField(
        max_length=20, 
        choices=TYPE_CHOICES, 
        default='exterieur', 
        verbose_name="Type d'affichage"
    )
    resolution = models.CharField(
        max_length=10, 
        choices=RESOLUTION_CHOICES, 
        default='fullhd'
    )
    cellule = models.CharField(
        max_length=10,
        choices=CELLULE,
        default='6x4',
        help_text="Nombre de spots simultanés (ex: 6x4 = 24)"
    )

    # Plage d'activation de l'écran (ex: 06:00 à 23:00)
    heure_allumage = models.TimeField(
        default="06:00:00", 
        help_text="Heure à laquelle l'écran s'allume"
    )
    heure_extinction = models.TimeField(
        default="23:00:00", 
        help_text="Heure à laquelle l'écran s'éteint"
    )

    class Meta:
        verbose_name = "Configuration Écran"
        verbose_name_plural = "Configurations Écrans"

    def __str__(self):
        return f"Config Technique - {self.support.code}"

    @property
    def secondes_totales_disponibles_jour(self):
        """Calcule le volume total de secondes de fonctionnement par jour."""
        today = datetime.today()
        start = datetime.combine(today, self.heure_allumage)
        end = datetime.combine(today, self.heure_extinction)
        
        # Gestion du cas où l'écran s'éteint après minuit
        if end <= start:
            end += timedelta(days=1)
            
        return (end - start).total_seconds()
        
    def calculer_occupation_reelle(self, date_test=None):
        """
        Calcule la somme des secondes déjà réservées par les campagnes 
        pour une journée spécifique.
        """
        from campaigns.models import LigneCampagne
        from django.utils import timezone
        
        target_date = date_test or timezone.now().date()
        
        # On récupère toutes les lignes de campagnes actives sur cet écran pour ce jour
        lignes = LigneCampagne.objects.filter(
            support=self.support,
            campagne__date_debut__lte=target_date,
            campagne__date_fin__gte=target_date,
            campagne__statut__in=['en_cours', 'a_venir']
            
        )
        # print(f"Calcul occupation pour {self.support.code} le {target_date} : {lignes.count()} lignes actives")
        total_occupe_sec = 0
        for ligne in lignes:
            # Calcul : (Durée du fonctionnement de l'écran en secondes / Fréquence en sec) * durée du spot
            # Note : On limite à la plage horaire du spot si elle est plus courte que celle de l'écran
            # pour calacule en prend ((3600 / pas la frequence) * pas duree de passage ) * (durée tranches horaires / 24h)
            frequence_sec = ligne.campagne.frequence or 0
            # print(f"  - Ligne {ligne.pk} : durée spot {ligne.campagne.duree_passage}s, fréquence toutes {ligne.campagne.frequence}s , Tranches horaires : {ligne.campagne.tranches_horaires} , Durée tranches : {calculer_duree_tranches(ligne.campagne.tranches_horaires)}h")
            # print("    → Seconde totales disponibles par jour : ", self.secondes_totales_disponibles_jour)
            # print("    → Impact en secondes : ", (3600/ frequence_sec) * ligne.campagne.duree_passage * (calculer_duree_tranches(ligne.campagne.tranches_horaires)) if frequence_sec > 0 else "N/A")
            
            if frequence_sec > 0:
                # pour calacule en prend ((3600 / pas la frequence) * pas duree de passage ) * (durée tranches horaires / 24h)
                # temps de  diffusion de  chanque ligne de campagne = (3600 / frequence_sec) * ligne.campagne.duree_passage * (calculer_duree_tranches(ligne.campagne.tranches_horaires))
                temps_diffusion_sec = (3600 / frequence_sec) * ligne.campagne.duree_passage * (calculer_duree_tranches(ligne.campagne.tranches_horaires))
                # print("    → Temps de diffusion en secondes : ", temps_diffusion_sec)
                total_occupe_sec += temps_diffusion_sec
        
        return total_occupe_sec

    def taux_occupation_pourcentage(self, date_test=None):
        """Retourne le pourcentage d'occupation (0 à 100)."""
        total = self.secondes_totales_disponibles_jour
        if total <= 0: return 0
        
        occupe = self.calculer_occupation_reelle(date_test)
        pourcentage = (occupe / total) * 100
        return round(min(pourcentage, 100), 2)

    def peut_accueillir_spot(self, duree_sec, frequence_min, date_debut, date_fin):
        """
        Vérification rapide : Est-ce qu'il reste assez de secondes 
        dans la journée pour ce nouveau spot ?
        """
        # 1. Calcul de l'impact du nouveau spot
        frequence_sec = frequence_min * 60
        nb_diffusions = self.secondes_totales_disponibles_jour / frequence_sec
        impact_nouveau_spot = nb_diffusions * duree_sec

        # 2. Vérification sur la période demandée
        # (Pour simplifier, on vérifie si le taux d'occupation dépasse 100%)
        # Dans un système réel, on bouclerait sur chaque jour entre date_debut et date_fin
        occupe = self.calculer_occupation_reelle(date_debut)
        
        if (occupe + impact_nouveau_spot) > self.secondes_totales_disponibles_jour:
            return False, "L'écran est saturé pour cette période."
        
        return True, "Disponible"



def maintenance_photo_upload_path(instance, filename):
    import os
    from django.utils import timezone
    ext = os.path.splitext(filename)[1].lower()
    now = timezone.now()
    return f"proofs/{instance.support.code}/{now:%Y/%m/%d}/{now:%H%M%S}{ext}"


class Maintenance(models.Model):
    """Intervention de maintenance effectuée par un technicien sur un support."""
    ETAT_CHOICES = ETAT_CHOICES
    ETAT_BON = ETAT_BON
    ETAT_PANNE = ETAT_PANNE

    support           = models.ForeignKey(
        Support, on_delete=models.CASCADE,
        related_name='maintenances',
        verbose_name="Support"
    )
    face              = models.ForeignKey(
        'FacePanneau', on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='maintenances_par_face',
        verbose_name="Face (Panneau)"
    )
    effectue_par      = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='maintenances',
        verbose_name="Technicien"
    )
    date_intervention = models.DateTimeField(
        default=timezone.now,
        verbose_name="Date d'intervention"
    )
    etat_apres        = models.CharField(
        max_length=20, choices=ETAT_CHOICES,
        verbose_name="État enregistré"
    )
    description       = models.TextField(
        blank=True,
        verbose_name="Description"
    )
    photo             = models.ImageField(
        upload_to=maintenance_photo_upload_path,
        blank=True, null=True,
        verbose_name="Photo"
    )

    class Meta:
        ordering            = ['-date_intervention']
        verbose_name        = "Maintenance"
        verbose_name_plural = "Maintenances"

    def __str__(self):
        if self.face:
            return f"Maintenance {self.support.code} {self.face.label} — {self.date_intervention:%d/%m/%Y}"
        return f"Maintenance {self.support.code} — {self.date_intervention:%d/%m/%Y}"
    
    def clean(self):
        from django.core.exceptions import ValidationError

        if self.face:
            # Si support_id non encore assigné, on le déduit depuis la face
            if not self.support_id:
                self.support_id = self.face.support_id

            if self.support_id and self.support.type_support != Support.TYPE_PANNEAU:
                raise ValidationError({'face': "La face ne peut être utilisée que pour un panneau statique."})

            if self.face.support_id != self.support_id:
                raise ValidationError({'face': "La face doit appartenir au support sélectionné."})

        if (
            not self.face
            and self.support_id
            and self.support.type_support == Support.TYPE_ECRAN
            and self.etat_apres not in (self.ETAT_BON, self.ETAT_PANNE)
        ):
            pass

    # un fonction pour connaitre le temps écoulé depuis la dernière maintenance pour un support donné
    @property
    def temps_depuis_derniere_maintenance(self):
        last_maintenance = self.support.maintenances.order_by('-date_intervention').first()
        if not last_maintenance:
            return None  # ou timedelta.max pour indiquer une très longue durée
        return timezone.now() - last_maintenance.date_intervention
    
    def save(self, *args, **kwargs):
        target_obj = self.face if self.face else self.support
        old_etat = target_obj.etat

        super().save(*args, **kwargs)

        if self.etat_apres:
            if self.face:
                # 1. Mettre à jour l'état de la face
                FacePanneau.objects.filter(pk=self.face_id).update(etat=self.etat_apres)
                self.face.etat = self.etat_apres

                # 2. Recalculer l'état du support parent (panneau statique)
                toutes_les_faces = self.support.faces.all()
                if toutes_les_faces.exists():
                    toutes_en_panne = all(
                        f.etat == Support.ETAT_PANNE
                        for f in toutes_les_faces
                    )
                    nouvel_etat_support = Support.ETAT_PANNE if toutes_en_panne else Support.ETAT_BON
                else:
                    nouvel_etat_support = self.etat_apres  # fallback si pas de faces

                Support.objects.filter(pk=self.support_id).update(etat=nouvel_etat_support)
                self.support.etat = nouvel_etat_support

            else:
                # Écran numérique → mise à jour directe
                Support.objects.filter(pk=self.support_id).update(etat=self.etat_apres)
                self.support.etat = self.etat_apres

            # Log uniquement si l'état du support a changé
            if old_etat != self.support.etat:
                from accounts.audit import AuditLog
                AuditLog.objects.create(
                    user        = self.effectue_par,
                    action      = AuditLog.ACTION_UPDATE,
                    module      = AuditLog.MODULE_INVENTORY,
                    object_id   = self.support_id,
                    object_repr = str(self.support),
                    detail      = (
                        f"Maintenance effectuée : {self.description}. "
                        f"Changement état : {old_etat} → {self.support.etat}"
                    )
                )
    @classmethod
    def get_last_for_support(cls, support) -> Optional['Maintenance']:
        """Retourne la dernière maintenance enregistrée pour un support donné."""
        return cls.objects.filter(support=support).order_by('-date_intervention').first()

    @classmethod
    def days_since_last(cls, support) -> Optional[int]:
        """Nombre de jours écoulés depuis la dernière maintenance, ou None si aucune."""
        last = cls.get_last_for_support(support)
        if not last:
            return None
        delta = timezone.now() - last.date_intervention
        return delta.days

    @classmethod
    def intervalles_panne_bon_for_support(cls, support):
        """Retourne les intervalles de temps entre chaque panne et le premier bon qui suit."""
        maints = cls.objects.filter(support=support).order_by('date_intervention')
        intervals = []
        panne_record = None

        for maint in maints:
            if maint.etat_apres == cls.ETAT_PANNE:
                panne_record = maint
                continue

            if panne_record is None:
                # On ignore les bons qui ne suivent pas une panne
                continue

            if maint.etat_apres == cls.ETAT_BON:
                intervals.append({
                    'panne': panne_record,
                    'bon': maint,
                    'duree': maint.date_intervention - panne_record.date_intervention,
                })
                panne_record = None

        return intervals

    @classmethod
    def duree_totale_panne_bon_for_support(cls, support):
        """Retourne la durée totale cumulée des périodes panne→bon pour un support."""
        intervals = cls.intervalles_panne_bon_for_support(support)
        total = timedelta()
        for interval in intervals:
            total += interval['duree']
        return total

    def mark_resolved(self, effectue_par=None, etat_apres=None, description=None, photo=None):
        """Marque l'intervention comme réalisée: met à jour champs et sauvegarde.

        Retourne l'instance mise à jour.
        """
        if effectue_par is not None:
            self.effectue_par = effectue_par
        if etat_apres is not None:
            self.etat_apres = etat_apres
        if description:
            self.description = (self.description or '') + ('\n' + description if self.description else description)
        if photo is not None:
            self.photo = photo

        self.date_intervention = timezone.now()
        self.save()
        return self

    def to_dict(self) -> dict:
        """Sérialisation légère pour API/popups."""
        data = {
            'id': self.pk,
            'support_id': self.support_id,
            'support_code': getattr(self.support, 'code', None),
            'date_intervention': self.date_intervention.isoformat() if self.date_intervention else None,
            'etat_apres': self.etat_apres,
            'description': self.description,
            'effectue_par_id': getattr(self.effectue_par, 'pk', None),
            'photo_url': self.photo.url if self.photo else None,
        }
        if self.face:
            data.update({
                'face_id': self.face_id,
                'face_label': self.face.label,
            })
        return data

    def is_overdue(self, threshold_days: int = 180) -> bool:
        """Indique si le support est en retard de maintenance par rapport au seuil (jours)."""
        last = type(self).get_last_for_support(self.support)
        if not last:
            return True
        delta = timezone.now() - last.date_intervention
        return delta.days > threshold_days
    
    def delete(self, *args, **kwargs):
        """
        Après suppression, recalcule l'état de la face (ou du support)
        à partir de la maintenance précédente.
        """
        face    = self.face
        support = self.support

        super().delete(*args, **kwargs)  # suppression réelle

        if face:
            face.recalculate_etat()

            # Recalcule aussi l'état du support parent
            toutes_les_faces = support.faces.all()
            if toutes_les_faces.exists():
                toutes_en_panne = all(f.etat == ETAT_PANNE for f in toutes_les_faces)
                nouvel_etat_support = ETAT_PANNE if toutes_en_panne else ETAT_BON
            else:
                nouvel_etat_support = ETAT_BON

            Support.objects.filter(pk=support.pk).update(etat=nouvel_etat_support)
        else:
            # Écran numérique : recalcule depuis la dernière maintenance du support
            derniere = (
                Maintenance.objects.filter(support=support)
                    .order_by('-date_intervention')
                    .values_list('etat_apres', flat=True)
                    .first()
            )
            nouvel_etat = derniere if derniere else ETAT_BON
            Support.objects.filter(pk=support.pk).update(etat=nouvel_etat)




