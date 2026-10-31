import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from campaigns.models import Campagne, CampagnePause, Client, LigneCampagne
from inventory.models import Support


class CampaignArchiveActionsTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(
            nom='Client archivage test',
            telephone='00000000',
            email='archive-test@example.com',
        )
        self.campagne = Campagne.objects.create(
            client=self.client_obj,
            nom='Campagne archivage test',
            date_debut=datetime.date(2026, 1, 1),
            date_fin=datetime.date(2026, 12, 31),
            actif=False,
        )
        staff = get_user_model().objects.create_user(
            username='campaign-archive-staff',
            password='test-password',
            role='staff',
        )
        self.client.force_login(staff)

    def test_detail_action_restores_archived_campaign(self):
        response = self.client.post(
            reverse('campagne_archive_toggle', args=[self.campagne.pk]),
            {'action': 'desarchiver'},
        )

        self.campagne.refresh_from_db()
        self.assertTrue(self.campagne.actif)
        self.assertRedirects(
            response,
            reverse('campagne_detail', args=[self.campagne.pk]),
        )

    def test_detail_action_archives_active_campaign(self):
        self.campagne.actif = True
        self.campagne.save(update_fields=['actif'])

        response = self.client.post(
            reverse('campagne_archive_toggle', args=[self.campagne.pk]),
            {'action': 'archiver'},
        )

        self.campagne.refresh_from_db()
        self.assertFalse(self.campagne.actif)
        self.assertRedirects(
            response,
            reverse('campagne_detail', args=[self.campagne.pk]),
        )

    def test_bulk_action_restores_archived_campaign(self):
        response = self.client.post(
            reverse('campagne_list'),
            {
                'action': 'desarchiver',
                'selected_ids': [self.campagne.pk],
            },
        )

        self.campagne.refresh_from_db()
        self.assertTrue(self.campagne.actif)
        self.assertRedirects(response, reverse('campagne_list'))


class CampaignPauseAndCancellationTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        client_obj = Client.objects.create(
            nom='Client cycle de vie test',
            telephone='00000001',
            email='campaign-lifecycle@example.com',
        )
        self.campagne = Campagne.objects.create(
            client=client_obj,
            nom='Campagne cycle de vie test',
            type_support='ecran',
            date_debut=self.today - datetime.timedelta(days=5),
            date_fin=self.today + datetime.timedelta(days=10),
            statut=Campagne.STATUT_EN_COURS,
            duree_passage=20,
            frequence=3600,
            tranches_horaires='08:00-09:00',
        )
        support = Support.objects.create(
            code='LIFECYCLE-TEST',
            nom='Écran de test',
            type_support=Support.TYPE_ECRAN,
            latitude=12.3456789,
            longitude=-1.2345678,
        )
        self.support = support
        self.ligne = LigneCampagne.objects.create(
            campagne=self.campagne,
            support=support,
        )
        staff = get_user_model().objects.create_user(
            username='campaign-lifecycle-staff',
            password='test-password',
            role='staff',
        )
        self.client.force_login(staff)

    def test_detail_shows_state_actions_inside_one_modal(self):
        response = self.client.get(
            reverse('campagne_detail', args=[self.campagne.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="modalGestionCampagne"')
        self.assertContains(response, 'value="mettre_en_pause"')
        self.assertContains(response, 'value="annuler"')
        self.assertNotContains(response, 'Mettre en pause</span>')

    def _create_pause(self, days=2):
        date_pause = self.today - datetime.timedelta(days=days)
        self.campagne.statut = Campagne.STATUT_EN_PAUSE
        self.campagne.date_mise_en_pause = date_pause
        self.campagne.save(update_fields=['statut', 'date_mise_en_pause'])
        return CampagnePause.objects.create(
            campagne=self.campagne,
            date_debut=date_pause,
        )

    def test_paused_campaign_keeps_support_and_auto_status(self):
        response = self.client.post(
            reverse('campagne_state_change', args=[self.campagne.pk]),
            {'action': 'mettre_en_pause'},
        )

        self.campagne.refresh_from_db()
        self.assertRedirects(
            response, reverse('campagne_detail', args=[self.campagne.pk])
        )
        self.assertEqual(self.campagne.statut, Campagne.STATUT_EN_PAUSE)
        self.assertTrue(self.support.is_occupe())
        self.assertEqual(
            self.ligne.calculer_spots_sur_periode(
                self.campagne.date_debut,
                self.campagne.date_fin,
            ),
            5,
        )
        self.campagne.date_fin = self.today - datetime.timedelta(days=1)
        self.campagne.save(update_fields=['date_fin'])
        self.assertTrue(self.support.is_occupe())
        self.campagne.auto_update_statut()
        self.campagne.refresh_from_db()
        self.assertEqual(self.campagne.statut, Campagne.STATUT_EN_PAUSE)

    def test_cancel_stops_campaign_and_releases_support(self):
        response = self.client.post(
            reverse('campagne_state_change', args=[self.campagne.pk]),
            {'action': 'annuler'},
        )

        self.campagne.refresh_from_db()
        self.assertRedirects(
            response, reverse('campagne_detail', args=[self.campagne.pk])
        )
        self.assertEqual(self.campagne.statut, Campagne.STATUT_ANNULEE)
        self.assertEqual(self.campagne.date_annulation, self.today)
        self.assertEqual(
            self.campagne.date_fin, self.today - datetime.timedelta(days=1)
        )
        self.assertFalse(self.support.is_occupe())

    def test_resume_can_extend_end_date_by_pause_duration(self):
        pause = self._create_pause(days=2)
        ancienne_fin = self.campagne.date_fin

        self.client.post(
            reverse('campagne_state_change', args=[self.campagne.pk]),
            {'action': 'reprendre_prolonger'},
        )

        self.campagne.refresh_from_db()
        pause.refresh_from_db()
        self.assertEqual(
            self.campagne.date_fin, ancienne_fin + datetime.timedelta(days=2)
        )
        self.assertEqual(self.campagne.statut, Campagne.STATUT_EN_COURS)
        self.assertEqual(
            pause.date_fin, self.today - datetime.timedelta(days=1)
        )

    def test_resume_can_keep_original_end_date(self):
        pause = self._create_pause(days=2)
        ancienne_fin = self.campagne.date_fin

        self.client.post(
            reverse('campagne_state_change', args=[self.campagne.pk]),
            {'action': 'reprendre_garder'},
        )

        self.campagne.refresh_from_db()
        self.assertEqual(self.campagne.date_fin, ancienne_fin)
        self.assertEqual(self.campagne.statut, Campagne.STATUT_EN_COURS)
        self.assertIsNotNone(pause.__class__.objects.get(pk=pause.pk).date_fin)

    def test_screen_spot_calculation_excludes_paused_days(self):
        pause = self._create_pause(days=2)
        pause.date_fin = self.today - datetime.timedelta(days=1)
        pause.save(update_fields=['date_fin'])

        spots = self.ligne.calculer_spots_sur_periode(
            self.campagne.date_debut,
            self.campagne.date_fin,
        )

        self.assertEqual(spots, 14)

    def test_cancelled_campaign_cannot_be_reactivated(self):
        self.client.post(
            reverse('campagne_state_change', args=[self.campagne.pk]),
            {'action': 'annuler'},
        )

        response = self.client.post(
            reverse('campagne_state_change', args=[self.campagne.pk]),
            {'action': 'mettre_en_pause'},
        )

        self.campagne.refresh_from_db()
        self.assertEqual(self.campagne.statut, Campagne.STATUT_ANNULEE)
        self.assertRedirects(
            response, reverse('campagne_detail', args=[self.campagne.pk])
        )
