from django.contrib import admin
from .models import *

class FaceInline(admin.TabularInline):
    model = FacePanneau
    extra = 0

class EcranInline(admin.StackedInline):
    model = EcranNumerique
    extra = 0

@admin.register(Support)
class SupportAdmin(admin.ModelAdmin):
    list_display = ['code', 'nom','code_ext', 'type_support', 'etat', 'ville', 'quartier', 'actif']
    list_filter = ['type_support', 'etat', 'ville']
    search_fields = ['code', 'nom', 'adresse']
    inlines = [FaceInline, EcranInline]
# Dans inventory/admin.py

@admin.register(Maintenance)
class MaintenanceAdmin(admin.ModelAdmin):
    list_display = ['support', 'face', 'effectue_par', 'date_intervention', 'etat_apres']
    list_filter = ['etat_apres', 'face', 'effectue_par', 'date_intervention']
    search_fields = ['support__code', 'face__label', 'description']
    
    
    