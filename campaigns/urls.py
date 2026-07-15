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

    path('reservations/nouvelle/etape1/', views.ReservationWizardEtape1View.as_view(), name='reservation_wizard_etape1'),
    # Variante utilisée depuis client_detail.html : {% url 'reservation_wizard_etape1' client.pk %}
    # → pré-sélectionne le client et saute directement à l'étape 2.
    path('clients/<int:client_pk>/reservations/nouvelle/etape1/', views.ReservationWizardEtape1View.as_view(), name='reservation_wizard_etape1'),
    path('reservations/nouvelle/etape2/', views.ReservationWizardEtape2View.as_view(), name='reservation_wizard_etape2'),
    path('reservations/nouvelle/etape3/', views.ReservationWizardEtape3View.as_view(), name='reservation_wizard_etape3'),
    path('reservations/nouvelle/annuler/', views.ReservationWizardCancelView.as_view(), name='reservation_wizard_cancel'),
    path('clients/<int:client_pk>/reservations/create/',views.ReservationCreateView.as_view(),name='reservation_create'),

    path('clients/<int:client_pk>/reservations/<int:resa_pk>/update/',views.ReservationUpdateView.as_view(),name='reservation_update'),
    path('clients/<int:client_pk>/reservations/<int:resa_pk>/delete/',views.ReservationDeleteView.as_view(),name='reservation_delete'),
    path('clients/<int:client_pk>/reservations/<int:resa_pk>/',views.ReservationDetailView.as_view(),name='reservation_detail'),
    # À ajouter dans campaigns/urls.py, à côté des autres routes 'reservations'

    path('reservations/<int:pk>/', views.ReservationRedirectView.as_view(), name='reservation_detail_direct'),
    path('reservations/select-client/', views.ReservationSelectClientView.as_view(), name='reservation_select_client'),
    path('reservations/', views.ReservationListView.as_view(), name='reservation_list'),

    path('clients/<int:client_pk>/reservations/<int:resa_pk>/traiter/',views.ReservationTraiterView.as_view(),name='reservation_traiter',),
    # Avant
    path('api/check-dispos/', views.verifier_dispo_faces_api, name='api_check_dispo'),
    # Après
    path('api/check-dispos/<int:client_pk>/', views.verifier_dispo_faces_api, name='api_check_dispo'),
    # urls.py
    path('api/check-dispo/<int:client_pk>/', views.api_check_dispo, name='api_check_dispo'),
    path('api/dashboard/clients-actifs/', views.api_dashboard_clients_actifs, name='api_dashboard_clients_actifs'),
    path('api/dashboard/campagnes/<str:statut>/', views.api_dashboard_campagnes, name='api_dashboard_campagnes'),
    path('api/dashboard/client/<int:client_id>/campagnes/', views.api_dashboard_client_campagnes, name='api_dashboard_client_campagnes'),
    
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
    path('api/campagnes-meres-client/<int:client_id>/', views.GetClientCampagnesMeresView.as_view(), name='get_client_campagnes_meres'),
    path('campagnes/simulation-ecran/', SimulationCampagneEcranView.as_view(), name='simulation_ecran'),
    

    # ── Dashboard staff ───────────────────────────────────────────────────────
    path('',views.DashboardView.as_view(),name='dashboard'),

    # ── Gestion des demandes ──────────────────────────────────────────────────


    # ── AJAX ──────────────────────────────────────────────────────────────────
    path('api/search-client/',views.AjaxSearchClientView.as_view(),name='ajax_search_client'),

    path('api/check-faces-dispo/',views.AjaxCheckFacesDispoView.as_view(),name='ajax_check_faces_dispo'),
    path('api/campagne-parente-info/<int:campagne_id>/', views.api_campagne_parente_info, name='api_campagne_parente_info'),
    path('demandes/', views.DemandesListView.as_view(), name='demandes_liste'),
    path('demandes/<uuid:uuid>/', views.DemandeDetailView.as_view(), name='demande_detail'),
    path('demandes/<uuid:uuid>/valider/', views.DemandeValiderView.as_view(), name='demande_valider'),
    path('demandes/<uuid:uuid>/refuser/', views.DemandeRefuserView.as_view(), name='demande_refuser'),
    
    
]