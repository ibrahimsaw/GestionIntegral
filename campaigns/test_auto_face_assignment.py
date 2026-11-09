import datetime
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from campaigns.models import (
    Campagne,
    Client,
    LigneCampagne,
    Reservation,
    ReservationLigne,
    STATUT_CONFIRMEE,
)
from inventory.models import FacePanneau, FormatSupport, Support


class AutomaticCampaignFaceAssignmentTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username='auto-face-campaign-staff',
            password='test-password',
            role='staff',
        )
        self.client.force_login(user)
        self.agency = Client.objects.create(
            nom='Agence sélection automatique',
            telephone='70000000',
            email='auto-face-campaign@example.com',
        )
        self.today = timezone.localdate()
        self.start = self.today + timedelta(days=10)
        self.end = self.start + timedelta(days=4)
        self.format_12m = FormatSupport.objects.create(
            code='AUTO-FACE-12',
            categorie='12m²',
            dimensions='4m x 3m',
        )
        self.format_giant = FormatSupport.objects.create(
            code='AUTO-FACE-GIANT',
            categorie='Géant',
            dimensions='8m x 6m',
        )

    def create_face(self, code, format_code, city='Ouagadougou'):
        support = Support.objects.create(
            code=code,
            nom=f'Support {code}',
            type_support=Support.TYPE_PANNEAU,
            format=format_code,
            latitude=12.34,
            longitude=-1.52,
            ville=city,
        )
        return FacePanneau.objects.create(support=support, label='A')

    def post_campaign(self, face_count):
        return self.client.post(
            reverse('campagne_create'),
            {
                'nom': 'Campagne historique',
                'type_support': '12m²',
                'lieu': 'Ouagadougou',
                'date_debut': self.start.isoformat(),
                'date_fin': self.end.isoformat(),
                'statut': Campagne.STATUT_A_VENIR,
                'nombre_visuels': 1,
                'nombre_faces_temporaire': face_count,
            },
        )

    def test_temporary_face_count_is_visible_on_campaign_creation_form(self):
        response = self.client.get(reverse('campagne_create'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="auto-face-count-group"')
        self.assertContains(response, 'name="nombre_faces_temporaire"')

    def test_selects_only_available_faces_matching_category_city_and_period(self):
        unavailable_by_campaign = self.create_face(
            'AUTO-FACE-CAMPAIGN-BLOCK',
            self.format_12m.code,
        )
        unavailable_by_reservation = self.create_face(
            'AUTO-FACE-RESERVATION-BLOCK',
            self.format_12m.code,
        )
        expected_face = self.create_face(
            'AUTO-FACE-AVAILABLE',
            self.format_12m.code,
        )
        another_expected_face = self.create_face(
            'AUTO-FACE-AVAILABLE-2',
            self.format_12m.code,
        )
        third_expected_face = self.create_face(
            'AUTO-FACE-AVAILABLE-3',
            self.format_12m.code,
        )
        self.create_face(
            'AUTO-FACE-WRONG-CITY',
            self.format_12m.code,
            city='Bobo-Dioulasso',
        )
        self.create_face(
            'AUTO-FACE-WRONG-CATEGORY',
            self.format_giant.code,
        )

        occupying_campaign = Campagne.objects.create(
            client=self.agency,
            nom='Campagne déjà planifiée',
            type_support='12m²',
            date_debut=self.start,
            date_fin=self.end,
            statut=Campagne.STATUT_A_VENIR,
        )
        LigneCampagne.objects.create(
            campagne=occupying_campaign,
            support=unavailable_by_campaign.support,
            face=unavailable_by_campaign,
        )
        reservation = Reservation.objects.create(
            nom='Réservation existante',
            client=self.agency,
            date_debut=timezone.make_aware(
                datetime.datetime.combine(self.start, datetime.time.min)
            ),
            date_fin=timezone.make_aware(
                datetime.datetime.combine(self.end, datetime.time.max)
            ),
            statut=STATUT_CONFIRMEE,
        )
        ReservationLigne.objects.create(
            reservation=reservation,
            support=unavailable_by_reservation.support,
            face=unavailable_by_reservation,
        )

        response = self.post_campaign(2)

        self.assertEqual(response.status_code, 302)
        campaign = Campagne.objects.get(nom='Campagne historique')
        self.assertRedirects(response, reverse('campagne_detail', args=[campaign.pk]))
        selected_face_ids = set(
            campaign.lignes.values_list('face_id', flat=True)
        )
        self.assertEqual(len(selected_face_ids), 2)
        self.assertTrue(selected_face_ids.issubset({
            expected_face.pk,
            another_expected_face.pk,
            third_expected_face.pk,
        }))
        self.assertNotIn(
            'nombre_faces_temporaire',
            {field.name for field in Campagne._meta.fields},
        )

    def test_rejects_a_count_larger_than_matching_available_faces(self):
        self.create_face('AUTO-FACE-ONLY-AVAILABLE', self.format_12m.code)

        response = self.post_campaign(2)

        self.assertEqual(response.status_code, 200)
        self.assertIn('nombre_faces_temporaire', response.context['form'].errors)
        self.assertFalse(Campagne.objects.filter(nom='Campagne historique').exists())

    def test_past_period_excludes_faces_occupied_by_completed_campaigns(self):
        occupied_face = self.create_face(
            'AUTO-FACE-PAST-OCCUPIED',
            self.format_12m.code,
        )
        available_face = self.create_face(
            'AUTO-FACE-PAST-AVAILABLE',
            self.format_12m.code,
        )
        past_start = self.today - timedelta(days=30)
        past_end = past_start + timedelta(days=4)
        old_campaign = Campagne.objects.create(
            client=self.agency,
            nom='Ancienne campagne déjà terminée',
            type_support='12m²',
            date_debut=past_start,
            date_fin=past_end,
            statut=Campagne.STATUT_TERMINEE,
        )
        LigneCampagne.objects.create(
            campagne=old_campaign,
            support=occupied_face.support,
            face=occupied_face,
        )

        response = self.client.post(
            reverse('campagne_create'),
            {
                'nom': 'Campagne historique ajoutée',
                'type_support': '12m²',
                'lieu': 'Ouagadougou',
                'date_debut': past_start.isoformat(),
                'date_fin': past_end.isoformat(),
                'statut': Campagne.STATUT_TERMINEE,
                'nombre_visuels': 1,
                'nombre_faces_temporaire': 1,
            },
        )

        self.assertEqual(response.status_code, 302)
        campaign = Campagne.objects.get(nom='Campagne historique ajoutée')
        self.assertEqual(
            list(campaign.lignes.values_list('face_id', flat=True)),
            [available_face.pk],
        )
