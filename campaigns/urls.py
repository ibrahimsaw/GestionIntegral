from django.urls import path
from . import views
from .simulation_view import SimulationCampagneEcranView


urlpatterns = [
    # Dashboard
    path('', views.DashboardView.as_view(), name='dashboard'),

    # Clients
    path('clients/', views.ClientListView.as_view(), name='client_list'),
    path('clients/creer/', views.ClientCreateView.as_view(), name='client_create'),
    path('clients/<int:pk>/', views.ClientDetailView.as_view(), name='client_detail'),
    path('clients/<int:pk>/modifier/', views.ClientUpdateView.as_view(), name='client_edit'),
    path('clients/<int:pk>/supprimer/', views.ClientDeleteView.as_view(), name='client_delete'),
    path('clients/<int:client_pk>/reservations/bulk/', views.ReservationPanneauBulkActionView.as_view(), name='reservations_bulk_action'),
    # Avant
    path('api/check-dispo/', views.verifier_dispo_faces_api, name='api_check_dispo'),
    # Après
    path('api/check-dispo/<int:client_pk>/', views.verifier_dispo_faces_api, name='api_check_dispo'),
    
    # Contrats
    path('contrats/creer/', views.ContratCreateView.as_view(), name='contrat_create'),
    path('contrats/creer/<int:client_pk>/', views.ContratCreateView.as_view(), name='contrat_create_for_client'),
    path('contrats/<int:pk>/modifier/', views.ContratUpdateView.as_view(), name='contrat_edit'),
    path('contrats/<int:pk>/supprimer/', views.ContratDeleteView.as_view(), name='contrat_delete'),

    # Campagnes
    path('campaigns/', views.CampagneListView.as_view(), name='campagne_list'),
    path('campaigns/creer/', views.CampagneCreateUpdateView.as_view(), name='campagne_create'),
    path('campaigns/<int:pk>/', views.CampagneDetailView.as_view(), name='campagne_detail'),
    path('campaigns/<int:pk>/modifier/', views.CampagneCreateUpdateView.as_view(), name='campagne_edit'),
    path('campaigns/<int:pk>/lancer/', views.CampagneLancerView.as_view(), name='campagne_lancer'),
    path('campaigns/<int:pk>/supprimer/', views.CampagneDeleteView.as_view(), name='campagne_delete'),
    path('campaigns/selectionne/supprimer/', views.CampagneSelectedDeleteView.as_view(), name='campagne_selected_delete'),
    # Visuels
    path('visuels/<int:pk>/supprimer/', views.VisuelDeleteView.as_view(), name='supprimer_visuel'),

    # Lignes
    path('campaigns/<int:campagne_pk>/supports/manage/', views.SupportBulkActionView.as_view(), name='supports_add_bulk'),
    path('campaigns/<int:campagne_pk>/supports/manage/', views.SupportBulkActionView.as_view(), name='supports_edit_bulk'),

    # API
    path('api/disponibilite/', views.ApiCheckDisponibiliteView.as_view(), name='api_disponibilite'),
    path('api/contrats-client/<int:client_id>/', views.GetClientContratsView.as_view(), name='get_client_contrats'),
    path('campagnes/simulation-ecran/', SimulationCampagneEcranView.as_view(), name='simulation_ecran'),
    
]
