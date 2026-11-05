import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from campaigns.models import (
    Client,
    Reservation,
    STATUT_ANNULEE,
    STATUT_CONFIRMEE,
    STATUT_EN_ATTENTE,
    STATUT_EXPIREE,
)


class ReservationListStatusTabsTests(TestCase):
    def setUp(self):
        staff = get_user_model().objects.create_user(
            username='reservation-list-staff',
            password='test-password',
            role='staff',
        )
        self.client.force_login(staff)
        client_obj = Client.objects.create(
            nom='Client test réservations',
            telephone='70000000',
            email='reservations@example.com',
        )
        start = timezone.now() + datetime.timedelta(days=2)
        statuses = (
            STATUT_EN_ATTENTE,
            STATUT_CONFIRMEE,
            STATUT_ANNULEE,
            STATUT_EXPIREE,
        )
        for status in statuses:
            Reservation.objects.create(
                nom=f'Réservation {status}',
                client=client_obj,
                date_debut=start,
                date_fin=start + datetime.timedelta(days=5),
                statut=status,
            )

    def test_default_tab_shows_pending_and_confirmed_reservations(self):
        response = self.client.get(reverse('reservation_list'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['reservations']), 2)
        self.assertEqual(response.context['statut_counts']['a_suivre'], 2)
        self.assertContains(response, 'À suivre')
        self.assertContains(response, 'Expirées')

    def test_status_tab_filters_reservations(self):
        response = self.client.get(reverse('reservation_list'), {'statut': STATUT_ANNULEE})

        self.assertEqual(response.status_code, 200)
        reservations = response.context['reservations']
        self.assertEqual(len(reservations), 1)
        self.assertEqual(reservations[0].statut, STATUT_ANNULEE)

    def test_client_filter_only_lists_clients_with_reservations(self):
        Client.objects.create(
            nom='Client sans réservation',
            telephone='71111111',
            email='sans-reservation@example.com',
        )

        response = self.client.get(reverse('reservation_list'))

        self.assertEqual(response.status_code, 200)
        clients = list(response.context['clients'])
        self.assertEqual(len(clients), 1)
        self.assertEqual(clients[0].nom, 'Client test réservations')
