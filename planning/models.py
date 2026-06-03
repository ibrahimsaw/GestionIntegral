from django.db import models
from django.utils import timezone


class LogDiffusion(models.Model):
    """Enregistrement de chaque passage d'un spot (main courante)."""
    ecran       = models.ForeignKey('inventory.EcranNumerique', on_delete=models.CASCADE, related_name='logs')
    ligne       = models.ForeignKey('campaigns.LigneCampagne', on_delete=models.SET_NULL, null=True, related_name='logs')
    client      = models.ForeignKey('campaigns.Client', on_delete=models.SET_NULL, null=True)
    campagne    = models.ForeignKey('campaigns.Campagne', on_delete=models.SET_NULL, null=True)
    timestamp   = models.DateTimeField(default=timezone.now)
    duree_sec   = models.PositiveIntegerField(default=10)
    genere_auto = models.BooleanField(default=True)

    class Meta:
        ordering = ['timestamp']
        verbose_name = "Log de Diffusion"
        verbose_name_plural = "Main Courante"

    def __str__(self):
        return f"{self.timestamp:%H:%M:%S} — {self.client} ({self.duree_sec}s)"
