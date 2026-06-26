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
