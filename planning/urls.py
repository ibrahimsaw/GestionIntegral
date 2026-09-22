from django.urls import path
from . import views

urlpatterns = [
    path('calendrier/', views.PlanningCalendrierView.as_view(), name='planning_calendrier'),
    path('api/taux/', views.ApiTauxOccupationView.as_view(), name='api_taux_occupation'),
]
