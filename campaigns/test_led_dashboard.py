import calendar
import datetime
from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.urls import reverse

from campaigns.models import (
    Campagne,
    Client,
    Contrat,
    EcranBilanMensuel,
    EcranParametresExploitation,
    LigneCampagne,
    PackageEcran,
)
from campaigns.views import DashboardView
from inventory.models import EcranNumerique, FacePanneau, FormatSupport, Support
from planning.models import LogDiffusion


class LedDashboardDataTests(TestCase):
    def test_dashboard_counts_distinct_supports_for_total_and_occupied_faces(self):
        today = timezone.localdate()
        client = Client.objects.create(
            nom='Client face-count test',
            telephone='00000000',
            email='face-count-test@example.com',
        )
        format_support = FormatSupport.objects.create(
            code='FACE-COUNT-12',
            categorie='12m²',
            dimensions='4m × 3m',
        )
        support = Support.objects.create(
            code='FACE-COUNT-A',
            nom='Support FACE-COUNT-A',
            type_support=Support.TYPE_PANNEAU,
            format=format_support.code,
            latitude=12.34,
            longitude=-1.52,
            ville='Ouagadougou',
        )
        other_support = Support.objects.create(
            code='FACE-COUNT-B',
            nom='Support FACE-COUNT-B',
            type_support=Support.TYPE_PANNEAU,
            format=format_support.code,
            latitude=12.35,
            longitude=-1.53,
            ville='Ouagadougou',
        )
        face_a = FacePanneau.objects.create(support=support, label='A')
        face_b = FacePanneau.objects.create(support=support, label='B')
        FacePanneau.objects.create(support=other_support, label='A')
        campaign = Campagne.objects.create(
            client=client,
            nom='Campagne des faces du jour',
            date_debut=today,
            date_fin=today,
            statut=Campagne.STATUT_EN_COURS,
        )
        LigneCampagne.objects.create(campagne=campaign, face=face_a)
        LigneCampagne.objects.create(campagne=campaign, face=face_b)
        user = get_user_model().objects.create_user(
            username='face-count-dashboard-admin',
            password='test-password',
            role='admin',
        )
        self.client.force_login(user)
        cache.clear()

        response = self.client.get(reverse('dashboard'), {'ville': 'Ouagadougou'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_faces'], 3)
        self.assertEqual(response.context['total_supports'], 2)
        self.assertEqual(response.context['total_faces_occupe'], 2)
        self.assertEqual(response.context['total_supports_occupe'], 1)
        category = response.context['parc_categories'][0]
        self.assertEqual(category['nb_faces'], 3)
        self.assertEqual(category['nb_supports'], 2)
        self.assertEqual(category['nb_occupe'], 2)
        self.assertEqual(category['nb_supports_occupe'], 1)
        self.assertContains(response, '3 <small>faces</small> / 2 <small>supports</small>')
        self.assertContains(response, '2 <small>faces</small> / 1 <small>supports</small>')

    def test_led_views_remain_visible_without_screens(self):
        user = get_user_model().objects.create_user(
            username='led-empty-dashboard-admin',
            password='test-password',
            role='admin',
        )
        self.client.force_login(user)

        response = self.client.get(reverse('dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-target="cat-ecrans"')
        for view_name in (
            'vue', 'fiche', 'historique', 'contrats', 'finance', 'energie',
        ):
            self.assertContains(response, f'data-led-tab="{view_name}"')
        self.assertContains(response, 'Aucun écran numérique n’est enregistré')

    def test_scheduled_spots_are_separate_from_diffusion_logs(self):
        today = timezone.localdate()
        client = Client.objects.create(
            nom='Client LED test',
            telephone='00000000',
            email='led-dashboard-test@example.com',
        )
        support = Support.objects.create(
            code='LED-REAL-TEST',
            nom='Écran LED réel',
            type_support=Support.TYPE_ECRAN,
            latitude=12.34,
            longitude=-1.52,
            ville='Ouagadougou',
        )
        Support.objects.filter(pk=support.pk).update(
            created_at=timezone.make_aware(
                datetime.datetime(today.year, 1, 1)
            )
        )
        support.refresh_from_db()
        screen = EcranNumerique.objects.create(support=support)
        contract = Contrat.objects.create(
            client=client,
            nom='Contrat écran réel',
            date_debut=datetime.date(today.year, 1, 1),
            date_fin=today + datetime.timedelta(days=30),
            nb_spots=1000,
        )
        campaign = Campagne.objects.create(
            client=client,
            nom='Campagne LED réelle',
            date_debut=datetime.date(today.year, 1, 1),
            date_fin=today,
            statut=Campagne.STATUT_EN_COURS,
            type_support='ecran',
            prix='5.00',
            duree_passage=10,
            frequence=120,
            contrat=contract,
        )
        line = LigneCampagne.objects.create(
            campagne=campaign,
            support=support,
            tranches_horaires='09:00-10:00',
        )
        LogDiffusion.objects.create(
            ecran=screen,
            ligne=line,
            campagne=campaign,
            client=client,
            timestamp=timezone.make_aware(
                datetime.datetime.combine(today, datetime.time(9, 30))
            ),
            duree_sec=10,
        )
        screen_stats = {
            support.pk: {
                'objectif_spots_annuel': 500,
                'spots_aujourdhui': 2,
                'taux_actuel': 1.0,
                'taux_annuel': 0.4,
                'secondes_reservees_aujourdhui': 20,
                'secondes_disponibles_aujourdhui': 61200,
            },
        }

        data = DashboardView._build_led_dashboard_data(
            'Ouagadougou', today, screen_stats
        )

        self.assertEqual(data['ecran_dashboard_screens'][0]['spots_diffuses'], 1)
        self.assertEqual(
            data['ecran_dashboard_screens'][0]['capacite_spots_jour'],
            3060,
        )
        self.assertEqual(
            data['ecran_dashboard_screens'][0]['taux_jour'],
            1.0,
        )
        self.assertEqual(
            data['ecran_dashboard_screens'][0]['spots_programmes_jour'],
            30,
        )
        self.assertEqual(data['ecran_dashboard_monthly'][today.month - 1]['spots'], 1)
        self.assertEqual(data['ecran_dashboard_campaigns'][0]['spots_diffuses'], 1)
        self.assertEqual(
            data['ecran_dashboard_campaigns'][0]['mensuel_diffuse'][today.month - 1],
            1,
        )
        hourly_schedule = data['ecran_dashboard_screens'][0][
            'programmation_horaire'
        ]
        nine_am_slot = next(
            slot for slot in hourly_schedule if slot['heure'] == '09:00'
        )
        self.assertEqual(nine_am_slot['spots'], 30)
        self.assertEqual(nine_am_slot['capacite'], 180)
        self.assertEqual(nine_am_slot['campagnes'][0]['nom'], campaign.nom)
        self.assertGreater(
            data['ecran_dashboard_screens'][0]['mensuel_programme'][today.month - 1],
            data['ecran_dashboard_screens'][0]['mensuel_diffuse'][today.month - 1],
        )
        self.assertEqual(
            data['ecran_dashboard_screens'][0]['mensuel_duree_diffusee'][today.month - 1],
            10,
        )
        monthly_screen = data['ecran_dashboard_screens'][0]
        expected_monthly_capacity = (
            calendar.monthrange(today.year, today.month)[1]
            * 17
            * 60
            * 3
        )
        self.assertEqual(
            monthly_screen['mensuel_capacite'][today.month - 1],
            expected_monthly_capacity,
        )
        self.assertEqual(
            data['ecran_dashboard_monthly'][today.month - 1]['capacite'],
            expected_monthly_capacity,
        )
        self.assertEqual(
            monthly_screen['mensuel_programme'][0],
            31 * 30,
        )
        self.assertGreater(monthly_screen['mensuel_programme'][0], 0)
        self.assertGreater(
            monthly_screen['mensuel_programme'][today.month - 1], 0
        )
        self.assertGreater(
            data['ecran_dashboard_monthly'][0]['taux_programme'], 0
        )
        self.assertGreater(
            data['ecran_dashboard_monthly'][today.month - 1]['taux_programme'],
            0,
        )
        self.assertGreater(
            data['ecran_dashboard_campaigns'][0]['spots_programmes'],
            data['ecran_dashboard_campaigns'][0]['spots_diffuses'],
        )
        self.assertTrue(data['ecran_dashboard_campaigns'][0]['prix_renseigne'])
        self.assertEqual(data['ecran_dashboard_contracts'][0]['nb_spots'], 1000)

    def test_dashboard_saves_led_parameters_bilans_and_packages(self):
        today = timezone.localdate()
        user = get_user_model().objects.create_user(
            username='led-dashboard-admin',
            password='test-password',
            role='admin',
        )
        self.client.force_login(user)
        client = Client.objects.create(
            nom='Client LED formulaires',
            telephone='00000000',
            email='led-forms@example.com',
        )
        support = Support.objects.create(
            code='LED-FORM-TEST',
            nom='Écran pour formulaires',
            type_support=Support.TYPE_ECRAN,
            latitude=12.34,
            longitude=-1.52,
            ville='Ouagadougou',
        )
        EcranNumerique.objects.create(support=support)
        contract = Contrat.objects.create(
            client=client,
            nom='Contrat package test',
            date_debut=today,
            date_fin=today + datetime.timedelta(days=30),
            nb_spots=100,
        )
        dashboard_url = reverse('dashboard')
        preserved_filters = {
            'ville': 'Ouagadougou',
            'ecran_id': str(support.pk),
            'ecran_annee': str(today.year),
            'ecran_date': today.isoformat(),
        }

        response = self.client.post(
            dashboard_url,
            {
                **preserved_filters,
                'led_action': 'exploitation',
                'puissance_kw': '1.250',
                'mode_commande': 'distance',
                'cout_intervention_fcfa': '',
                'seuil_extinction_heures': '',
                'notes': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        exploitation = EcranParametresExploitation.objects.get(ecran__support=support)
        self.assertEqual(exploitation.puissance_kw, Decimal('1.250'))
        self.assertEqual(exploitation.cout_intervention_fcfa, None)

        response = self.client.post(
            dashboard_url,
            {
                **preserved_filters,
                'led_action': 'bilan',
                'annee': str(today.year),
                'mois': str(today.month),
                'consommation_kwh': '100',
                'tarif_energie_fcfa_kwh': '5',
                'taxes_publicitaires_fcfa': '',
                'loyer_site_fcfa': '',
                'maintenance_fcfa': '',
                'frais_techniques_fcfa': '',
                'autres_charges_fcfa': '',
                'encaissements_fcfa': '25000',
                'notes': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        bilan = EcranBilanMensuel.objects.get(ecran__support=support)
        self.assertIsNone(bilan.total_charges_fcfa)
        self.assertEqual(bilan.encaissements_fcfa, Decimal('25000'))

        response = self.client.post(
            dashboard_url,
            {
                **preserved_filters,
                'led_action': 'bilan',
                'annee': str(today.year),
                'mois': str(today.month),
                'consommation_kwh': '100',
                'tarif_energie_fcfa_kwh': '5',
                'taxes_publicitaires_fcfa': '10',
                'loyer_site_fcfa': '20',
                'maintenance_fcfa': '30',
                'frais_techniques_fcfa': '40',
                'autres_charges_fcfa': '5',
                'encaissements_fcfa': '25000',
                'notes': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        bilan.refresh_from_db()
        self.assertEqual(bilan.total_charges_fcfa, Decimal('605.00'))

        response = self.client.post(
            dashboard_url,
            {
                **preserved_filters,
                'led_action': 'package',
                'contrat': str(contract.pk),
                'agence': 'Agence test',
                'annonceur': 'Annonceur test',
                'prix_unitaire_fcfa': '125',
                'notes': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        package = PackageEcran.objects.get(contrat=contract)
        self.assertEqual(package.agence, 'Agence test')
        self.assertEqual(package.prix_unitaire_fcfa, Decimal('125.00'))

        response = self.client.get(
            dashboard_url,
            {
                **preserved_filters,
                'package_contrat_id': str(contract.pk),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['ecran_form_package'].instance, package)
        self.assertContains(response, 'Agence test')
