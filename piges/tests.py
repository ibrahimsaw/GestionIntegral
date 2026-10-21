import io
import os
from datetime import date, timedelta
from unittest.mock import Mock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client as DjangoClient, TestCase, SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError
from PIL import Image

from accounts.models import User
from campaigns.models import Client as CampaignClient
from campaigns.models import Campagne, DemandeReservation, LigneCampagne
from inventory.models import FacePanneau, Support
from .drive_piges import (
    DrivePigesError,
    _execute,
    _http_error,
    build_photo_filename,
    build_photo_prefix,
    delete_photo,
    download_photo,
    get_drive_service,
    get_or_create_campaign_folder,
    get_or_create_folder,
    prepare_photo,
)
from .forms import MAX_FILES_PER_UPLOAD, MAX_IMAGE_SIZE, PigesUploadForm
from .models import PigeFichier


def make_image(name='photo.png', image_format='PNG', size=(10, 10)):
    image = Image.new('RGB', size, color='red')
    buffer = io.BytesIO()
    image.save(buffer, format=image_format)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f'image/{image_format.lower()}')


class DriveFolderTests(SimpleTestCase):
    def test_existing_folder_is_reused_and_query_values_are_escaped(self):
        service = Mock()
        service.files.return_value.list.return_value.execute.return_value = {
            'files': [{'id': 'existing-folder'}],
        }

        folder_id = get_or_create_folder(
            service,
            "Client O'Brien\\Centre",
            'parent-id',
        )

        self.assertEqual(folder_id, 'existing-folder')
        query = service.files.return_value.list.call_args.kwargs['q']
        self.assertIn(r"name = 'Client O\'Brien\\Centre'", query)
        service.files.return_value.create.assert_not_called()

    def test_campaign_folder_tree_is_created_once_and_only_campaign_folder_is_shared(self):
        service = Mock()
        service.files.return_value.list.return_value.execute.return_value = {'files': []}
        service.files.return_value.create.return_value.execute.side_effect = [
            {'id': 'piges-root'},
            {'id': 'client-folder'},
            {'id': 'reservation-folder'},
        ]
        service.permissions.return_value.list.return_value.execute.return_value = {'permissions': []}
        service.permissions.return_value.create.return_value.execute.return_value = {'id': 'public-reader'}
        service.files.return_value.get.return_value.execute.return_value = {
            'webViewLink': 'https://drive.google.com/drive/folders/reservation-folder',
        }

        folder = get_or_create_campaign_folder(
            service,
            "Client O'Brien",
            'RES-2026-ABC123',
        )

        self.assertEqual(folder.folder_id, 'reservation-folder')
        self.assertEqual(
            folder.web_view_link,
            'https://drive.google.com/drive/folders/reservation-folder',
        )
        self.assertEqual(service.files.return_value.create.call_count, 3)
        permission_call = service.permissions.return_value.create.call_args
        self.assertEqual(permission_call.kwargs['fileId'], 'reservation-folder')
        self.assertEqual(
            permission_call.kwargs['body'],
            {'type': 'anyone', 'role': 'reader', 'allowFileDiscovery': False},
        )
        self.assertEqual(
            service.files.return_value.create.call_args_list[0].kwargs['body']['parents'],
            ['root'],
        )
        self.assertEqual(
            service.files.return_value.create.call_args_list[1].kwargs['body']['parents'],
            ['piges-root'],
        )
        self.assertEqual(
            service.files.return_value.create.call_args_list[2].kwargs['body']['parents'],
            ['client-folder'],
        )

    def test_existing_reservation_link_reuses_its_folder(self):
        service = Mock()
        service.files.return_value.get.return_value.execute.return_value = {
            'id': 'same-folder-id',
            'name': 'RES-2026-ABC123',
            'mimeType': 'application/vnd.google-apps.folder',
        }
        service.permissions.return_value.list.return_value.execute.return_value = {
            'permissions': [{'id': 'public-reader', 'type': 'anyone', 'role': 'reader'}],
        }
        existing_link = 'https://drive.google.com/drive/folders/same-folder-id'

        folder = get_or_create_campaign_folder(
            service,
            'Renamed Client',
            'RES-2026-ABC123',
            existing_link,
        )

        self.assertEqual(folder.folder_id, 'same-folder-id')
        self.assertEqual(folder.web_view_link, existing_link)
        service.files.return_value.create.assert_not_called()
        service.permissions.return_value.create.assert_not_called()

    def test_photo_filename_uses_city_support_date_and_sequence(self):
        prefix = build_photo_prefix(
            "Bobo-Dioulasso",
            "Panneau d'O'Brien",
            date(2026, 10, 19),
        )

        self.assertEqual(prefix, 'Bobo-Dioulasso_Panneau_d_O_Brien_2026-10-19')
        self.assertEqual(build_photo_filename(prefix, 2), f'{prefix}_2.jpg')

    def test_photo_is_resized_and_converted_to_jpeg(self):
        prepared = prepare_photo(make_image(size=(2500, 1000)))

        with Image.open(prepared) as image:
            self.assertEqual(image.format, 'JPEG')
            self.assertEqual(image.size, (2000, 800))


