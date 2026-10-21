from pathlib import Path, PurePosixPath
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse

from accounts.models import User
from campaigns.models import CampagneVisuel, Contrat, LigneCampagne, PreuveDiffusion
from inventory.models import Maintenance


def can_access_private_media(user, media_path):
    parts = PurePosixPath(media_path).parts
    if not parts:
        return False

    category = parts[0]
    is_staff = user.is_staff_regie_role

    if category == 'avatars':
        owner = User.objects.filter(avatar=media_path).only('pk').first()
        return owner is not None and (is_staff or owner.pk == user.pk)

    if category == 'documents':
        return is_staff and Contrat.objects.filter(document=media_path).exists()

    if category == 'proofs':
        return (
            (is_staff or user.is_technicien_role)
            and Maintenance.objects.filter(photo=media_path).exists()
        )

    if category == 'piges':
        proof = PreuveDiffusion.objects.filter(photo=media_path).select_related('campagne').first()
        return proof is not None and (
            is_staff
            or (
                user.is_client_role
                and user.client_profile_id == proof.campagne.client_id
            )
        )

    if category == 'visuels':
        visual = CampagneVisuel.objects.filter(fichier=media_path).select_related('campagne').first()
        if visual is not None:
            campaign = visual.campagne
        else:
            line = LigneCampagne.objects.filter(visuel=media_path).select_related('campagne').first()
            campaign = line.campagne if line is not None else None

        return campaign is not None and (
            is_staff
            or (
                user.is_client_role
                and user.client_profile_id == campaign.client_id
            )
        )

    return False


@login_required
def protected_media(request, media_path):
    if (
        not media_path
        or '\\' in media_path
        or any(part in {'', '.', '..'} for part in media_path.split('/'))
    ):
        raise Http404

    if not can_access_private_media(request.user, media_path):
        raise Http404

    media_root = Path(settings.MEDIA_ROOT).resolve()
    try:
        file_path = (media_root / Path(*PurePosixPath(media_path).parts)).resolve(strict=True)
        file_path.relative_to(media_root)
    except (FileNotFoundError, NotADirectoryError, RuntimeError, ValueError):
        raise Http404 from None

    if not file_path.is_file():
        raise Http404

    response = HttpResponse()
    response['X-Accel-Redirect'] = f'/_protected_media/{quote(media_path, safe="/")}'
    response['Cache-Control'] = 'private, no-store'
    return response
