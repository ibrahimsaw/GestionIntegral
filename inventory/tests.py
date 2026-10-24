import datetime
import io
import zipfile
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.urls import reverse
from unittest.mock import patch

from campaigns.forms import LigneCampagneForm
from campaigns.models import Campagne, Client, LigneCampagne
from inventory.forms import MaintenanceForm, SupportForm
from inventory.models import (
    Emplacement,
    EcranNumerique,
    FacePanneau,
    FormatSupport,
    Marche,
    Maintenance,
    Support,
    SupportDeletionRequest,
)
from inventory.views import (
    FormatSupportDetailView,
    FormatSupportListView,
    SupportListView,
)
from campaigns.context_processors import alerts_data
from geoad.context_processors import inventory_villes


class PreventiveMaintenanceStateTests(TestCase):
    def test_maintenance_form_offers_preventive_state(self):
        form = MaintenanceForm()
        self.assertIn(
            (
                Support.ETAT_A_MAINTENIR,
                'Fonctionnel — maintenance à prévoir',
            ),
            list(form.fields['etat_apres'].choices),
        )

    def test_maintenance_marks_screen_functional_but_due_for_maintenance(self):
        screen = Support.objects.create(
            code='MAINT-SCREEN',
            nom='Écran à entretenir',
            type_support=Support.TYPE_ECRAN,
            latitude=12.34,
            longitude=-1.52,
        )

        Maintenance.objects.create(
            support=screen,
            etat_apres=Support.ETAT_A_MAINTENIR,
        )

        screen.refresh_from_db()
        self.assertEqual(screen.etat, Support.ETAT_A_MAINTENIR)

    def test_panel_state_tracks_preventive_and_failed_faces(self):
        panel = Support.objects.create(
            code='MAINT-PANEL',
            nom='Panneau à entretenir',
            type_support=Support.TYPE_PANNEAU,
            latitude=12.34,
            longitude=-1.52,
        )
        face_a = FacePanneau.objects.create(support=panel, label='A')
        face_b = FacePanneau.objects.create(support=panel, label='B')

        Maintenance.objects.create(
            support=panel,
            face=face_a,
            etat_apres=Support.ETAT_A_MAINTENIR,
        )
        panel.refresh_from_db()
        self.assertEqual(panel.etat, Support.ETAT_A_MAINTENIR)

        Maintenance.objects.create(
            support=panel,
            face=face_b,
            etat_apres=Support.ETAT_PANNE,
        )
        panel.refresh_from_db()
        self.assertEqual(panel.etat, Support.ETAT_A_MAINTENIR)

        Maintenance.objects.create(
            support=panel,
            face=face_a,
            etat_apres=Support.ETAT_PANNE,
        )
        panel.refresh_from_db()
        self.assertEqual(panel.etat, Support.ETAT_PANNE)

    def test_preventive_state_remains_selectable_for_campaigns(self):
        screen = Support.objects.create(
            code='MAINT-BOOKABLE',
            nom='Écran réservable',
            type_support=Support.TYPE_ECRAN,
            etat=Support.ETAT_A_MAINTENIR,
            latitude=12.34,
            longitude=-1.52,
        )

        form = LigneCampagneForm(data={'type_support': Support.TYPE_ECRAN})

        self.assertIn(screen, form.fields['support'].queryset)


class SupportFormCategoryFilterTests(TestCase):
    def setUp(self):
        self.geant_format = FormatSupport.objects.create(
            code='FORM-GEANT',
            dimensions='8m × 6m',
            superficie=48,
            categorie='Géant',
        )
        self.sucette_format = FormatSupport.objects.create(
            code='FORM-SUCETTE',
            dimensions='1m × 2m',
            superficie=2,
            categorie='Sucette',
        )
        self.screen_format = FormatSupport.objects.create(
            code='FORM-ECRAN',
            dimensions='6 × 4',
            categorie='Ecran',
        )
        self.market_format = FormatSupport.objects.create(
            code='FORM-MARCHE',
            dimensions='Marché',
            categorie='Marché',
        )

    def test_form_lists_categories_and_groups_formats_by_category(self):
        form = SupportForm()
        choices = form.fields['format'].choices

        self.assertIn(('Géant', [('FORM-GEANT', '8m × 6m (48m²)')]), choices)
        self.assertIn(('Sucette', [('FORM-SUCETTE', '1m × 2m (2m²)')]), choices)
        self.assertIn(('Géant', 'Géant'), form.fields['categorie_format'].choices)
        self.assertIn(('Sucette', 'Sucette'), form.fields['categorie_format'].choices)
        self.assertIn(('Marché', 'Marché'), form.fields['categorie_format'].choices)
        self.assertNotIn(('Ecran', 'Ecran'), form.fields['categorie_format'].choices)
        self.assertNotIn(
            ('Ecran', [('FORM-ECRAN', '6 × 4')]),
            choices,
        )

    def test_selected_category_must_match_selected_format(self):
        form = SupportForm(data={
            'categorie_format': 'Géant',
            'format': self.sucette_format.code,
            'type_support': Support.TYPE_PANNEAU,
            'nb_faces': 2,
        })

        self.assertIn('format', form.errors)
        self.assertIn('ne correspond pas à la catégorie', form.errors['format'][0])