class DriveErrorTests(SimpleTestCase):
    def test_expired_refresh_token_has_a_clear_recovery_message(self):
        credentials = {
            'GOOGLE_CLIENT_ID': 'client-id',
            'GOOGLE_CLIENT_SECRET': 'client-secret',
            'GOOGLE_REFRESH_TOKEN': 'expired-token',
        }
        with (
            patch.dict(os.environ, credentials),
            patch(
                'piges.drive_piges.Credentials.refresh',
                side_effect=RefreshError('invalid_grant: Token has been expired or revoked.'),
            ),
            self.assertRaisesMessage(
                DrivePigesError,
                'Générez un nouveau refresh token OAuth',
            ),
        ):
            get_drive_service()

    @patch('piges.drive_piges.MediaIoBaseDownload')
    def test_drive_photo_download_returns_file_content(self, download_class):
        def make_downloader(output, request):
            output.write(b'image-bytes')
            downloader = Mock()
            downloader.next_chunk.return_value = (None, True)
            return downloader

        download_class.side_effect = make_downloader
        service = Mock()

        content = download_photo(service, 'drive-file-id')

        self.assertEqual(content, b'image-bytes')
        service.files.return_value.get_media.assert_called_once_with(fileId='drive-file-id')

    def test_missing_oauth_configuration_is_reported_without_calling_google(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesMessage(DrivePigesError, 'GOOGLE_CLIENT_ID'):
                get_drive_service()

    def test_storage_quota_error_has_a_clear_message(self):
        error = HttpError(
            resp=Mock(status=403),
            content=b'{"error":{"errors":[{"reason":"storageQuotaExceeded"}]}}',
            uri='https://www.googleapis.com/drive/v3/files',
        )

        with self.assertRaisesMessage(DrivePigesError, 'espace de stockage'):
            raise _http_error(error)

    def test_network_failure_has_a_clear_message(self):
        request = Mock()
        request.execute.side_effect = OSError('network is offline')

        with self.assertRaisesMessage(DrivePigesError, 'momentanément inaccessible'):
            _execute(request)

    def test_delete_photo_calls_drive_and_reports_api_failures(self):
        service = Mock()

        delete_photo(service, 'drive-file-id')

        service.files.return_value.delete.assert_called_once_with(fileId='drive-file-id')
        service.files.return_value.delete.return_value.execute.assert_called_once_with()

        service.files.return_value.delete.return_value.execute.side_effect = OSError('offline')
        with self.assertRaisesMessage(DrivePigesError, 'momentanément inaccessible'):
            delete_photo(service, 'drive-file-id')


class PigesFixturesMixin:
    def setUp(self):
        super().setUp()
        self.staff_user = User.objects.create_user(
            username='staff-piges',
            password='password',
            role=User.ROLE_STAFF,
        )
        self.customer = CampaignClient.objects.create(
            nom='Client test',
            telephone='70000000',
            email='client@example.test',
        )
        self.support = Support.objects.create(
            code='SUP-PIGES-1',
            nom='Panneau Centre',
            type_support='panneau',
            latitude='12.3714',
            longitude='-1.5197',
            ville='Ouagadougou',
        )
        self.face = FacePanneau.objects.create(support=self.support, label='A')
        start = timezone.localdate() + timedelta(days=1)
        self.campagne = Campagne.objects.create(
            nom='Campagne de test',
            client=self.customer,
            date_debut=start,
            date_fin=start + timedelta(days=10),
        )
        self.ligne = LigneCampagne.objects.create(
            campagne=self.campagne,
            support=self.support,
            face=self.face,
        )


class PigesUploadFormTests(PigesFixturesMixin, TestCase):
    def form_for(self, files):
        return PigesUploadForm(
            data={
                'ligne': str(self.ligne.pk),
                'date_prise': '2026-10-19',
            },
            files={'photos': files},
            campagne=self.campagne,
        )

    def test_valid_png_upload_passes_server_validation(self):
        form = self.form_for([make_image('preuve.png')])

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(len(form.cleaned_data['photos']), 1)

    def test_rejects_unsupported_extension(self):
        form = self.form_for([
            SimpleUploadedFile('preuve.gif', b'not-an-image', content_type='image/gif'),
        ])

        self.assertFalse(form.is_valid())
        self.assertIn('Formats acceptés', str(form.errors))

    def test_rejects_file_over_ten_megabytes(self):
        form = self.form_for([
            SimpleUploadedFile(
                'preuve.jpg',
                b'x' * (MAX_IMAGE_SIZE + 1),
                content_type='image/jpeg',
            ),
        ])

        self.assertFalse(form.is_valid())
        self.assertIn('10 Mo maximum', str(form.errors))

    def test_rejects_more_than_twenty_files(self):
        form = self.form_for([
            make_image(f'preuve-{index}.png') for index in range(MAX_FILES_PER_UPLOAD + 1)
        ])

        self.assertFalse(form.is_valid())
        self.assertIn('maximum 20 photos', str(form.errors))

    def test_rejects_file_with_invalid_image_content(self):
        form = self.form_for([
            SimpleUploadedFile('preuve.jpg', b'not-an-image', content_type='image/jpeg'),
        ])

        self.assertFalse(form.is_valid())
        self.assertIn("Impossible de lire l'image", form.errors.as_data()['photos'][0].message)

    def test_form_excludes_previously_photographed_line_and_other_faces_of_same_support(self):
        other_face = FacePanneau.objects.create(support=self.support, label='B')
        other_face_line = LigneCampagne.objects.create(
            campagne=self.campagne,
            support=self.support,
            face=other_face,
        )
        other_support = Support.objects.create(
            code='SUP-PIGES-2',
            nom='Panneau Sud',
            type_support='panneau',
            latitude='12.3000',
            longitude='-1.5000',
            ville='Ouagadougou',
        )
        other_support_line = LigneCampagne.objects.create(
            campagne=self.campagne,
            support=other_support,
            face=FacePanneau.objects.create(support=other_support, label='A'),
        )
        PigeFichier.objects.create(
            campagne=self.campagne,
            ligne=self.ligne,
            support=self.support,
            support_nom=self.support.nom,
            date_prise=timezone.localdate(),
            nom_fichier='preuve-existante.jpg',
            drive_file_id='drive-covered-line',
        )

        form = PigesUploadForm(campagne=self.campagne)
        selectable_line_ids = set(form.fields['ligne'].queryset.values_list('pk', flat=True))

        self.assertNotIn(self.ligne.pk, selectable_line_ids)
        self.assertNotIn(other_face_line.pk, selectable_line_ids)
        self.assertIn(other_support_line.pk, selectable_line_ids)


class PigesUploadViewTests(PigesFixturesMixin, TestCase):
    def test_upload_page_contains_accessible_progress_indicator(self):
        self.client.force_login(self.staff_user)

        response = self.client.get(reverse('piges:upload', args=[self.campagne.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="upload-progress"')
        self.assertContains(response, "Progression de l'envoi des photos", html=False)
        self.assertContains(response, 'Transfert des photos vers le serveur')
        self.assertContains(response, 'Google Drive en cours')

    def create_demande(self, **kwargs):
        today = timezone.localdate()
        return DemandeReservation.objects.create(
            nom_contact='Contact Test',
            email='client@example.test',
            telephone='70000000',
            date_debut_souhaitee=today,
            date_fin_souhaitee=today + timedelta(days=10),
            client=self.customer,
            **kwargs,
        )

    def test_upload_saves_drive_link_and_file_metadata_on_campaign(self):
        self.client.force_login(self.staff_user)
        image = make_image('preuve.png')
        folder = type(
            'FolderResult',
            (),
            {
                'folder_id': 'drive-folder-id',
                'web_view_link': 'https://drive.google.com/drive/folders/drive-folder-id',
            },
        )()

        with (
            patch('piges.views.get_drive_service'),
            patch('piges.views.get_or_create_campaign_folder', return_value=folder),
            patch('piges.views.get_next_photo_number', return_value=1),
            patch('piges.views.upload_photo', return_value='drive-file-id'),
        ):
            response = self.client.post(
                reverse('piges:upload', args=[self.campagne.pk]),
                {
                    'ligne': str(self.ligne.pk),
                    'date_prise': '2026-10-19',
                    'photos': [image],
                },
            )

        self.assertEqual(response.status_code, 302)
        self.campagne.refresh_from_db()
        self.assertEqual(
            self.campagne.lien_piges,
            'https://drive.google.com/drive/folders/drive-folder-id',
        )
        fichier = PigeFichier.objects.get(campagne=self.campagne)
        self.assertEqual(fichier.ligne, self.ligne)
        self.assertEqual(fichier.drive_file_id, 'drive-file-id')
        self.assertEqual(fichier.nom_fichier, 'Ouagadougou_Panneau_Centre_2026-10-19_1.jpg')
        self.assertEqual(fichier.ajoute_par, self.staff_user)

    def test_upload_accepts_multiple_photos_for_one_line(self):
        self.client.force_login(self.staff_user)
        folder = type(
            'FolderResult',
            (),
            {
                'folder_id': 'drive-folder-multi',
                'web_view_link': 'https://drive.google.com/drive/folders/drive-folder-multi',
            },
        )()
        images = [make_image('preuve-1.png'), make_image('preuve-2.png')]
        with (
            patch('piges.views.get_drive_service'),
            patch('piges.views.get_or_create_campaign_folder', return_value=folder),
            patch('piges.views.get_next_photo_number', side_effect=[1, 2]),
            patch('piges.views.upload_photo', side_effect=['drive-multi-1', 'drive-multi-2']),
        ):
            response = self.client.post(
                reverse('piges:upload', args=[self.campagne.pk]),
                {
                    'ligne': str(self.ligne.pk),
                    'date_prise': '2026-10-19',
                    'photos': images,
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            PigeFichier.objects.filter(campagne=self.campagne, ligne=self.ligne).count(),
            2,
        )
        self.assertEqual(
            set(PigeFichier.objects.filter(ligne=self.ligne).values_list('drive_file_id', flat=True)),
            {'drive-multi-1', 'drive-multi-2'},
        )

    def test_upload_view_is_for_staff_only(self):
        non_staff_user = User.objects.create_user(
            username='technicien-piges',
            password='password',
            role=User.ROLE_TECHNICIEN,
        )
        self.client.force_login(non_staff_user)

        response = self.client.get(reverse('piges:upload', args=[self.campagne.pk]))

        self.assertEqual(response.status_code, 403)

    def test_upload_post_requires_csrf_token(self):
        csrf_client = DjangoClient(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff_user)

        response = csrf_client.post(
            reverse('piges:upload', args=[self.campagne.pk]),
            {},
        )

        self.assertEqual(response.status_code, 403)

    def test_staff_can_edit_drive_pige_date_metadata(self):
        self.client.force_login(self.staff_user)
        pige = PigeFichier.objects.create(
            campagne=self.campagne,
            ligne=self.ligne,
            support=self.support,
            support_nom=self.support.nom,
            date_prise=date(2026, 10, 19),
            nom_fichier='photo-originale.jpg',
            drive_file_id='drive-edit-date',
        )

        response = self.client.post(
            reverse('piges:photo_update', args=[self.campagne.pk, pige.pk]),
            {'date_prise': '2026-10-20'},
        )

        self.assertEqual(response.status_code, 302)
        pige.refresh_from_db()
        self.assertEqual(pige.date_prise, date(2026, 10, 20))
        self.assertEqual(pige.nom_fichier, 'photo-originale.jpg')

    def test_drive_pige_edit_is_limited_to_staff_and_owning_campaign(self):
        pige = PigeFichier.objects.create(
            campagne=self.campagne,
            ligne=self.ligne,
            support=self.support,
            support_nom=self.support.nom,
            date_prise=timezone.localdate(),
            nom_fichier='photo-protegee.jpg',
            drive_file_id='drive-edit-permission',
        )
        technician = User.objects.create_user(
            username='technicien-edit-pige',
            password='password',
            role=User.ROLE_TECHNICIEN,
        )
        self.client.force_login(technician)
        response = self.client.get(
            reverse('piges:photo_update', args=[self.campagne.pk, pige.pk]),
        )
        self.assertEqual(response.status_code, 403)

        self.client.force_login(self.staff_user)
        other_campaign = Campagne.objects.create(
            nom='Autre campagne',
            client=self.customer,
            date_debut=self.campagne.date_debut,
            date_fin=self.campagne.date_fin,
        )
        response = self.client.get(
            reverse('piges:photo_update', args=[other_campaign.pk, pige.pk]),
        )
        self.assertEqual(response.status_code, 404)

    def test_drive_pige_edit_post_requires_csrf_token(self):
        pige = PigeFichier.objects.create(
            campagne=self.campagne,
            ligne=self.ligne,
            support=self.support,
            support_nom=self.support.nom,
            date_prise=date(2026, 10, 19),
            nom_fichier='photo-edit-csrf.jpg',
            drive_file_id='drive-edit-csrf',
        )
        csrf_client = DjangoClient(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff_user)

        response = csrf_client.post(
            reverse('piges:photo_update', args=[self.campagne.pk, pige.pk]),
            {'date_prise': '2026-10-20'},
        )

        self.assertEqual(response.status_code, 403)
        pige.refresh_from_db()
        self.assertEqual(pige.date_prise, date(2026, 10, 19))

    def test_delete_drive_pige_removes_drive_file_and_metadata(self):
        self.client.force_login(self.staff_user)
        pige = PigeFichier.objects.create(
            campagne=self.campagne,
            ligne=self.ligne,
            support=self.support,
            support_nom=self.support.nom,
            date_prise=timezone.localdate(),
            nom_fichier='photo-a-supprimer.jpg',
            drive_file_id='drive-delete-success',
        )
        with (
            patch('piges.views.get_drive_service') as get_service,
            patch('piges.views.delete_photo') as delete,
        ):
            response = self.client.post(
                reverse('piges:photo_delete', args=[self.campagne.pk, pige.pk]),
            )

        self.assertEqual(response.status_code, 302)
        get_service.assert_called_once_with()
        delete.assert_called_once_with(get_service.return_value, 'drive-delete-success')
        self.assertFalse(PigeFichier.objects.filter(pk=pige.pk).exists())

    def test_failed_drive_delete_keeps_metadata_and_reports_error(self):
        self.client.force_login(self.staff_user)
        pige = PigeFichier.objects.create(
            campagne=self.campagne,
            ligne=self.ligne,
            support=self.support,
            support_nom=self.support.nom,
            date_prise=timezone.localdate(),
            nom_fichier='photo-conservee.jpg',
            drive_file_id='drive-delete-failure',
        )
        with (
            patch('piges.views.get_drive_service'),
            patch(
                'piges.views.delete_photo',
                side_effect=DrivePigesError('Google Drive est momentanément inaccessible.'),
            ),
        ):
            response = self.client.post(
                reverse('piges:photo_delete', args=[self.campagne.pk, pige.pk]),
            )

        self.assertEqual(response.status_code, 503)
        self.assertContains(response, 'La photo n’a pas été supprimée', status_code=503)
        self.assertTrue(PigeFichier.objects.filter(pk=pige.pk).exists())

    def test_drive_pige_delete_post_requires_csrf_token(self):
        pige = PigeFichier.objects.create(
            campagne=self.campagne,
            ligne=self.ligne,
            support=self.support,
            support_nom=self.support.nom,
            date_prise=timezone.localdate(),
            nom_fichier='photo-csrf.jpg',
            drive_file_id='drive-delete-csrf',
        )
        csrf_client = DjangoClient(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff_user)

        response = csrf_client.post(
            reverse('piges:photo_delete', args=[self.campagne.pk, pige.pk]),
        )

        self.assertEqual(response.status_code, 403)
        self.assertTrue(PigeFichier.objects.filter(pk=pige.pk).exists())

    def test_campaign_detail_shows_drive_link_only_when_it_exists(self):
        self.client.force_login(self.staff_user)
        PigeFichier.objects.create(
            campagne=self.campagne,
            ligne=self.ligne,
            support=self.support,
            support_nom=self.support.nom,
            ville=self.support.ville,
            date_prise=timezone.localdate(),
            nom_fichier='preuve-drive.jpg',
            drive_file_id='drive-file-detail-render-test',
        )
        url = reverse('campagne_detail', args=[self.campagne.pk])
        response_without_link = self.client.get(url)

        self.assertNotContains(response_without_link, 'Voir et télécharger les piges sur Drive')
        self.assertContains(response_without_link, 'preuve-drive.jpg')
        self.assertContains(response_without_link, 'drive.google.com/thumbnail?id=drive-file-detail-render-test')

        self.campagne.lien_piges = 'https://drive.google.com/drive/folders/example'
        self.campagne.save(update_fields=['lien_piges'])

        response_with_link = self.client.get(url)

        self.assertContains(response_with_link, 'Voir et télécharger les piges sur Drive')
        self.assertContains(
            response_with_link,
            'https://drive.google.com/drive/folders/example',
        )

    def test_staff_can_associate_a_campaign_to_a_followup_request(self):
        demande = self.create_demande()
        self.client.force_login(self.staff_user)

        response = self.client.post(
            reverse('demande_associer_campagne', args=[demande.uuid]),
            {'campagne': str(self.campagne.pk)},
        )

        self.assertEqual(response.status_code, 302)
        demande.refresh_from_db()
        self.assertEqual(demande.campagne, self.campagne)

    def test_staff_cannot_associate_a_campaign_from_another_client(self):
        demande = self.create_demande()
        other_client = CampaignClient.objects.create(
            nom='Autre client',
            telephone='71000000',
            email='autre@example.test',
        )
        other_campaign = Campagne.objects.create(
            nom='Campagne autre client',
            client=other_client,
            date_debut=self.campagne.date_debut,
            date_fin=self.campagne.date_fin,
        )
        self.client.force_login(self.staff_user)

        response = self.client.post(
            reverse('demande_associer_campagne', args=[demande.uuid]),
            {'campagne': str(other_campaign.pk)},
        )

        self.assertEqual(response.status_code, 302)
        demande.refresh_from_db()
        self.assertIsNone(demande.campagne)

    def test_only_staff_can_associate_a_campaign(self):
        demande = self.create_demande()
        technician = User.objects.create_user(
            username='technicien-association',
            password='password',
            role=User.ROLE_TECHNICIEN,
        )
        self.client.force_login(technician)

        response = self.client.post(
            reverse('demande_associer_campagne', args=[demande.uuid]),
            {'campagne': str(self.campagne.pk)},
        )

        self.assertEqual(response.status_code, 403)
        demande.refresh_from_db()
        self.assertIsNone(demande.campagne)

    def test_followup_shows_drive_link_only_for_its_associated_campaign(self):
        demande = self.create_demande(campagne=self.campagne)
        url = reverse('portail:confirmation', kwargs={'uuid': demande.uuid})

        response_without_link = self.client.get(url)
        self.assertNotContains(response_without_link, 'Voir et télécharger les piges')

        self.campagne.lien_piges = 'https://drive.google.com/drive/folders/followup'
        self.campagne.save(update_fields=['lien_piges'])
        response_with_link = self.client.get(url)

        self.assertContains(response_with_link, 'Voir et télécharger les piges')
        self.assertContains(
            response_with_link,
            'https://drive.google.com/drive/folders/followup',
        )
