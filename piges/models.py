from django.conf import settings
from django.db import models


class PigeFichier(models.Model):
    campagne = models.ForeignKey(
        'campaigns.Campagne',
        on_delete=models.CASCADE,
        related_name='pige_fichiers',
        null=True,
        blank=True,
        verbose_name="Campagne",
    )
    ligne = models.ForeignKey(
        'campaigns.LigneCampagne',
        on_delete=models.SET_NULL,
        related_name='pige_fichiers_drive',
        null=True,
        blank=True,
        verbose_name="Support / emplacement de campagne",
    )
    support = models.ForeignKey(
        'inventory.Support',
        on_delete=models.PROTECT,
        related_name='pige_fichiers',
        null=True,
        blank=True,
        verbose_name="Support photographié",
    )
    support_nom = models.CharField(max_length=200, verbose_name="Nom du support au dépôt")
    ville = models.CharField(max_length=100, blank=True, verbose_name="Ville au dépôt")
    date_prise = models.DateField(verbose_name="Date de prise de vue")
    nom_fichier = models.CharField(max_length=255, verbose_name="Nom du fichier Drive")
    drive_file_id = models.CharField(max_length=200, unique=True, verbose_name="Identifiant Drive")
    ajoute_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='pige_fichiers_ajoutes',
        verbose_name="Ajouté par",
    )
    ajoute_le = models.DateTimeField(auto_now_add=True, verbose_name="Envoyé le")

    class Meta:
        ordering = ['-date_prise', '-ajoute_le']
        verbose_name = "Fichier de pige"
        verbose_name_plural = "Fichiers de pige"

    def __str__(self):
        return self.nom_fichier