class SupportMarketPlacementDefaultsTests(TestCase):
    def setUp(self):
        self.market = Marche.objects.create(nom='Marché sans emplacement')
        self.market_format = FormatSupport.objects.create(
            code='MARKET-PANEL-FORMAT',
            dimensions='Emplacement marché',
            superficie=1,
            categorie='Marché',
        )
        staff = get_user_model().objects.create_user(
            username='market-panel-create-staff',
            password='test-password',
            role='staff',
        )
        self.client.force_login(staff)

    def test_new_support_form_exposes_market_and_editable_market_face_default(self):
        response = self.client.get(reverse('support_create'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="id_marche_select" name="marche"')
        self.assertContains(response, "nbFacesInput.value = '1'")
        self.assertContains(response, 'marketFaceDefaultApplied = true;')
        self.assertContains(response, 'updateFaces();')
        self.assertContains(response, 'premierEmplacementLibre')
        self.assertContains(response, 'supportSubmitButton.disabled = blocked;')
        self.assertContains(response, 'Ce marché n’a aucun emplacement libre.')

    def test_cannot_create_panel_in_market_without_free_location(self):
        response = self.client.post(reverse('support_create'), {
            'type_choice': Support.TYPE_PANNEAU,
            'type_support': Support.TYPE_PANNEAU,
            'code': 'MARKET-PANEL-NO-SLOT',
            'nom': 'Panneau marché sans emplacement',
            'format': self.market_format.code,
            'categorie_format': 'Marché',
            'nb_faces': 1,
            'latitude': '12.3400000',
            'longitude': '-1.5200000',
            'ville': 'Ouagadougou',
            'etat': Support.ETAT_BON,
            'marche': str(self.market.pk),
            'emplacement': '',
        })

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Ce marché n'a plus d'emplacement libre",
            response.context['form'].non_field_errors()[0],
        )
        self.assertFalse(
            Support.objects.filter(code='MARKET-PANEL-NO-SLOT').exists()
        )

    def test_market_emplacements_include_availability_in_code_order(self):
        market = Marche.objects.create(nom='Marché des emplacements')
        occupied = Emplacement.objects.create(marche=market, code='A-01')
        first_free = Emplacement.objects.create(marche=market, code='A-02')
        Emplacement.objects.create(marche=market, code='A-03')
        Support.objects.create(
            code='MARKET-OCCUPIED',
            nom='Support déjà installé',
            type_support=Support.TYPE_PANNEAU,
            emplacement=occupied,
            latitude=12.34,
            longitude=-1.52,
        )
        response = self.client.get(
            reverse('api_emplacements_marche', args=[market.pk])
        )

        self.assertEqual(response.status_code, 200)
        emplacements = response.json()['emplacements']
        self.assertEqual(
            [emplacement['code'] for emplacement in emplacements],
            ['A-01', 'A-02', 'A-03'],
        )
        self.assertFalse(emplacements[0]['libre'])
        self.assertEqual(
            next(emplacement['id'] for emplacement in emplacements if emplacement['libre']),
            first_free.pk,
        )

    def test_market_panel_is_included_in_supports_geojson(self):
        market = Marche.objects.create(
            nom='Marché visible sur la carte',
            latitude=12.345,
            longitude=-1.523,
        )
        emplacement = Emplacement.objects.create(marche=market, code='B-01')
        support = Support.objects.create(
            code='MARKET-PANEL-GEOJSON',
            nom='Panneau du marché',
            type_support=Support.TYPE_PANNEAU,
            format=self.market_format.code,
            emplacement=emplacement,
            latitude=12.345,
            longitude=-1.523,
        )
        second_emplacement = Emplacement.objects.create(marche=market, code='B-02')
        second_support = Support.objects.create(
            code='MARKET-PANEL-GEOJSON-2',
            nom='Deuxième panneau du marché',
            type_support=Support.TYPE_PANNEAU,
            format=self.market_format.code,
            emplacement=second_emplacement,
            latitude=0,
            longitude=0,
        )

        response = self.client.get(reverse('api_geojson'))

        self.assertEqual(response.status_code, 200)
        features = {
            feature['properties']['id']: feature
            for feature in response.json()['features']
        }
        feature = features[support.pk]
        second_feature = features[second_support.pk]
        self.assertEqual(feature['geometry']['coordinates'], [-1.523, 12.345])
        self.assertEqual(feature['properties']['marche'], market.nom)
        self.assertEqual(feature['properties']['marche_id'], market.pk)
        self.assertEqual(feature['properties']['emplacement_code'], emplacement.code)
        self.assertEqual(second_feature['geometry']['coordinates'], [-1.523, 12.345])
        self.assertEqual(second_feature['properties']['marche_id'], market.pk)


class FormatSupportDetailViewTests(TestCase):
    def test_detail_shows_format_details_and_matching_supports(self):
        format_support = FormatSupport.objects.create(
            code='DETAIL-4X3',
            dimensions='4m × 3m',
            superficie=12,
            categorie='Standard',
        )
        support = Support.objects.create(
            code='DETAIL-SUPPORT',
            nom='Panneau du format',
            type_support=Support.TYPE_PANNEAU,
            format=format_support.code,
            latitude=12.34,
            longitude=-1.52,
            ville='Ouagadougou',
            quartier='Centre',
        )

        request = RequestFactory().get(reverse(
            'format_support_detail', args=[format_support.pk]
        ))
        view = FormatSupportDetailView()
        view.setup(request, pk=format_support.pk)
        view.object = format_support
        context = view.get_context_data(object=format_support)
        rendered = render_to_string(view.template_name, context, request=request)

        self.assertEqual(context['support_count'], 1)
        self.assertEqual(list(context['supports']), [support])
        self.assertIn('4m × 3m', rendered)
        self.assertIn('Panneau du format', rendered)

    def test_detail_hides_empty_category(self):
        format_support = FormatSupport.objects.create(
            code='DETAIL-NO-CATEGORY',
            dimensions='4m × 3m',
            categorie='',
        )
        request = RequestFactory().get(reverse(
            'format_support_detail', args=[format_support.pk]
        ))
        request.user = AnonymousUser()
        view = FormatSupportDetailView()
        view.setup(request, pk=format_support.pk)
        view.object = format_support
        context = view.get_context_data(object=format_support)
        rendered = render_to_string(view.template_name, context, request=request)

        self.assertNotIn('Sans catégorie', rendered)
        self.assertNotIn('bi-tag-fill', rendered)
        self.assertNotIn('>Catégorie</dt>', rendered)

    def test_format_used_by_panel_cannot_be_deleted(self):
        format_support = FormatSupport.objects.create(
            code='DELETE-PROTECTED',
            dimensions='4m × 3m',
            superficie=12,
            categorie='Standard',
        )
        Support.objects.create(
            code='DELETE-PROTECTED-PANEL',
            nom='Panneau utilisant le format',
            type_support=Support.TYPE_PANNEAU,
            format=format_support.code,
            latitude=12.34,
            longitude=-1.52,
        )
        user = get_user_model().objects.create_user(
            username='format-delete-test',
            password='test-password',
            role='staff',
        )
        self.client.force_login(user)

        response = self.client.post(reverse('format_delete', args=[format_support.pk]))

        self.assertRedirects(
            response,
            reverse('format_support_detail', args=[format_support.pk]),
        )
        self.assertTrue(FormatSupport.objects.filter(pk=format_support.pk).exists())
        self.assertIn(
            'au moins un panneau l’utilise',
            str(list(get_messages(response.wsgi_request))),
        )

    def test_unused_format_can_still_be_deleted(self):
        format_support = FormatSupport.objects.create(
            code='DELETE-UNUSED',
            dimensions='4m × 3m',
            superficie=12,
            categorie='Standard',
        )
        user = get_user_model().objects.create_user(
            username='format-delete-unused-test',
            password='test-password',
            role='staff',
        )
        self.client.force_login(user)

        response = self.client.post(reverse('format_delete', args=[format_support.pk]))

        self.assertRedirects(response, reverse('format_list'))
        self.assertFalse(FormatSupport.objects.filter(pk=format_support.pk).exists())

    def test_detail_supports_are_paginated(self):
        format_support = FormatSupport.objects.create(
            code='DETAIL-PAGED',
            dimensions='4m × 3m',
            categorie='Standard',
        )
        for number in range(21):
            Support.objects.create(
                code=f'DETAIL-PAGED-{number:02d}',
                nom=f'Panneau {number:02d}',
                type_support=Support.TYPE_PANNEAU,
                format=format_support.code,
                latitude=12.34,
                longitude=-1.52,
                ville='Ouagadougou',
            )

        request = RequestFactory().get(
            reverse('format_support_detail', args=[format_support.pk]),
            {'page': '2'},
        )
        view = FormatSupportDetailView()
        view.setup(request, pk=format_support.pk)
        view.object = format_support
        context = view.get_context_data(object=format_support)
        rendered = render_to_string(view.template_name, context, request=request)

        self.assertEqual(context['paginator'].count, 21)
        self.assertEqual(context['page_obj'].number, 2)
        self.assertEqual(len(context['supports']), 1)
        self.assertIn('Page 2 / 2', rendered)
        self.assertIn('?page=1', rendered)

    def test_format_list_is_paginated_and_reports_total_count(self):
        for number in range(21):
            FormatSupport.objects.create(
                code=f'LIST-PAGED-{number:02d}',
                dimensions='4m × 3m',
                categorie='Standard',
            )

        request = RequestFactory().get(reverse('format_list'), {'page': '2'})
        view = FormatSupportListView()
        view.setup(request)
        object_list = view.get_queryset()
        context = view.get_context_data(object_list=object_list)
        rendered = render_to_string(view.template_name, context, request=request)

        self.assertEqual(context['paginator'].count, 21)
        self.assertEqual(context['page_obj'].number, 2)
        self.assertIn('21 format', rendered)
        self.assertIn('Page 2 / 2', rendered)

    def test_format_list_is_sorted_by_category_then_measure_and_code(self):
        formats = [
            ('SORT-LARGE', '8m × 6m', 48, 'Géant'),
            ('SORT-SMALL', '1m × 2m', 2, 'Sucette'),
            ('SORT-MIDDLE', '4m × 3m', 12, 'Standard'),
            ('SORT-DERIVED', '9m × 5m', None, 'Géant'),
            ('SORT-UNKNOWN', 'Personnalisé', None, 'Marché'),
        ]
        for code, dimensions, superficie, categorie in formats:
            FormatSupport.objects.create(
                code=code,
                dimensions=dimensions,
                superficie=superficie,
                categorie=categorie,
            )

        view = FormatSupportListView()
        view.setup(RequestFactory().get(
            reverse('format_list'),
            {'sort': 'code', 'dir': 'desc'},
        ))

        self.assertEqual(
            [format_support.code for format_support in view.get_queryset()],
            [
                'SORT-DERIVED',
                'SORT-LARGE',
                'SORT-UNKNOWN',
                'SORT-MIDDLE',
                'SORT-SMALL',
            ],
        )

    def test_format_list_defaults_to_category_then_superficie_order(self):
        expected = [
            ('ORDER-GEANT-SMALL', 2, 'Géant'),
            ('ORDER-GEANT-LARGE', 48, 'Géant'),
            ('ORDER-STANDARD', 12, 'Standard'),
            ('ORDER-SUCETTE', 1, 'Sucette'),
        ]
        for code, superficie, categorie in expected:
            FormatSupport.objects.create(
                code=code,
                dimensions=f'{superficie}m²',
                superficie=superficie,
                categorie=categorie,
            )

        view = FormatSupportListView()
        view.setup(RequestFactory().get(reverse('format_list')))

        ordered_formats = [
            item for item in view.get_queryset()
            if item.code.startswith('ORDER-')
        ]
        self.assertEqual(
            [(item.code, item.superficie) for item in ordered_formats],
            [(code, superficie) for code, superficie, _ in expected],
        )


class SupportDeletionApprovalTests(TestCase):
    def setUp(self):
        self.support = Support.objects.create(
            code='DELETE-APPROVAL',
            nom='Support à supprimer',
            type_support=Support.TYPE_ECRAN,
            latitude=12.34,
            longitude=-1.52,
        )
        self.requester = get_user_model().objects.create_user(
            username='deletion-requester',
            password='test-password',
            role='staff',
        )
        self.reviewer = get_user_model().objects.create_user(
            username='deletion-reviewer',
            password='test-password',
            role='staff',
        )
        self.admin = get_user_model().objects.create_user(
            username='deletion-admin',
            password='test-password',
            role='admin',
        )
        self.url = reverse('support_delete', args=[self.support.uuid])

    def test_second_staff_user_must_approve_and_cannot_be_requester(self):
        self.client.force_login(self.requester)

        confirmation_response = self.client.get(self.url)
        self.assertContains(confirmation_response, 'un autre compte utilisateur')

        first_response = self.client.post(self.url)

        self.assertRedirects(
            first_response,
            reverse('support_detail', args=[self.support.uuid]),
        )
        deletion_request = SupportDeletionRequest.objects.get(support=self.support)
        self.assertEqual(deletion_request.requested_by, self.requester)
        cache.clear()
        reviewer_request = RequestFactory().get('/')
        reviewer_request.user = self.reviewer
        reviewer_alerts = alerts_data(reviewer_request)['alertes']
        self.assertTrue(any(self.support.code in alert['msg'] for alert in reviewer_alerts))
        requester_request = RequestFactory().get('/')
        requester_request.user = self.requester
        requester_alerts = alerts_data(requester_request)['alertes']
        self.assertFalse(any(self.support.code in alert['msg'] for alert in requester_alerts))

        self.client.post(self.url)
        self.assertTrue(Support.objects.filter(pk=self.support.pk).exists())
        self.assertEqual(
            SupportDeletionRequest.objects.get(pk=deletion_request.pk).status,
            SupportDeletionRequest.STATUS_PENDING,
        )

        self.client.force_login(self.reviewer)
        queue_response = self.client.get(reverse('support_deletion_requests'))
        self.assertContains(queue_response, self.support.code)
        support_list_response = self.client.get(reverse('support_list'))
        self.assertContains(support_list_response, '1 demande à approuver')
        self.assertContains(support_list_response, 'Suppression en attente')
        approval_response = self.client.post(self.url)

        self.assertRedirects(approval_response, reverse('support_list'))
        self.assertFalse(Support.objects.filter(pk=self.support.pk).exists())
        deletion_request.refresh_from_db()
        self.assertIsNone(deletion_request.support)
        self.assertEqual(deletion_request.status, SupportDeletionRequest.STATUS_APPROVED)
        self.assertEqual(deletion_request.approved_by, self.reviewer)

    def test_admin_can_delete_support_without_second_approval(self):
        self.client.force_login(self.admin)

        response = self.client.post(self.url)

        self.assertRedirects(response, reverse('support_list'))
        self.assertFalse(Support.objects.filter(pk=self.support.pk).exists())
        self.assertFalse(SupportDeletionRequest.objects.filter(support_code=self.support.code).exists())

    def test_panel_also_requires_two_distinct_staff_users(self):
        panel = Support.objects.create(
            code='DELETE-PANEL-APPROVAL',
            nom='Panneau à supprimer',
            type_support=Support.TYPE_PANNEAU,
            format='4x3',
            latitude=12.34,
            longitude=-1.52,
        )
        panel_url = reverse('support_delete', args=[panel.uuid])
        self.client.force_login(self.requester)
        self.client.post(panel_url)
        self.client.force_login(self.reviewer)

        response = self.client.post(panel_url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('support_list'))
        self.assertFalse(Support.objects.filter(pk=panel.pk).exists())

    def test_support_linked_to_campaign_cannot_be_deleted_even_by_admin(self):
        client = Client.objects.create(
            nom='Client suppression',
            telephone='00000000',
            email='delete-campaign@example.com',
        )
        campaign = Campagne.objects.create(
            client=client,
            nom='Campagne liée',
            date_debut=datetime.date.today(),
            date_fin=datetime.date.today() + datetime.timedelta(days=1),
        )
        LigneCampagne.objects.create(campagne=campaign, support=self.support)
        self.client.force_login(self.admin)

        response = self.client.post(self.url)

        self.assertRedirects(
            response,
            reverse('support_detail', args=[self.support.uuid]),
        )
        self.assertTrue(Support.objects.filter(pk=self.support.pk).exists())
        self.assertFalse(SupportDeletionRequest.objects.filter(support=self.support).exists())

    def test_admin_can_delete_directly_and_cancels_pending_request(self):
        self.client.force_login(self.requester)
        self.client.post(self.url)
        self.client.force_login(self.admin)

        response = self.client.post(self.url)

        self.assertRedirects(response, reverse('support_list'))
        self.assertFalse(Support.objects.filter(pk=self.support.pk).exists())
        deletion_request = SupportDeletionRequest.objects.get(support_code=self.support.code)
        self.assertEqual(deletion_request.status, SupportDeletionRequest.STATUS_CANCELLED)


class SupportListCategoryFilterTests(TestCase):
    def setUp(self):
        self.geant_format = FormatSupport.objects.create(
            code='FILTER-GEANT',
            dimensions='8m × 6m',
            categorie='Géant',
        )
        self.sucette_format = FormatSupport.objects.create(
            code='FILTER-SUCETTE',
            dimensions='1m × 2m',
            categorie='Sucette',
        )
        self.screen_format = FormatSupport.objects.create(
            code='FILTER-ECRAN',
            dimensions='6 × 4',
            categorie='Ecran',
        )
        self.geant = Support.objects.create(
            code='FILTER-PANEL-GEANT',
            nom='Panneau géant',
            type_support=Support.TYPE_PANNEAU,
            format=self.geant_format.code,
            latitude=12.34,
            longitude=-1.52,
        )
        self.sucette = Support.objects.create(
            code='FILTER-PANEL-SUCETTE',
            nom='Panneau sucette',
            type_support=Support.TYPE_PANNEAU,
            format=self.sucette_format.code,
            latitude=12.34,
            longitude=-1.52,
            ville='Bobo-Dioulasso',
        )
        self.screen = Support.objects.create(
            code='FILTER-SCREEN',
            nom='Écran numérique',
            type_support=Support.TYPE_ECRAN,
            latitude=12.34,
            longitude=-1.52,
        )
        EcranNumerique.objects.create(
            support=self.screen,
            cellule=self.screen_format.code,
        )

    def get_view(self, query):
        request = RequestFactory().get(reverse('support_list'), query)
        view = SupportListView()
        view.setup(request)
        return view

    def test_category_filter_returns_supports_of_selected_category(self):
        view = self.get_view({'categorie': 'Géant'})

        self.assertEqual(list(view.get_queryset()), [self.geant])

    def test_12m2_menu_category_matches_standard_formats(self):
        standard_format = FormatSupport.objects.create(
            code='FILTER-STANDARD',
            dimensions='4m × 3m',
            superficie=12,
            categorie='Standard',
        )
        standard = Support.objects.create(
            code='FILTER-PANEL-STANDARD',
            nom='Panneau standard',
            type_support=Support.TYPE_PANNEAU,
            format=standard_format.code,
            latitude=12.34,
            longitude=-1.52,
        )

        view = self.get_view({'categorie': '12m2'})

        self.assertEqual(list(view.get_queryset()), [standard])

    def test_screen_category_filters_by_screen_cell_format(self):
        legacy_screen = Support.objects.create(
            code='FILTER-SCREEN-LEGACY',
            nom='Écran sans fiche technique',
            type_support=Support.TYPE_ECRAN,
            latitude=12.34,
            longitude=-1.52,
        )
        view = self.get_view({'categorie': 'Ecran'})

        self.assertEqual(set(view.get_queryset()), {self.screen, legacy_screen})

    def test_unknown_screen_cell_format_remains_filterable(self):
        screen = Support.objects.create(
            code='FILTER-SCREEN-UNKNOWN',
            nom='Écran format non catalogué',
            type_support=Support.TYPE_ECRAN,
            latitude=12.34,
            longitude=-1.52,
        )
        EcranNumerique.objects.create(support=screen, cellule='9x5')

        view = self.get_view({
            'categorie': 'Ecran',
            'type_panneau': '9x5',
        })
        context = view.get_context_data(object_list=view.get_queryset())

        self.assertEqual(list(view.get_queryset()), [screen])
        self.assertIn(('9x5', '9x5'), context['type_panneau_choices'])

    def test_categories_and_formats_are_limited_to_selected_city(self):
        view = self.get_view({
            'ville': 'Bobo-Dioulasso',
            'categorie': 'Sucette',
        })
        context = view.get_context_data(object_list=view.get_queryset())

        self.assertEqual(context['category_choices'], ['Sucette'])
        self.assertEqual(
            context['type_panneau_choices'],
            [('FILTER-SUCETTE', '1m × 2m')],
        )
        self.assertEqual(list(view.get_queryset()), [self.sucette])

    def test_category_choices_and_formats_are_exposed_to_filter(self):
        view = self.get_view({'categorie': 'Géant'})
        queryset = view.get_queryset()
        context = view.get_context_data(object_list=queryset)
        view.request.user = AnonymousUser()
        rendered = render_to_string(
            view.template_name,
            context,
            request=view.request,
        )

        self.assertIn('Géant', context['category_choices'])
        self.assertEqual(context['category_f'], 'Géant')
        self.assertEqual(
            context['type_panneau_choices'],
            [('FILTER-GEANT', '8m × 6m')],
        )
        self.assertIn('name="categorie"', rendered)
        self.assertIn('<option value="Géant" selected>Géant</option>', rendered)
        self.assertNotIn('Tous les types', rendered)


class InventoryNavigationTests(TestCase):
    def test_menu_keeps_standard_and_giant_by_city_and_globalizes_sucette_screen(self):
        geant_format = FormatSupport.objects.create(
            code='MENU-GEANT',
            dimensions='8m × 6m',
            superficie=48,
            categorie='Géant',
        )
        standard_format = FormatSupport.objects.create(
            code='MENU-STANDARD',
            dimensions='4m × 3m',
            superficie=12,
            categorie='Standard',
        )
        sucette_format = FormatSupport.objects.create(
            code='MENU-SUCETTE',
            dimensions='1m × 2m',
            superficie=2,
            categorie='Sucette',
        )
        Support.objects.create(
            code='MENU-PANEL-GEANT',
            nom='Panneau géant',
            type_support=Support.TYPE_PANNEAU,
            format=geant_format.code,
            latitude=12.34,
            longitude=-1.52,
            ville='Ouagadougou',
        )
        Support.objects.create(
            code='MENU-PANEL-STANDARD',
            nom='Panneau standard',
            type_support=Support.TYPE_PANNEAU,
            format=standard_format.code,
            latitude=12.34,
            longitude=-1.52,
            ville='Ouagadougou',
        )
        Support.objects.create(
            code='MENU-PANEL-SUCETTE',
            nom='Panneau sucette',
            type_support=Support.TYPE_PANNEAU,
            format=sucette_format.code,
            latitude=12.34,
            longitude=-1.52,
            ville='Bobo-Dioulasso',
        )
        for number in range(3):
            Support.objects.create(
                code=f'MENU-PANEL-BOBO-{number}',
                nom=f'Panneau géant {number}',
                type_support=Support.TYPE_PANNEAU,
                format=geant_format.code,
                latitude=12.34,
                longitude=-1.52,
                ville='Bobo-Dioulasso',
            )
        screen_format = FormatSupport.objects.create(
            code='MENU-ECRAN',
            dimensions='6 × 4',
            categorie='Ecran',
        )
        smaller_screen_format = FormatSupport.objects.create(
            code='MENU-ECRAN-2',
            dimensions='2 × 1',
            superficie=2,
            categorie='Ecran',
        )
        screen = Support.objects.create(
            code='MENU-SCREEN',
            nom='Écran de Ouagadougou',
            type_support=Support.TYPE_ECRAN,
            latitude=12.34,
            longitude=-1.52,
            ville='Ouagadougou',
        )
        EcranNumerique.objects.create(support=screen, cellule=screen_format.code)
        smaller_screen = Support.objects.create(
            code='MENU-SCREEN-2',
            nom='Petit écran de Ouagadougou',
            type_support=Support.TYPE_ECRAN,
            latitude=12.34,
            longitude=-1.52,
            ville='Ouagadougou',
        )
        EcranNumerique.objects.create(
            support=smaller_screen,
            cellule=smaller_screen_format.code,
        )

        navigation = inventory_villes(RequestFactory().get('/'))
        self.assertEqual(
            [city['ville'] for city in navigation['inventory_categories']],
            ['Bobo-Dioulasso', 'Ouagadougou'],
        )
        self.assertEqual(
            [city['support_count'] for city in navigation['inventory_categories']],
            [3, 2],
        )
        ouagadougou = next(
            city for city in navigation['inventory_categories']
            if city['ville'] == 'Ouagadougou'
        )
        categories = {
            category['label']: category['formats']
            for category in ouagadougou['categories']
        }

        self.assertEqual(set(categories), {'12m2', 'Géant'})
        self.assertEqual(
            categories['12m2'],
            [{'code': 'MENU-STANDARD', 'label': '4m × 3m (12 m²)'}],
        )
        self.assertEqual(
            categories['Géant'],
            [{'code': 'MENU-GEANT', 'label': '8m × 6m (48 m²)'}],
        )
        global_categories = {
            category['label']: category['formats']
            for category in navigation['inventory_global_categories']
        }
        self.assertEqual(
            global_categories['Ecran'],
            [
                {'code': 'MENU-ECRAN-2', 'label': '2 × 1 (2 Cellules)'},
                {'code': 'MENU-ECRAN', 'label': '6 × 4'},
            ],
        )
        self.assertEqual(
            global_categories['Sucette'],
            [{'code': 'MENU-SUCETTE', 'label': '1m × 2m (2 m²)'}],
        )

        request = RequestFactory().get(
            reverse('support_list'),
            {'ville': 'Ouagadougou', 'categorie': 'Géant'},
        )
        request.user = get_user_model().objects.create_user(
            username='inventory-menu-test',
            password='test-password',
        )
        rendered_menu = render_to_string('base.html', {}, request=request)

        self.assertNotIn('Tous les supports de la ville', rendered_menu)
        self.assertIn(
            '?ville=Ouagadougou&categorie=G%C3%A9ant&type_panneau=MENU-GEANT&desactif=ville',
            rendered_menu,
        )
        self.assertIn(
            '?ville=Ouagadougou&categorie=12m2&type_panneau=MENU-STANDARD&desactif=ville',
            rendered_menu,
        )
        self.assertIn(
            '12m2 · 4m × 3m (12 m²)',
            rendered_menu,
        )
        self.assertNotIn(
            '<span class="nav-submenu-label">12m2</span>',
            rendered_menu,
        )
        self.assertIn(
            '?categorie=Ecran&type_panneau=MENU-ECRAN',
            rendered_menu,
        )
        self.assertNotIn(
            '?ville=Bobo-Dioulasso&categorie=G%C3%A9ant&type_panneau=MENU-SUCETTE',
            rendered_menu,
        )
        self.assertNotIn(
            '?ville=Ouagadougou&categorie=Ecran',
            rendered_menu,
        )


class EmplacementBulkCreateTests(TestCase):
    def setUp(self):
        self.marche = Marche.objects.create(nom='Grand Marché')
        self.user = get_user_model().objects.create_user(
            username='emplacement-bulk-staff',
            password='test-password',
            role='staff',
        )
        self.client.force_login(self.user)
        self.url = reverse('emplacement_create', args=[self.marche.pk])

    def test_creates_requested_count_using_market_sequence(self):
        Emplacement.objects.create(marche=self.marche, code='GM-04')

        response = self.client.post(self.url, {'nombre': 3, 'notes': 'Allée nord'})

        self.assertRedirects(response, reverse('marche_detail', args=[self.marche.pk]))
        created = list(self.marche.emplacements.order_by('code'))
        self.assertEqual(
            [emplacement.code for emplacement in created],
            ['GM-04', 'GM-05', 'GM-06', 'GM-07'],
        )
        self.assertEqual(
            {emplacement.notes for emplacement in created if emplacement.code != 'GM-04'},
            {'Allée nord'},
        )

    def test_market_name_generates_expected_code_prefix_and_preview(self):
        response = self.client.get(self.url)

        self.assertEqual(response.context['code_prefix'], 'GM')
        self.assertEqual(response.context['code_start'], 1)
        self.assertContains(response, 'data-prefix="GM"')
        self.assertContains(response, 'data-start="1"')

        other_market = Marche.objects.create(nom='Marché de 10')
        other_response = self.client.get(
            reverse('emplacement_create', args=[other_market.pk])
        )
        self.assertEqual(other_response.context['code_prefix'], 'M10')
        creation_response = self.client.post(
            reverse('emplacement_create', args=[other_market.pk]),
            {'nombre': 2},
        )
        self.assertRedirects(
            creation_response,
            reverse('marche_detail', args=[other_market.pk]),
        )
        self.assertEqual(
            list(other_market.emplacements.order_by('code').values_list('code', flat=True)),
            ['M10-01', 'M10-02'],
        )

    def test_next_code_endpoint_uses_market_prefix_and_sequence(self):
        Emplacement.objects.create(marche=self.marche, code='GM-08')

        response = self.client.get(
            reverse('emplacement_next_code', args=[self.marche.pk])
        )

        self.assertEqual(response.json(), {'code': 'GM-09'})

    def test_count_is_required_and_must_stay_within_bounds(self):
        for data in ({}, {'nombre': 0}, {'nombre': 501}, {'nombre': 'abc'}):
            with self.subTest(data=data):
                response = self.client.post(self.url, data)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'nombre')
                self.assertEqual(self.marche.emplacements.count(), 0)

    def test_code_collision_returns_explicit_error_without_partial_creation(self):
        Emplacement.objects.create(marche=self.marche, code='GM-01')
        with patch('inventory.views.generer_prochain_code_emplacement', return_value='GM-01'):
            response = self.client.post(self.url, {'nombre': 2})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'codes suivants existent déjà')
        self.assertEqual(self.marche.emplacements.count(), 1)

    def test_non_staff_cannot_create_market_emplacements(self):
        client_user = get_user_model().objects.create_user(
            username='emplacement-bulk-technicien',
            password='test-password',
            role='technicien',
        )
        self.client.force_login(client_user)

        response = self.client.post(self.url, {'nombre': 1})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('login'))
        self.assertEqual(self.marche.emplacements.count(), 0)


