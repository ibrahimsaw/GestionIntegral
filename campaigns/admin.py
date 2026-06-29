from django.contrib import admin
from .models import (
    Client,
    Contrat,
    Campagne,
    LigneCampagne,
    CampagneVisuel,
    Reservation,
    ReservationLigne,
)


class CampagneVisuelInline(admin.TabularInline):
    model = CampagneVisuel
    extra = 3
    verbose_name = "Visuel supplémentaire"
    verbose_name_plural = "Visuels de la campagne"


class ReservationLigneInline(admin.TabularInline):
    model = ReservationLigne
    extra = 1
    verbose_name = "Ligne de réservation"
    verbose_name_plural = "Lignes de réservation"
    raw_id_fields = ['support', 'face']


class LigneCampagneInline(admin.TabularInline):
    model = LigneCampagne
    extra = 1
    verbose_name = "Ligne de campagne"
    verbose_name_plural = "Lignes de campagne"
    fields = [
        'support', 'face', 'visuel', 'ordre_dans_boucle',
        # Champs spécifiques par écran
        'date_debut', 'date_fin',
        'duree_passage', 'frequence', 'tranches_horaires',
        'notes',
    ]
    readonly_fields = []


@admin.register(LigneCampagne)
class LigneCampagneAdmin(admin.ModelAdmin):
    list_display = [
        'campagne', 'support', 'face',
        'get_date_debut', 'get_date_fin',
        'get_duree_passage', 'get_frequence', 'get_tranches_horaires',
        'ordre_dans_boucle',
    ]
    list_filter = ['campagne__type_support', 'support__type_support']
    search_fields = ['campagne__nom', 'campagne__reference', 'support__code']

    fieldsets = (
        ("Campagne & Support", {
            'fields': ('campagne', 'support', 'face', 'visuel', 'ordre_dans_boucle', 'notes')
        }),
        ("Paramètres spécifiques à cet écran (optionnel — hérite de la campagne si vide)", {
            'classes': ('collapse',),
            'fields': ('date_debut', 'date_fin', 'duree_passage', 'frequence', 'tranches_horaires'),
        }),
    )

    # Colonnes affichant la valeur effective (ligne ou campagne)
    @admin.display(description="Date début effective")
    def get_date_debut(self, obj):
        val = obj.get_date_debut
        return f"{val} {'(ligne)' if obj.date_debut else '(campagne)'}"

    @admin.display(description="Date fin effective")
    def get_date_fin(self, obj):
        val = obj.get_date_fin
        return f"{val} {'(ligne)' if obj.date_fin else '(campagne)'}"

    @admin.display(description="Durée effective")
    def get_duree_passage(self, obj):
        val = obj.get_duree_passage
        return f"{val}s {'(ligne)' if obj.duree_passage else '(campagne)'}" if val else "-"

    @admin.display(description="Fréquence effective")
    def get_frequence(self, obj):
        val = obj.get_frequence
        return f"{val}s {'(ligne)' if obj.frequence else '(campagne)'}" if val else "-"

    @admin.display(description="Tranches effectives")
    def get_tranches_horaires(self, obj):
        val = obj.get_tranches_horaires
        return f"{val} {'(ligne)' if obj.tranches_horaires else '(campagne)'}" if val else "-"


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ['nom', 'contact_nom', 'telephone', 'actif']
    search_fields = ['nom']


@admin.register(Contrat)
class ContratAdmin(admin.ModelAdmin):
    list_display = ['client', 'nom', 'type_contrat', 'date_debut', 'date_fin', 'nb_spots', 'actif']
    list_filter = ['type_contrat', 'actif']
    search_fields = ['client__nom']


@admin.register(Campagne)
class CampagneAdmin(admin.ModelAdmin):
    list_display = ['reference', 'nom', 'client', 'date_debut', 'date_fin', 'statut', 'type_support', 'contrat', 'actif']
    list_filter = ['statut', 'type_support']
    search_fields = ['nom', 'reference', 'client__nom']
    inlines = [CampagneVisuelInline, LigneCampagneInline]


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ['reference', 'nom', 'client', 'statut', 'date_debut', 'date_fin', 'created_by']
    list_filter = ['statut', 'client']
    search_fields = ['reference', 'nom', 'client__nom']
    date_hierarchy = 'date_debut'
    inlines = [ReservationLigneInline]


@admin.register(ReservationLigne)
class ReservationLigneAdmin(admin.ModelAdmin):
    list_display = ['reservation', 'support', 'face', 'reservation_date_debut', 'reservation_date_fin']
    list_filter = ['reservation__statut', 'support__type_support']
    search_fields = ['reservation__reference', 'support__code', 'face__label']
    raw_id_fields = ['support', 'face']

    @admin.display(description='Date début')
    def reservation_date_debut(self, obj):
        return obj.reservation.date_debut

    @admin.display(description='Date fin')
    def reservation_date_fin(self, obj):
        return obj.reservation.date_fin



from django.contrib import admin
from django.utils.html import format_html
from django.utils import timezone
from .models import DemandeReservation


