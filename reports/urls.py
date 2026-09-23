from django.urls import path
from . import views
from .supports_report_view import *

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
    path('client/<int:pk>/preview-alias/', views.PreviewClientPdfView.as_view(), name='client_preview'),
    path("supports/", SupportsReportView.as_view(), name="supports_report"),
    path("supports/export/pdf/", ExportSupportsPdfView.as_view(), name="supports_export_pdf"),
    path("supports/export/excel/", ExportSupportsExcelView.as_view(), name="supports_export_excel"),
]
