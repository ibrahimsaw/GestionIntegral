from django.urls import path
from .views import *

urlpatterns = [
    # API
    path('api/geojson/', api_geojson, name='api_geojson'),
    path('api/support/<int:pk>/popup/', api_support_popup, name='api_support_popup'),
    path('api/support/<int:pk>/faces/', api_faces_support, name='api_faces_support'),
    path('api/faces/<int:support_id>/', get_faces_support, name='api_faces_support'),
    path('supports/next-code/', support_next_code, name='support_next_code'),

    # CRUD
    path('', SupportListView.as_view(), name='support_list'),
    path('ajouter/', SupportCreateView.as_view(), name='support_create'),
    path('<uuid:uuid>/', SupportDetailView.as_view(), name='support_detail'),
    path('<uuid:uuid>/modifier/', SupportUpdateView.as_view(), name='support_edit'),
    path('<uuid:uuid>/supprimer/', SupportDeleteView.as_view(), name='support_delete'),
    path('maintenances/',                    MaintenanceListView.as_view(),   name='maintenance_list'),
    path('maintenances/<int:pk>/',           MaintenanceDetailView.as_view(), name='maintenance_detail'),
    path('maintenances/<int:pk>/reparation-rapide/', MaintenanceQuickRepairView.as_view(), name='maintenance_quick_repair'),
    path('maintenances/creer/',              MaintenanceCreateView.as_view(), name='maintenance_create'),
    path('maintenances/creer/<int:pk>/',     MaintenanceCreateView.as_view(), name='maintenance_create'),
    path('maintenances/<int:pk>/modifier/',  MaintenanceUpdateView.as_view(), name='maintenance_update'),
    path('<int:pk>/periodes-panne/', SupportPeriodesVanneView.as_view(), name='support_periodes_panne'),
    path('maintenances/periodes-panne/', PeriodesParVueView.as_view(), name='periodes_panne_liste'),

    path('formats/', FormatSupportListView.as_view(), name='format_list'),
    path('formats/ajouter/', FormatSupportCreateView.as_view(), name='format_create'),
    path('formats/<int:pk>/modifier/', FormatSupportUpdateView.as_view(), name='format_update'),
    path('formats/<int:pk>/supprimer/', FormatSupportDeleteView.as_view(), name='format_delete'),

    # Marchés
    path('marches/', MarcheListView.as_view(), name='marche_list'),
    path('marches/ajouter/', MarcheCreateView.as_view(), name='marche_create'),
    path('marches/<int:pk>/', MarcheDetailView.as_view(), name='marche_detail'),
    path('marches/<int:pk>/modifier/', MarcheUpdateView.as_view(), name='marche_update'),
    path('marches/<int:pk>/supprimer/', MarcheDeleteView.as_view(), name='marche_delete'),

    # Emplacements (rattachés à un marché)
    path('marches/<int:marche_pk>/emplacements/ajouter/', EmplacementCreateView.as_view(), name='emplacement_create'),
    path('marches/<int:marche_pk>/emplacements/next-code/', emplacement_next_code, name='emplacement_next_code'),
    path('emplacements/<int:pk>/', EmplacementDetailView.as_view(), name='emplacement_detail'),
    path('emplacements/<int:pk>/modifier/', EmplacementUpdateView.as_view(), name='emplacement_update'),
    path('emplacements/<int:pk>/supprimer/', EmplacementDeleteView.as_view(), name='emplacement_delete'),

    # API
    path('api/marches/<int:pk>/emplacements/', api_emplacements_marche, name='api_emplacements_marche'),
    path('api/marches/geojson/', api_marches_geojson, name='api_marches_geojson'),
    path('api/marches/<int:pk>/popup/', api_marche_popup, name='api_marche_popup'),
]