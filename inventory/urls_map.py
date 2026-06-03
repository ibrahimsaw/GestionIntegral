from django.urls import path
from inventory.views import carte

urlpatterns = [
    path('', carte, name='carte'),
]
