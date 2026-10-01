from django.urls import path
from . import views
from .supports_report_view import *
from .supports_report_view import SupportsReportView, ExportSupportsPdfView, ExportSupportsExcelView
from .marches_report_view import MarchesReportView, ExportMarchesPdfView, ExportMarchesExcelView
from .clients_report_view import ClientsReportView, ExportClientsPdfView, ExportClientsExcelView
from .campagnes_report_view import CampagnesReportView, ExportCampagnesPdfView, ExportCampagnesExcelView

urlpatterns = [
    # Hub Rapports
    path('', views.ReportsIndexView.as_view(), name='reports_index'),
    #path('campagne/<int:pk>/pdf/', views.export_campagne_pdf, name='export_pdf'),
    # Campagne

    # ══════════════════════════════════════════════════════════════════
    # RAPPORTS GLOBAUX (LISTES COMPLÈTES & STATISTIQUES RÉSEAU)
    # ══════════════════════════════════════════════════════════════════
    # 1. Supports (Liste Globale)
    path("supports/", SupportsReportView.as_view(), name="supports_report"),
    path("supports/export/pdf/", ExportSupportsPdfView.as_view(), name="supports_export_pdf"),
    path("supports/export/excel/", ExportSupportsExcelView.as_view(), name="supports_export_excel"),

    # 2. Marchés (Liste Globale)
    path("marches/", MarchesReportView.as_view(), name="marches_report"),
    path("marches/export/pdf/", ExportMarchesPdfView.as_view(), name="marches_export_pdf"),
    path("marches/export/excel/", ExportMarchesExcelView.as_view(), name="marches_export_excel"),

    # 3. Clients (Portefeuille Global)
    path("clients/", ClientsReportView.as_view(), name="clients_report"),
    path("clients/export/pdf/", ExportClientsPdfView.as_view(), name="clients_export_pdf"),
    path("clients/export/excel/", ExportClientsExcelView.as_view(), name="clients_export_excel"),

    # 4. Campagnes (Liste Globale)
    path("campagnes/", CampagnesReportView.as_view(), name="campagnes_report"),
    path("campagnes/export/pdf/", ExportCampagnesPdfView.as_view(), name="campagnes_export_pdf"),
    path("campagnes/export/excel/", ExportCampagnesExcelView.as_view(), name="campagnes_export_excel"),

    # ══════════════════════════════════════════════════════════════════
    # RAPPORTS INDIVIDUELS (DÉTAILS PAR ÉLÉMENT)
    # ══════════════════════════════════════════════════════════════════
    # Campagne individuelle
    path('campagne/<int:pk>/pdf/', views.ExportCampagnePdfView.as_view(), name='export_campagne_pdf'),
    path('campagne/<int:pk>/preview/', views.PreviewCampagnePdfView.as_view(), name='campagne_preview'),
    # Client

    # Client individuel
    path('client/<int:pk>/pdf/', views.ExportClientPdfView.as_view(), name='export_client_pdf'),
    path('client/<int:pk>/excel/', views.ExportClientExcelView.as_view(), name='export_client_excel'),
    path('client/<int:pk>/preview/', views.PreviewClientPdfView.as_view(), name='preview_client_pdf'),
    path('client/<int:pk>/preview-alias/', views.PreviewClientPdfView.as_view(), name='client_preview'),
    path("supports/", SupportsReportView.as_view(), name="supports_report"),
    path("supports/export/pdf/", ExportSupportsPdfView.as_view(), name="supports_export_pdf"),
    path("supports/export/excel/", ExportSupportsExcelView.as_view(), name="supports_export_excel"),
    # Support individuel (Détail)

    # Support individuel
    path("support/<int:pk>/pdf/", views.ExportSupportPdfView.as_view(), name="export_support_pdf"),
    path("support/<int:pk>/preview/", views.PreviewSupportPdfView.as_view(), name="preview_support_pdf"),
    path("support/uuid/<uuid:uuid>/pdf/", views.ExportSupportPdfView.as_view(), name="export_support_uuid_pdf"),
    path("support/uuid/<uuid:uuid>/preview/", views.PreviewSupportPdfView.as_view(), name="preview_support_uuid_pdf"),
    # Marché individuel (Détail)

    # Marché individuel
    path("marche/<int:pk>/pdf/", views.ExportMarchePdfView.as_view(), name="export_marche_pdf"),
    path("marche/<int:pk>/preview/", views.PreviewMarchePdfView.as_view(), name="preview_marche_pdf"),
    # Emplacement individuel (Détail)

    # Emplacement individuel
    path("emplacement/<int:pk>/pdf/", views.ExportEmplacementPdfView.as_view(), name="export_emplacement_pdf"),
    path("emplacement/<int:pk>/preview/", views.PreviewEmplacementPdfView.as_view(), name="preview_emplacement_pdf"),
]


