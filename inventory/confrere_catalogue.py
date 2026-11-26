from collections import defaultdict
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.utils import timezone

from campaigns.tarification import tarif_par_ville

from .models import Confrere, DossierConfrere, SupportConfrere


def ligne_support_confrere(support, faces):
    format_support = support.format
    libelle_format = (format_support.dimensions or format_support.code) if format_support else support.format_libre
    return {
        'support_id': support.pk,
        'confrere_id': support.confrere_id,
        'reference': support.reference,
        'nom': support.confrere.nom,
        'nom_support': support.nom,
        'ville': support.ville,
        'emplacement': support.emplacement,
        'format': libelle_format or support.categorie_affichee or 'Format non renseigné',
        'faces': faces,
        'nombre_faces': len(faces),
    }


def catalogue_confreres():
    catalogue = {
        pk: {'id': pk, 'nom': nom, 'supports': []}
        for pk, nom in Confrere.objects.filter(archive=False).values_list('pk', 'nom')
    }
    supports = SupportConfrere.objects.filter(archive=False, confrere__archive=False).select_related(
        'confrere', 'format',
    ).order_by('confrere__nom', 'reference')
    for support in supports:
        confrere = catalogue.get(support.confrere_id)
        if confrere:
            ligne = ligne_support_confrere(support, list(support.codes_faces))
            format_support = support.format
            mesure = f' ({format_support.superficie:g} m²)' if format_support and format_support.superficie else ''
            confrere['supports'].append({
                **ligne,
                'categorie': support.categorie_affichee,
                'format_code': format_support.code if format_support else '',
                'format_filtre': ligne['format'] + mesure,
            })
    return list(catalogue.values())


def verifier_supports_confreres(demande, lignes):
    faces_existantes = defaultdict(set)
    for ligne in demande.panneaux_confreres:
        if ligne.get('support_id'):
            faces_existantes[ligne['support_id']].update(ligne['faces'])

    nouveaux_supports = {
        ligne['support_id'] for ligne in lignes
        if ligne.get('support_id') and not set(ligne['faces']).issubset(faces_existantes[ligne['support_id']])
    }
    if not nouveaux_supports:
        return
    supports = SupportConfrere.objects.select_for_update().filter(
        pk__in=nouveaux_supports, archive=False, confrere__archive=False,
    ).order_by('pk')
    if set(supports.values_list('pk', flat=True)) != nouveaux_supports:
        raise ValidationError("Un support confrère a été retiré. Revenez à l'étape 1 pour modifier la sélection.")


def lignes_recap_confreres(panneaux, tarifs=None):
    tarifs = tarifs or {}
    return [
        {
            **panneau,
            'face': face,
            'tarif': tarif_par_ville(tarifs, panneau.get('ville'), f"confrere:{panneau['format']}"),
        }
        for panneau in panneaux
        for face in (panneau.get('faces') or [None])
    ]


def statut_validation_confreres(demande):
    lignes = demande.panneaux_confreres
    if not lignes:
        return ''
    if any(not ligne.get('confrere_id') for ligne in lignes):
        return 'manuel'

    confreres = {ligne['confrere_id'] for ligne in lignes}
    periode = f'{demande.date_debut_souhaitee}/{demande.date_fin_souhaitee}'
    reponses = dict(demande.dossiers_confreres.filter(
        actif=True, confrere_id__in=confreres, periode_validation=periode,
    ).values_list('confrere_id', 'reponse_confrere'))
    if DossierConfrere.Reponse.REFUSEE in reponses.values():
        return 'refuse'
    if len(reponses) == len(confreres) and all(
        reponse in (DossierConfrere.Reponse.CONFIRMEE, DossierConfrere.Reponse.AUTOMATIQUE)
        for reponse in reponses.values()
    ):
        return 'confirme'
    return 'attente'


def synchroniser_dossiers_confreres(demande):
    groupes = defaultdict(list)
    for ligne in demande.panneaux_confreres:
        if ligne.get('confrere_id'):
            groupes[ligne['confrere_id']].append(ligne)

    demande.dossiers_confreres.exclude(confrere_id__in=groupes).update(actif=False)
    periode = f'{demande.date_debut_souhaitee}/{demande.date_fin_souhaitee}'
    maintenant = timezone.now()
    modes = dict(Confrere.objects.filter(pk__in=groupes).values_list('pk', 'confirmation_automatique'))
    for confrere_id, lignes in groupes.items():
        automatique = modes[confrere_id]
        dossier, created = DossierConfrere.objects.select_for_update().get_or_create(
            demande=demande, confrere_id=confrere_id,
            defaults={
                'lignes': lignes,
                'periode_validation': periode,
                'confirmation_automatique': automatique,
                'reponse_confrere': (
                    DossierConfrere.Reponse.AUTOMATIQUE if automatique else DossierConfrere.Reponse.EN_ATTENTE
                ),
                'repondu_le': maintenant if automatique else None,
            },
        )
        if not created and (not dossier.actif or dossier.lignes != lignes or dossier.periode_validation != periode):
            dossier.lignes = lignes
            dossier.actif = True
            dossier.periode_validation = periode
            dossier.token_validation = uuid4()
            dossier.reponse_confrere = (
                DossierConfrere.Reponse.AUTOMATIQUE if dossier.confirmation_automatique
                else DossierConfrere.Reponse.EN_ATTENTE
            )
            dossier.repondu_le = maintenant if dossier.confirmation_automatique else None
            dossier.save(update_fields=[
                'lignes', 'actif', 'periode_validation', 'token_validation', 'reponse_confrere', 'repondu_le',
            ])
        elif not created and dossier.confirmation_automatique and dossier.reponse_confrere == DossierConfrere.Reponse.EN_ATTENTE:
            dossier.reponse_confrere = DossierConfrere.Reponse.AUTOMATIQUE
            dossier.repondu_le = maintenant
            dossier.save(update_fields=['reponse_confrere', 'repondu_le'])
