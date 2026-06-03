from django.contrib import admin
from .models import Client, Contrat, Campagne, LigneCampagne, CampagneVisuel # N'oublie pas l'import

# 1. Créer l'interface "en ligne" pour les visuels
class CampagneVisuelInline(admin.TabularInline):
    model = CampagneVisuel
    extra = 3  # Nombre de lignes vides affichées par défaut
    verbose_name = "Visuel supplémentaire"
    verbose_name_plural = "Visuels de la campagne"

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
    
    # 2. Ajouter l'Inline ici
    inlines = [CampagneVisuelInline]

admin.site.register(LigneCampagne)