from django.urls import path
from .views import *

urlpatterns = [
    # API
    path('api/geojson/', api_geojson, name='api_geojson'),
    path('api/support/<int:pk>/popup/', api_support_popup, name='api_support_popup'),
    path('api/support/<int:pk>/faces/', api_faces_support, name='api_faces_support'),

    # CRUD
    path('', SupportListView.as_view(), name='support_list'),
    path('ajouter/', SupportCreateView.as_view(), name='support_create'),
    path('<int:pk>/', SupportDetailView.as_view(), name='support_detail'),
    path('<int:pk>/modifier/', SupportUpdateView.as_view(), name='support_edit'),
    path('<int:pk>/supprimer/', SupportDeleteView.as_view(), name='support_delete'),
    path('maintenances/',                    MaintenanceListView.as_view(),   name='maintenance_list'),
    path('maintenances/<int:pk>/',           MaintenanceDetailView.as_view(), name='maintenance_detail'),
    path('maintenances/creer/',              MaintenanceCreateView.as_view(), name='maintenance_create'),
    path('maintenances/creer/<int:pk>/',     MaintenanceCreateView.as_view(), name='maintenance_create'),
    path('maintenances/<int:pk>/modifier/',  MaintenanceUpdateView.as_view(), name='maintenance_update'),
]
