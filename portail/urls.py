# portail/urls.py
from django.urls import path
from . import views

app_name = 'portail'

urlpatterns = [

    # ── Pages publiques ───────────────────────────────────────────────────────
    path('',
         views.AccueilView.as_view(),
         name='accueil'),

    path('catalogue/',
         views.CatalogueView.as_view(),
         name='catalogue'),

    path('supports/',
         views.SupportsListeView.as_view(),
         name='supports_liste'),

    path('support/<uuid:uuid>/',
         views.SupportDetailView.as_view(),
         name='support_detail'),

    path('services/',
         views.ServicesView.as_view(),
         name='services'),

    path('contact/',
         views.ContactView.as_view(),
         name='contact'),

    path('contact/confirmation/',
         views.ContactConfirmationView.as_view(),
         name='contact_confirmation'),

    # ── Wizard réservation (3 étapes) ─────────────────────────────────────────
    path('reserver/',
         views.ReserverEtape1View.as_view(),
         name='reserver_etape1'),

    path('reserver/etape2/',
         views.ReserverEtape2View.as_view(),
         name='reserver_etape2'),

    path('reserver/etape3/',
         views.ReserverEtape3View.as_view(),
         name='reserver_etape3'),

    path('confirmation/<uuid:uuid>/',
         views.ConfirmationView.as_view(),
         name='confirmation'),

    # ── API publiques (JSON) ──────────────────────────────────────────────────
    path('api/geojson/',
         views.ApiGeoJsonView.as_view(),
         name='api_geojson'),

    path('api/check-dispo/',
         views.ApiCheckDispoView.as_view(),
         name='api_check_dispo'),
]