class FacesSouhaitiesInline(admin.TabularInline):
    model = DemandeReservation.faces_souhaitees.through
    extra = 0
    verbose_name = "Face souhaitée"
    verbose_name_plural = "Faces souhaitées"


class SupportsSouhaitesInline(admin.TabularInline):
    model = DemandeReservation.supports_souhaites.through
    extra = 0
    verbose_name = "Support souhaité (écran)"
    verbose_name_plural = "Supports souhaités (écrans)"


@admin.register(DemandeReservation)
class DemandeReservationAdmin(admin.ModelAdmin):

    # ── Liste ──────────────────────────────────────────────────────────
    list_display  = [
        'reference', 'nom_contact', 'societe', 'email', 'telephone',
        'date_debut_souhaitee', 'date_fin_souhaitee',
        'nb_emplacements_display', 'statut_badge', 'created_at',
    ]
    list_filter   = ['statut', 'created_at', 'accepte_contact']
    search_fields = ['reference', 'nom_contact', 'societe', 'email', 'telephone']
    ordering      = ['-created_at']
    date_hierarchy = 'created_at'

    # ── Détail ─────────────────────────────────────────────────────────
    readonly_fields = [
        'uuid', 'reference', 'created_at', 'updated_at',
        'traite_par', 'traite_le', 'duree_jours_display',
        'emplacements_display',
    ]

    fieldsets = [
        ('Identité', {
            'fields': ['uuid', 'reference', 'statut', 'created_at', 'updated_at'],
        }),
        ('Contact', {
            'fields': ['nom_contact', 'societe', 'email', 'telephone', 'accepte_contact'],
        }),
        ('Projet', {
            'fields': [
                'nom_campagne', 'date_debut_souhaitee', 'date_fin_souhaitee',
                'duree_jours_display', 'message',
            ],
        }),
        ('Emplacements demandés', {
            'fields': ['emplacements_display'],
        }),
        ('Traitement staff', {
            'fields': ['notes_staff', 'traite_par', 'traite_le'],
            'classes': ['collapse'],
        }),
        ('Associations créées', {
            'fields': ['client', 'reservation'],
            'classes': ['collapse'],
        }),
    ]

    inlines = [FacesSouhaitiesInline, SupportsSouhaitesInline]

    # ── Actions ────────────────────────────────────────────────────────
    actions = ['marquer_en_cours', 'marquer_refusee']

    @admin.action(description='Passer en cours de traitement')
    def marquer_en_cours(self, request, queryset):
        updated = 0
        for demande in queryset.filter(
            statut__in=[DemandeReservation.STATUT_NOUVELLE, DemandeReservation.STATUT_EN_COURS]
        ):
            demande.marquer_en_cours(user=request.user)
            updated += 1
        self.message_user(request, f"{updated} demande(s) passée(s) en cours.")

    @admin.action(description='Marquer comme refusée')
    def marquer_refusee(self, request, queryset):
        updated = 0
        for demande in queryset.filter(statut__in=[
            DemandeReservation.STATUT_NOUVELLE, DemandeReservation.STATUT_EN_COURS
        ]):
            demande.marquer_refusee(user=request.user, notes='Refusée depuis l\'admin.')
            updated += 1
        self.message_user(request, f"{updated} demande(s) refusée(s).")

    # ── Colonnes personnalisées ────────────────────────────────────────
    @admin.display(description='Statut', ordering='statut')
    def statut_badge(self, obj):
        colors = {
            'nouvelle':  ('#fef3c7', '#92400e'),
            'en_cours':  ('#dbeafe', '#1e40af'),
            'validee':   ('#d1fae5', '#065f46'),
            'refusee':   ('#fee2e2', '#991b1b'),
        }
        bg, color = colors.get(obj.statut, ('#f1f5f9', '#475569'))
        icon = obj.get_statut_icon()
        return format_html(
            '<span style="background:{};color:{};padding:3px 10px;border-radius:20px;'
            'font-size:.75rem;font-weight:700;display:inline-flex;align-items:center;gap:4px;">'
            '<i class="{}"></i> {}</span>',
            bg, color, icon, obj.get_statut_display()
        )

    @admin.display(description='Emplacements')
    def nb_emplacements_display(self, obj):
        n = obj.nb_emplacements()
        return format_html('<strong>{}</strong>', n)

    @admin.display(description='Durée')
    def duree_jours_display(self, obj):
        return f"{obj.duree_jours} jour(s)"

    @admin.display(description='Emplacements demandés')
    def emplacements_display(self, obj):
        lignes = []
        for face in obj.faces_souhaitees.select_related('support').all():
            lignes.append(
                f'<li><strong>{face.support.code}</strong> — Face {face.label} '
                f'<span style="color:#64748b;font-size:.8rem;">({face.support.ville})</span></li>'
            )
        for support in obj.supports_souhaites.all():
            lignes.append(
                f'<li><strong>{support.code}</strong> — Écran '
                f'<span style="color:#64748b;font-size:.8rem;">({support.ville})</span></li>'
            )
        if not lignes:
            return "Aucun emplacement"
        return format_html('<ul style="margin:0;padding-left:16px;">{}</ul>', format_html(''.join(lignes)))