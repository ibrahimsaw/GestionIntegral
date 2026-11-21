from django.urls import path
from .views import *
from . import confrere_views

urlpatterns = [
    path('confreres/dossiers/<int:pk>/', confrere_views.DossierConfrereView.as_view(), name='confrere_dossier'),
    path('confreres/dossiers/<int:pk>/mode-confirmation/', confrere_views.ConfirmationAutomatiqueConfrereView.as_view(), name='confrere_mode_confirmation'),
    path('confreres/dossiers/<int:pk>/documents/<int:document_pk>/', confrere_views.DocumentDossierConfrereView.as_view(), name='confrere_document'),
    path('confreres/dossiers/<int:pk>/bon-de-commande/', confrere_views.BonCommandeConfrereView.as_view(), name='confrere_bon_commande'),
    path('confreres/', confrere_views.ConfrereListView.as_view(), name='confrere_list'),
    path('confreres/ajouter/', confrere_views.ConfrereCreateView.as_view(), name='confrere_create'),
    path('confreres/<int:pk>/', confrere_views.ConfrereDetailView.as_view(), name='confrere_detail'),
    path('confreres/<int:pk>/mode-confirmation/', confrere_views.ModeConfirmationConfrereView.as_view(), name='confrere_fiche_mode_confirmation'),
    path('confreres/<int:pk>/modifier/', confrere_views.ConfrereUpdateView.as_view(), name='confrere_edit'),
    path('confreres/<int:confrere_pk>/supports/ajouter/', confrere_views.SupportConfrereCreateView.as_view(), name='confrere_support_create'),
    path('confreres/<int:confrere_pk>/supports/<int:pk>/modifier/', confrere_views.SupportConfrereUpdateView.as_view(), name='confrere_support_edit'),
    # API
    path('api/geojson/', api_geojson, name='api_geojson'),
    path('api/support/<int:pk>/popup/', api_support_popup, name='api_support_popup'),
    path('api/support/<int:pk>/faces/', api_faces_support, name='api_faces_support'),
    path('api/faces/<int:support_id>/', get_faces_support, name='api_faces_support'),
    path('supports/next-code/', support_next_code, name='support_next_code'),
    path('supports/actions/', SupportBulkActionView.as_view(), name='support_bulk_action'),

    # CRUD
    path('', SupportListView.as_view(), name='support_list'),
    path('ajouter/', SupportCreateView.as_view(), name='support_create'),
    path('suppressions/en-attente/', SupportDeletionRequestsView.as_view(), name='support_deletion_requests'),
    path('<uuid:uuid>/objectifs-annuels/', EcranObjectifsAnnuelsView.as_view(), name='ecran_objectifs_annuels'),
    path('<uuid:uuid>/', SupportDetailView.as_view(), name='support_detail'),
    path('<uuid:uuid>/modifier/', SupportUpdateView.as_view(), name='support_edit'),
    path('<uuid:uuid>/supprimer/', SupportDeleteView.as_view(), name='support_delete'),
    path('maintenances/',                    MaintenanceListView.as_view(),   name='maintenance_list'),
    path('maintenances/<int:pk>/',           MaintenanceDetailView.as_view(), name='maintenance_detail'),
    path('maintenances/<int:pk>/reparation-rapide/', MaintenanceQuickRepairView.as_view(), name='maintenance_quick_repair'),
    path('maintenances/<int:pk>/demarrer-reparation/', MaintenanceStartRepairView.as_view(), name='maintenance_start_repair'),
    path('maintenances/creer/',              MaintenanceCreateView.as_view(), name='maintenance_create'),
    path('maintenances/creer/<int:pk>/',     MaintenanceCreateView.as_view(), name='maintenance_create'),
    path('maintenances/<int:pk>/modifier/',  MaintenanceUpdateView.as_view(), name='maintenance_update'),
    path('<int:pk>/periodes-panne/', SupportPeriodesVanneView.as_view(), name='support_periodes_panne'),
    path('maintenances/periodes-panne/', PeriodesParVueView.as_view(), name='periodes_panne_liste'),
    path('maintenances/periodes-panne/<int:pk>/', PeriodePanneDetailView.as_view(), name='periode_panne_detail'),

    path('formats/', FormatSupportListView.as_view(), name='format_list'),
    path('formats/ajouter/', FormatSupportCreateView.as_view(), name='format_create'),
    path('formats/<int:pk>/', FormatSupportDetailView.as_view(), name='format_support_detail'),
    path('formats/<int:pk>/modifier/', FormatSupportUpdateView.as_view(), name='format_update'),
    path('formats/<int:pk>/supprimer/', FormatSupportDeleteView.as_view(), name='format_delete'),

    # Marchés
    path('marches/', MarcheListView.as_view(), name='marche_list'),
    path('marches/ajouter/', SupportCreateView.as_view(), name='marche_create'),
    path('marches/<int:pk>/', MarcheDetailView.as_view(), name='marche_detail'),
    path('marches/<int:pk>/modifier/', MarcheUpdateView.as_view(), name='marche_update'),
    path('marches/<int:pk>/supprimer/', MarcheDeleteView.as_view(), name='marche_delete'),

    # Emplacements (rattachés à un marché)
    path('marches/<int:marche_pk>/emplacements/ajouter/', EmplacementCreateView.as_view(), name='emplacement_create'),
    path('marches/<int:marche_pk>/emplacements/actions/', EmplacementBulkActionView.as_view(), name='emplacement_bulk_action'),
    path('marches/<int:marche_pk>/emplacements/next-code/', emplacement_next_code, name='emplacement_next_code'),
    path('emplacements/<int:pk>/', EmplacementDetailView.as_view(), name='emplacement_detail'),
    path('emplacements/<int:pk>/modifier/', EmplacementUpdateView.as_view(), name='emplacement_update'),
    path('emplacements/<int:pk>/supprimer/', EmplacementDeleteView.as_view(), name='emplacement_delete'),

    # API
    path('api/marches/<int:pk>/emplacements/', api_emplacements_marche, name='api_emplacements_marche'),
    path('api/marches/geojson/', api_marches_geojson, name='api_marches_geojson'),
    path('api/marches/<int:pk>/popup/', api_marche_popup, name='api_marche_popup'),
]
