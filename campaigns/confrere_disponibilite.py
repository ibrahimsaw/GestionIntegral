from django.core.exceptions import ValidationError
from django.db.models import Q

from inventory.models import SupportConfrere

from .models import (
    Campagne, DemandeReservation, Reservation, STATUT_CONFIRMEE, STATUT_EN_ATTENTE,
    normaliser_panneaux_confreres,
)


def occupations_confreres(debut, fin, *, demande_id=None, reservation_id=None, campagne_id=None):
    demandes = DemandeReservation.blocages_sur_periode(debut, fin).exclude(
        pk=demande_id,
    ).exclude(panneau_confrere__in=[{}, []])
    reservations = Reservation.objects.filter(
        statut__in=[STATUT_EN_ATTENTE, STATUT_CONFIRMEE], date_debut__date__lte=fin, date_fin__date__gte=debut,
    ).exclude(pk=reservation_id).exclude(panneaux_confreres_reserves=[])
    campagnes = (
        Campagne.objects.filter(
            Q(statut__in=[Campagne.STATUT_EN_COURS, Campagne.STATUT_A_VENIR], date_debut__lte=fin, date_fin__gte=debut)
            | Q(statut=Campagne.STATUT_EN_PAUSE, date_debut__lte=fin, date_mise_en_pause__lte=fin)
        ).exclude(pk=campagne_id).exclude(panneaux_confreres=[])
    )

    occupations = {}
    for objets, champ, statut, libelle in (
        (campagnes, 'panneaux_confreres', 'occupe', 'Campagne'),
        (reservations, 'panneaux_confreres_reserves', 'reserve', 'Réservation'),
        (demandes, 'panneau_confrere', 'reserve', 'Demande'),
    ):
        for objet in objets.only('reference', champ).order_by('pk').iterator():
            for ligne in normaliser_panneaux_confreres(getattr(objet, champ)):
                if ligne.get('support_id'):
                    for face in ligne.get('faces', []):
                        occupations.setdefault(f"{ligne['support_id']}:{face}", {
                            'statut': statut,
                            'info': f'{libelle} {objet.reference}',
                        })
    return occupations


def verifier_disponibilite_confreres(lignes, debut, fin, **exclusions):
    selection = [ligne for ligne in lignes if ligne.get('support_id')]
    if not selection:
        return

    # Sérialise la vérification des demandes, réservations et campagnes concurrentes.
    list(SupportConfrere.objects.select_for_update().filter(
        pk__in={ligne['support_id'] for ligne in selection},
    ).order_by('pk').values_list('pk', flat=True))
    occupations = occupations_confreres(debut, fin, **exclusions)
    conflits = [
        f"{ligne['reference']} · Face {face}"
        for ligne in selection for face in ligne.get('faces', [])
        if f"{ligne['support_id']}:{face}" in occupations
    ]
    if conflits:
        raise ValidationError(
            'Ces faces confrères sont déjà bloquées sur cette période : %(faces)s.',
            params={'faces': ', '.join(conflits)},
        )
