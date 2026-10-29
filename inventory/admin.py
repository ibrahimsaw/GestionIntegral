from django.contrib import admin
from accounts.audit import log_action, log_changes
from accounts.models import AuditLog
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
    search_fields = ['code', 'nom', 'ancien_nom', 'adresse']
    inlines = [FaceInline, EcranInline]
# Dans inventory/admin.py

@admin.register(Maintenance)
class MaintenanceAdmin(admin.ModelAdmin):
    list_display = ['support', 'face', 'effectue_par', 'date_intervention', 'etat_apres']
    list_filter = ['etat_apres', 'face', 'effectue_par', 'date_intervention']
    search_fields = ['support__code', 'face__label', 'description']

    def save_model(self, request, obj, form, change):
        old_maintenance = (
            Maintenance.objects.get(pk=obj.pk)
            if change
            else None
        )
        super().save_model(request, obj, form, change)

        if old_maintenance:
            changes = log_changes(
                old_maintenance,
                obj,
                [
                    'support',
                    'face',
                    'effectue_par',
                    'date_intervention',
                    'etat_apres',
                    'description',
                    'photo',
                    'reparation_commencee_le',
                    'reparation_commencee_par',
                ],
            )
            action = (
                AuditLog.ACTION_ETAT_CHANGE
                if changes and 'etat_apres' in changes
                else AuditLog.ACTION_UPDATE
            )
            detail = f"Modification de l'intervention de maintenance #{obj.pk} depuis l'administration."
        else:
            action = AuditLog.ACTION_CREATE
            changes = {
                'support': {'avant': '', 'apres': obj.support.code},
                'face': {'avant': '', 'apres': obj.face.label if obj.face else ''},
                'effectue_par': {'avant': '', 'apres': str(obj.effectue_par or '')},
                'date_intervention': {'avant': '', 'apres': obj.date_intervention.isoformat()},
                'etat_apres': {'avant': '', 'apres': obj.etat_apres},
                'description': {'avant': '', 'apres': obj.description},
                'photo': {'avant': '', 'apres': obj.photo.name if obj.photo else ''},
            }
            detail = f"Création d'une intervention de maintenance depuis l'administration pour {obj.support.code}."

        log_action(
            request,
            action,
            AuditLog.MODULE_INVENTORY,
            obj=obj,
            detail=detail,
            changes=changes,
        )

    def delete_model(self, request, obj):
        log_action(
            request,
            AuditLog.ACTION_DELETE,
            AuditLog.MODULE_INVENTORY,
            obj=obj,
            detail=f"Suppression de l'intervention de maintenance #{obj.pk}.",
            changes={
                'support': {'avant': obj.support.code, 'apres': ''},
                'face': {'avant': obj.face.label if obj.face else '', 'apres': ''},
                'etat_apres': {'avant': obj.etat_apres, 'apres': ''},
                'description': {'avant': obj.description, 'apres': ''},
            },
        )
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset.select_related('support', 'face'):
            log_action(
                request,
                AuditLog.ACTION_DELETE,
                AuditLog.MODULE_INVENTORY,
                obj=obj,
                detail=f"Suppression de l'intervention de maintenance #{obj.pk}.",
                changes={
                    'support': {'avant': obj.support.code, 'apres': ''},
                    'face': {'avant': obj.face.label if obj.face else '', 'apres': ''},
                    'etat_apres': {'avant': obj.etat_apres, 'apres': ''},
                    'description': {'avant': obj.description, 'apres': ''},
                },
            )
        super().delete_queryset(request, queryset)


@admin.register(ObjectifSpotsEcranAnnuel)
class ObjectifSpotsEcranAnnuelAdmin(admin.ModelAdmin):
    list_display = ['ecran', 'annee', 'nombre_spots']
    list_filter = ['annee']
    search_fields = ['ecran__support__code', 'ecran__support__nom']
    