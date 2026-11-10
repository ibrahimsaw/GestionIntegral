import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from campaigns.models import Campagne, Client
from inventory.models import FacePanneau, FormatSupport, Support


class SupportBulkFormatDisplayTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(
            username='bulk-format-display-staff',
            password='test-password',
            role='staff',
        )
        self.client.force_login(user)
        self.client_obj = Client.objects.create(
            nom='Agence affichage formats',
            telephone='70000000',
            email='bulk-format-display@example.com',
        )

    def create_campaign(self, support_type):
        return Campagne.objects.create(
            client=self.client_obj,
            nom='Campagne de sélection',
            type_support=support_type,
            date_debut=datetime.date(2026, 12, 1),
            date_fin=datetime.date(2026, 12, 31),
            statut=Campagne.STATUT_A_VENIR,
        )

    def create_support(self, code, format_support, quartier=''):
        support = Support.objects.create(
            code=code,
            nom=f'Support {code}',
            type_support=Support.TYPE_PANNEAU,
            format=format_support.code,
            latitude=12.34,
            longitude=-1.52,
            ville='Ouagadougou',
            quartier=quartier,
        )
        FacePanneau.objects.create(support=support, label='A')
        return support

    def test_giant_supports_are_grouped_and_labeled_by_format(self):
        giant_format = FormatSupport.objects.create(
            code='BULK-GIANT-8X6',
            dimensions='8m × 6m',
            categorie='Géant',
        )
        second_giant_format = FormatSupport.objects.create(
            code='BULK-GIANT-10X5',
            dimensions='10m × 5m',
            categorie='Géant',
        )
        self.create_support('BULK-GIANT-SUPPORT-A', giant_format, quartier='Zogona')
        self.create_support('BULK-GIANT-SUPPORT-B', second_giant_format, quartier='Dassasgho')
        campaign = self.create_campaign('Géant')

        response = self.client.get(
            reverse('supports_add_bulk', args=[campaign.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['campagne_categorie_geant'])
        content = response.content.decode()
        format_a = content.index('Format : 8m × 6m — BULK-GIANT-8X6')
        quartier_a = content.index('Quartier : Zogona')
        format_b = content.index('Format : 10m × 5m — BULK-GIANT-10X5')
        quartier_b = content.index('Quartier : Dassasgho')
        self.assertLess(format_b, quartier_b)
        self.assertLess(quartier_b, format_a)
        self.assertLess(format_a, quartier_a)
        self.assertEqual(response.context['panel_view'], 'list')
        self.assertContains(
            response,
            'id="panel-list-view" aria-pressed="true"',
        )

    def test_other_categories_are_not_grouped_by_format(self):
        standard_format = FormatSupport.objects.create(
            code='BULK-STANDARD-4X3',
            dimensions='4m × 3m',
            categorie='Standard',
        )
        self.create_support('BULK-STANDARD-SUPPORT', standard_format)
        campaign = self.create_campaign(standard_format.code)

        response = self.client.get(
            reverse('supports_add_bulk', args=[campaign.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['campagne_categorie_geant'])
        self.assertNotContains(response, 'Format : 4m × 3m')
        self.assertEqual(response.context['panel_view'], 'list')

    def test_requested_grid_view_is_preserved(self):
        standard_format = FormatSupport.objects.create(
            code='BULK-GRID-4X3',
            dimensions='4m × 3m',
            categorie='Standard',
        )
        self.create_support('BULK-GRID-SUPPORT', standard_format)
        campaign = self.create_campaign(standard_format.code)

        response = self.client.get(
            reverse('supports_add_bulk', args=[campaign.pk]),
            {'view': 'grid'},
        )

        self.assertEqual(response.context['panel_view'], 'grid')
        self.assertContains(
            response,
            'id="panel-grid-view" aria-pressed="true"',
        )
