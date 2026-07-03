from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('gestion/admin/', admin.site.urls),
    path('gestion/accounts/', include('accounts.urls')),
    path('gestion/inventory/', include('inventory.urls')),
    path('gestion/campaigns/', include('campaigns.urls')),
    path('gestion/planning/', include('planning.urls')),
    path('gestion/reports/', include('reports.urls')),
    path('', include('portail.urls')),
    path('gestion/', include('inventory.urls_map')),   # carte = page principale
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    