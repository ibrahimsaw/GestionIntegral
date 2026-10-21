from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.http import Http404
from django.test import RequestFactory, SimpleTestCase, override_settings

from core.media_views import can_access_private_media, protected_media


class PrivateMediaAuthorizationTests(SimpleTestCase):
    def user(self, *, staff=False, technician=False, client_id=None, user_id=1):
        return SimpleNamespace(
            pk=user_id,
            is_staff_regie_role=staff,
            is_technicien_role=technician,
            is_client_role=client_id is not None,
            client_profile_id=client_id,
        )

    @patch('core.media_views.Contrat.objects.filter')
    def test_contract_files_are_staff_only(self, filter_contracts):
        filter_contracts.return_value.exists.return_value = True

        self.assertTrue(
            can_access_private_media(
                self.user(staff=True),
                'documents/contrats/contract.pdf',
            )
        )
        self.assertFalse(
            can_access_private_media(
                self.user(client_id=10),
                'documents/contrats/contract.pdf',
            )
        )

    @patch('core.media_views.CampagneVisuel.objects.filter')
    def test_campaign_visuals_are_limited_to_the_campaign_client(self, filter_visuals):
        filter_visuals.return_value.select_related.return_value.first.return_value = (
            SimpleNamespace(campagne=SimpleNamespace(client_id=10))
        )

        self.assertTrue(
            can_access_private_media(self.user(client_id=10), 'visuels/campaign.jpg')
        )
        self.assertFalse(
            can_access_private_media(self.user(client_id=20), 'visuels/campaign.jpg')
        )

    def test_unknown_private_media_categories_are_denied(self):
        self.assertFalse(
            can_access_private_media(self.user(staff=True), 'private/file.bin')
        )

    @patch('core.media_views.can_access_private_media', return_value=True)
    def test_authorized_file_uses_nginx_internal_redirect(self, _can_access):
        with TemporaryDirectory() as media_root:
            file_path = Path(media_root, 'avatars', 'profile image.png')
            file_path.parent.mkdir()
            file_path.write_bytes(b'image')
            request = RequestFactory().get(
                '/protected-media/avatars/profile%20image.png'
            )
            request.user = SimpleNamespace(is_authenticated=True)

            with override_settings(MEDIA_ROOT=media_root):
                response = protected_media(request, 'avatars/profile image.png')

        self.assertEqual(
            response['X-Accel-Redirect'],
            '/_protected_media/avatars/profile%20image.png',
        )
        self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_traversal_paths_are_rejected(self):
        request = RequestFactory().get('/protected-media/proofs/../secret.pdf')
        request.user = SimpleNamespace(is_authenticated=True)

        with self.assertRaises(Http404):
            protected_media(request, 'proofs/../secret.pdf')

# Create your tests here.
