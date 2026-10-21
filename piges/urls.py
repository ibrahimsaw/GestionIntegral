from django.urls import path

from .views import PigeFichierDeleteView, PigeFichierUpdateView, PigesUploadView

app_name = 'piges'

urlpatterns = [
    path(
        'campagnes/<int:campagne_pk>/ajouter/',
        PigesUploadView.as_view(),
        name='upload',
    ),
    path(
        'campagnes/<int:campagne_pk>/photos/<int:pige_pk>/modifier/',
        PigeFichierUpdateView.as_view(),
        name='photo_update',
    ),
    path(
        'campagnes/<int:campagne_pk>/photos/<int:pige_pk>/supprimer/',
        PigeFichierDeleteView.as_view(),
        name='photo_delete',
    ),
]
