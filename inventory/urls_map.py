from django.urls import path
from campaigns import views
from inventory.views import carte

urlpatterns = [
        # Dashboard
    path('', views.DashboardView.as_view(), name='dashboard'),
    path('Carte', carte, name='carte'),
]
