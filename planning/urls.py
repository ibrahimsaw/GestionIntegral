from django.urls import path
from . import views

urlpatterns = [
    path('calendrier/', views.PlanningCalendrierView.as_view(), name='planning_calendrier'),
    path('main-courante/', views.MainCouranteView.as_view(), name='main_courante'),
    path('api/taux/', views.ApiTauxOccupationView.as_view(), name='api_taux_occupation'),
]
