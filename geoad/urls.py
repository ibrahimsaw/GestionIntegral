from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView

urlpatterns = [
    path('favicon.ico', RedirectView.as_view(url=settings.STATIC_URL + 'img/logo.png', permanent=True)),
    path('gestion/admin/', admin.site.urls),
    path('gestion/accounts/', include('accounts.urls')),
    path('gestion/inventory/', include('inventory.urls')),
    path('gestion/campaigns/', include('campaigns.urls')),
    path('gestion/piges/', include('piges.urls')),
    path('gestion/planning/', include('planning.urls')),
    path('gestion/reports/', include('reports.urls')),
    path('', include('portail.urls')),
    path('gestion/', include('inventory.urls_map')),   # carte = page principale
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

    