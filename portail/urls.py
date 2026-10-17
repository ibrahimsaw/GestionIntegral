# portail/urls.py
from django.urls import path
from . import views

app_name = 'portail'

urlpatterns = [

    # ── Pages publiques ───────────────────────────────────────────────────────
    path('',
         views.AccueilView.as_view(),
         name='accueil'),
    
    path('s',
             views.AccueilViews.as_view(),
             name='accueils'),

    path('v2',
         views.AccueilV2View.as_view(),
         name='accueil_v2'),

    path('v3',
         views.AccueilV3View.as_view(),
         name='accueil_v3'),

     path('v4',
         views.AccueilV4View.as_view(),
         name='accueil_v4'),

    path('v5',
         views.AccueilV5View.as_view(),
         name='accueil_v5'),

    path('v6',
         views.AccueilV6View.as_view(),
         name='accueil_v6'),

    path('v7',
         views.AccueilV7View.as_view(),
         name='accueil_v7'),

    path('v8',
         views.AccueilV8View.as_view(),
         name='accueil_v8'),
    
    path('v9',
         views.AccueilV9View.as_view(),
         name='accueil_v9'),


    path('v10',
         views.AccueilV10View.as_view(),
         name='accueil_v10'),
    
    path('v11', views.AccueilV11View.as_view(), name='accueil_v11'),
    
    path('v12', views.AccueilV12View.as_view(), name='accueil_v12'),

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
    path('suivi/', views.SuiviDemandeView.as_view(), name='suivi_demande'),     
    # ── API publiques (JSON) ──────────────────────────────────────────────────
    path('api/geojson/',
         views.ApiGeoJsonView.as_view(),
         name='api_geojson'),

    path('api/check-dispo/',
         views.ApiCheckDispoView.as_view(),
         name='api_check_dispo'),
    path('contact/envoyer/', views.ContactFormView.as_view(), name='contact_envoyer'),
]
