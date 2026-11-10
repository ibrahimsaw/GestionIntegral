import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from campaigns.models import Client
from inventory.models import FacePanneau, Support


class ReservationWizardStepsTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(
            nom='Client réservation étapes',
            telephone='00000000',
            email='reservation-steps@example.com',
        )
        staff = get_user_model().objects.create_user(
            username='reservation-steps-staff',
            password='test-password',
            role='staff',
        )
        self.client.force_login(staff)

    def test_client_and_face_selection_are_presented_as_first_step(self):
        response = self.client.get(reverse('reservation_wizard_etape1'))
        self.assertRedirects(response, reverse('reservation_wizard_etape2'))

        response = self.client.get(reverse('reservation_wizard_etape2'))
        self.assertContains(response, 'Étape 1 sur 2')
        self.assertContains(response, 'Sélectionnez le client de la réservation')
        self.assertContains(response, 'Période de diffusion')
        self.assertContains(response, 'Supports disponibles')

        response = self.client.post(
            reverse('reservation_wizard_etape2'),
            {'action': 'selectionner', 'client_pk': self.client_obj.pk},
        )
        self.assertRedirects(response, reverse('reservation_wizard_etape2'))

        response = self.client.get(reverse('reservation_wizard_etape2'))
        self.assertContains(response, 'Étape 1 sur 2')
        self.assertContains(response, 'Supports disponibles')
        self.assertContains(response, 'Période de diffusion')
        self.assertContains(response, self.client_obj.nom)

    def test_review_and_confirmation_are_presented_as_second_step(self):
        support = Support.objects.create(
            code='RESA-STEP-TEST',
            nom='Support réservation étapes',
            type_support=Support.TYPE_PANNEAU,
            latitude=12.34,
            longitude=-1.52,
        )
        face = FacePanneau.objects.create(support=support, label='A')
        session = self.client.session
        session.update({
            'resa_wizard_client_pk': self.client_obj.pk,
            'resa_wizard_dates': {
                'date_debut': '2026-11-11',
                'date_fin': '2026-11-12',
            },
            'resa_wizard_faces': [face.pk],
        })
        session.save()

        response = self.client.get(reverse('reservation_wizard_etape3'))

        self.assertContains(response, 'Étape 2 sur 2')
        self.assertContains(response, 'Confirmer et créer la réservation')
