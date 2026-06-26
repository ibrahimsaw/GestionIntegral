from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('inventory/', include('inventory.urls')),
    path('campaigns/', include('campaigns.urls')),
    path('planning/', include('planning.urls')),
    path('reports/', include('reports.urls')),
    path('info', include('portail.urls')),
    path('', include('inventory.urls_map')),   # carte = page principale
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
