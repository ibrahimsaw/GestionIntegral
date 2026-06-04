from django.urls import path
from . import views
from .panneau_report_view import *

urlpatterns = [
    path('', views.ReportsIndexView.as_view(), name='reports_index'),
    #path('campagne/<int:pk>/pdf/', views.export_campagne_pdf, name='export_pdf'),
    # Campagne
    path('campagne/<int:pk>/pdf/', views.ExportCampagnePdfView.as_view(), name='export_campagne_pdf'),
    path('campagne/<int:pk>/preview/', views.PreviewCampagnePdfView.as_view(), name='campagne_preview'),
    # Client
    path('client/<int:pk>/pdf/', views.ExportClientPdfView.as_view(), name='export_client_pdf'),
    path('client/<int:pk>/excel/', views.ExportClientExcelView.as_view(), name='export_client_excel'),
    path('client/<int:pk>/preview/', views.PreviewClientPdfView.as_view(), name='preview_client_pdf'),
    path("panneaux/",PanneauxReportView.as_view(),name="panneaux_report",),
    path("panneaux/export/pdf/",ExportPanneauxPdfView.as_view(),name="panneaux_export_pdf",),
    path("panneaux/export/excel/",ExportPanneauxExcelView.as_view(),name="panneaux_export_excel",),
]