class EmplacementBulkActionTests(TestCase):
    def setUp(self):
        self.marche = Marche.objects.create(nom='Marché actions')
        self.other_marche = Marche.objects.create(nom='Autre marché')
        self.user = get_user_model().objects.create_user(
            username='emplacement-actions-staff',
            password='test-password',
            role='staff',
        )
        self.client.force_login(self.user)
        self.url = reverse('emplacement_bulk_action', args=[self.marche.pk])
        self.first = Emplacement.objects.create(
            marche=self.marche,
            code='MA-01',
            notes='Ancienne note',
        )
        self.second = Emplacement.objects.create(
            marche=self.marche,
            code='MA-02',
            notes='Autre note',
        )

    def test_bulk_edit_updates_notes_only_for_selected_market_emplacements(self):
        foreign = Emplacement.objects.create(
            marche=self.other_marche,
            code='AM-01',
            notes='Ne pas modifier',
        )

        response = self.client.post(self.url, {
            'action': 'edit_notes',
            'selected_ids': [self.first.pk, self.second.pk],
            'notes': 'Note commune',
        })

        self.assertRedirects(response, reverse('marche_detail', args=[self.marche.pk]))
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        foreign.refresh_from_db()
        self.assertEqual(self.first.notes, 'Note commune')
        self.assertEqual(self.second.notes, 'Note commune')
        self.assertEqual(foreign.notes, 'Ne pas modifier')

    def test_market_detail_renders_select_all_and_per_row_checkboxes_inside_action_form(self):
        response = self.client.get(reverse('marche_detail', args=[self.marche.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="select-all-emplacements"')
        self.assertContains(response, f'value="{self.first.pk}"')
        self.assertContains(response, f'value="{self.second.pk}"')
        form_start = response.content.decode().index('id="emplacement-bulk-form"')
        checkbox = response.content.decode().index(
            f'value="{self.first.pk}"',
            form_start,
        )
        form_end = response.content.decode().index('</form>', form_start)
        self.assertLess(checkbox, form_end)

    def test_market_detail_paginates_emplacements_and_links_installed_panel(self):
        for number in range(3, 22):
            Emplacement.objects.create(
                marche=self.marche,
                code=f'MA-{number:02d}',
            )
        format_support = FormatSupport.objects.create(
            code='MARKET-DETAIL-FORMAT',
            dimensions='4m × 3m',
            superficie=12,
            categorie='Standard',
        )
        panel = Support.objects.create(
            code='MARKET-DETAIL-PANEL',
            nom='Panneau installé au marché',
            type_support=Support.TYPE_PANNEAU,
            format=format_support.code,
            latitude=12.34,
            longitude=-1.52,
            emplacement=self.first,
        )

        first_page = self.client.get(reverse('marche_detail', args=[self.marche.pk]))

        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(first_page.context['paginator'].count, 21)
        self.assertEqual(len(first_page.context['emplacements_page']), 20)
        self.assertContains(first_page, 'Page 1 / 2')
        self.assertContains(first_page, reverse('support_detail', args=[panel.uuid]))
        self.assertContains(first_page, 'title="Voir le panneau associé"')

        second_page = self.client.get(
            reverse('marche_detail', args=[self.marche.pk]),
            {'page': 2},
        )
        self.assertEqual(second_page.status_code, 200)
        self.assertEqual(second_page.context['page_obj'].number, 2)
        self.assertEqual(len(second_page.context['emplacements_page']), 1)
        self.assertContains(second_page, 'Page 2 / 2')

    def test_bulk_delete_is_admin_only(self):
        self.client.raise_request_exception = False
        response = self.client.post(self.url, {
            'action': 'delete',
            'selected_ids': [self.first.pk],
        })

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Emplacement.objects.filter(pk=self.first.pk).exists())

    def test_admin_can_bulk_delete_selected_emplacements(self):
        admin = get_user_model().objects.create_user(
            username='emplacement-actions-admin',
            password='test-password',
            role='admin',
        )
        self.client.force_login(admin)

        response = self.client.post(self.url, {
            'action': 'delete',
            'selected_ids': [self.first.pk],
        })

        self.assertRedirects(response, reverse('marche_detail', args=[self.marche.pk]))
        self.assertFalse(Emplacement.objects.filter(pk=self.first.pk).exists())
        self.assertTrue(Emplacement.objects.filter(pk=self.second.pk).exists())

    def test_bulk_delete_skips_emplacements_with_installed_support(self):
        admin = get_user_model().objects.create_user(
            username='emplacement-actions-admin-installed',
            password='test-password',
            role='admin',
        )
        self.client.force_login(admin)
        support = Support.objects.create(
            code='BULK-INSTALLED',
            nom='Panneau installé',
            type_support=Support.TYPE_PANNEAU,
            latitude=12.34,
            longitude=-1.52,
            ville='Ouagadougou',
            emplacement=self.first,
        )

        response = self.client.post(self.url, {
            'action': 'delete',
            'selected_ids': [self.first.pk, self.second.pk],
        }, follow=True)

        self.assertRedirects(response, reverse('marche_detail', args=[self.marche.pk]))
        self.assertTrue(Emplacement.objects.filter(pk=self.first.pk).exists())
        self.assertTrue(Support.objects.filter(pk=support.pk).exists())
        self.assertFalse(Emplacement.objects.filter(pk=self.second.pk).exists())
        self.assertContains(response, 'panneau installé')

    @patch('reports.views._build_context_emplacement_detail', return_value={})
    @patch('reports.views.HTML')
    def test_bulk_download_returns_zip_with_one_report_per_selected_item(
        self,
        html_mock,
        context_mock,
    ):
        html_mock.return_value.write_pdf.return_value = b'%PDF-test'

        response = self.client.post(self.url, {
            'action': 'download',
            'selected_ids': [self.first.pk, self.second.pk],
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(
                archive.namelist(),
                [
                    f'rapport_emplacement_ma-01_{self.first.pk}.pdf',
                    f'rapport_emplacement_ma-02_{self.second.pk}.pdf',
                ],
            )
            self.assertEqual(archive.read(archive.namelist()[0]), b'%PDF-test')
        self.assertEqual(context_mock.call_count, 2)
