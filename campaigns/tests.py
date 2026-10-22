import datetime
from types import MethodType

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils import timezone

from campaigns.forms import PreuveDiffusionForm
from campaigns.models import Campagne, Client, Contrat, LigneCampagne, PreuveDiffusion
from campaigns.views import CampagneDetailView, DashboardView
from piges.models import PigeFichier
from campaigns.simulation_view import (
    _enrich_resultat_metriques,
    _spots,
    calculer_objectif_total,
    calculer_repartition_blocs,
    calculer_suggestions_objectif,
    calculer_total_spots_avec_jours_ajoutes,
    generer_ajustements_meme_periode,
    generer_combinaisons_complement,
    generer_scenarios_simulation,
    normaliser_objectif_spots,
)
from inventory.models import (
    EcranNumerique,
    Emplacement,
    FacePanneau,
    FormatSupport,
    Marche,
    Support,
)


class DashboardParcGroupingTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(
            nom='Client dashboard test',
            telephone='00000000',
            email='dashboard-test@example.com',
        )
        self.formats = {
            '12m2': FormatSupport.objects.create(
                code='DASH-12', categorie='12m²', dimensions='4m × 3m',
            ),
            'sucette': FormatSupport.objects.create(
                code='DASH-SUC', categorie='Sucette', dimensions='1m × 2m',
            ),
            'geant': FormatSupport.objects.create(
                code='DASH-GEA', categorie='Géant', dimensions='8m × 6m',
            ),
            'marche': FormatSupport.objects.create(
                code='DASH-MAR', categorie='Marché', dimensions='Marché',
            ),
        }

    def create_support(self, code, format_code, ville='Ouagadougou'):
        return Support.objects.create(
            code=code,
            nom=f'Support {code}',
            type_support=Support.TYPE_PANNEAU,
            format=format_code,
            latitude=12.34,
            longitude=-1.52,
            ville=ville,
        )

    def create_campaign(self, name, start_date, end_date, status):
        return Campagne.objects.create(
            client=self.client_obj,
            nom=name,
            date_debut=start_date,
            date_fin=end_date,
            statut=status,
        )

    def test_supports_and_campaigns_are_classified_from_lines_and_formats(self):
        today = datetime.date.today()
        standard = self.create_support('DASH-P12', 'DASH-12')
        sucette = self.create_support('DASH-PSU', 'DASH-SUC', ville='Bobo-Dioulasso')
        giant = self.create_support('DASH-PGE', 'DASH-GEA')
        FacePanneau.objects.create(support=standard, label='A')

        screen = Support.objects.create(
            code='DASH-ECR',
            nom='Écran dashboard',
            type_support=Support.TYPE_ECRAN,
            latitude=12.34,
            longitude=-1.52,
        )
        EcranNumerique.objects.create(support=screen)

        market = Marche.objects.create(nom='Marché dashboard', ville='Ouagadougou')
        emplacement = Emplacement.objects.create(marche=market, code='D-01')
        market_support = self.create_support('DASH-PMA', 'DASH-MAR')
        market_support.emplacement = emplacement
        market_support.save()

        campaign = self.create_campaign(
            'Campagne multi-supports',
            today,
            today + datetime.timedelta(days=2),
            Campagne.STATUT_EN_COURS,
        )
        LigneCampagne.objects.create(campagne=campaign, face=standard.faces.get(label='A'))
        LigneCampagne.objects.create(campagne=campaign, support=standard)
        LigneCampagne.objects.create(campagne=campaign, support=screen)
        LigneCampagne.objects.create(campagne=campaign, emplacement=emplacement)

        view = DashboardView()
        support_groups, campaign_groups = view._build_support_and_campaign_groups(
            'Ouagadougou', [campaign], []
        )
        supports_by_key = {group['key']: group for group in support_groups}
        campaigns_by_key = {group['key']: group for group in campaign_groups}

        self.assertEqual(supports_by_key['statique_12m2']['nb_supports'], 1)
        self.assertEqual(supports_by_key['statique_sucettes']['nb_supports'], 0)
        self.assertEqual(supports_by_key['statique_geants']['nb_supports'], 1)
        self.assertEqual(supports_by_key['ecrans']['nb_supports'], 1)
        self.assertEqual(supports_by_key['marches']['nb_supports'], 1)
        self.assertEqual(supports_by_key['statique_12m2']['nb_faces'], 1)
        self.assertEqual(supports_by_key['statique_12m2']['nb_faces_bon'], 1)
        self.assertNotIn('supports', supports_by_key['statique_12m2'])

        self.assertEqual(campaigns_by_key['statique_12m2']['campagnes'], [campaign])
        self.assertEqual(campaigns_by_key['ecrans']['campagnes'], [campaign])
        self.assertEqual(campaigns_by_key['marches']['campagnes'], [campaign])
        self.assertEqual(campaigns_by_key['statique_sucettes']['campagnes'], [])

    def test_static_support_groups_report_panel_face_occupancy_individually(self):
        support = self.create_support('DASH-OCCUPANCY', 'DASH-12')
        occupied = FacePanneau.objects.create(
            support=support, label='A', etat=Support.ETAT_BON,
        )
        reserved = FacePanneau.objects.create(
            support=support, label='B', etat=Support.ETAT_BON,
        )
        free_support = self.create_support('DASH-FREE', 'DASH-12')
        free = FacePanneau.objects.create(
            support=free_support, label='A', etat=Support.ETAT_BON,
        )
        broken_support = self.create_support('DASH-BROKEN', 'DASH-12')
        FacePanneau.objects.create(
            support=broken_support, label='A', etat=Support.ETAT_PANNE,
        )

        support_groups, _ = DashboardView()._build_support_and_campaign_groups(
            'Ouagadougou', [], [],
            faces_occupees_ids={occupied.pk},
            faces_reservees_ids={reserved.pk},
        )
        panels = next(
            group for group in support_groups if group['key'] == 'statique_12m2'
        )

        self.assertEqual(panels['nb_supports'], 3)
        self.assertEqual(panels['nb_faces'], 4)
        self.assertEqual(panels['nb_faces_occupees'], 1)
        self.assertEqual(panels['nb_faces_reservees'], 1)
        self.assertEqual(panels['nb_faces_libres'], 1)

    def test_inventory_cards_show_simple_categories_and_market_specific_face_counts(self):
        today = timezone.now().date()
        static_support = self.create_support('DASH-CARD-12', 'DASH-12')
        occupied_face = FacePanneau.objects.create(
            support=static_support, label='A', etat=Support.ETAT_BON,
        )
        FacePanneau.objects.create(
            support=static_support, label='B', etat=Support.ETAT_BON,
        )
        giant_support = self.create_support('DASH-CARD-GIANT', 'DASH-GEA')
        FacePanneau.objects.create(support=giant_support, label='A')

        market = Marche.objects.create(nom='Marché carte', ville='Ouagadougou')
        emplacement = Emplacement.objects.create(marche=market, code='C-01')
        market_support = self.create_support('DASH-CARD-MARKET', 'DASH-MAR')
        market_support.emplacement = emplacement
        market_support.save()
        market_face = FacePanneau.objects.create(
            support=market_support, label='A', etat=Support.ETAT_BON,
        )
        FacePanneau.objects.create(
            support=market_support, label='B', etat=Support.ETAT_BON,
        )
        Marche.objects.create(nom='Marché sans panneau', ville='Ouagadougou')

        screen = Support.objects.create(
            code='DASH-CARD-SCREEN',
            nom='Écran carte',
            type_support=Support.TYPE_ECRAN,
            etat=Support.ETAT_PANNE,
            latitude=12.34,
            longitude=-1.52,
            ville='Ouagadougou',
        )
        EcranNumerique.objects.create(support=screen)
        campaign = self.create_campaign(
            'Campagne carte',
            today,
            today + datetime.timedelta(days=2),
            Campagne.STATUT_EN_COURS,
        )
        LigneCampagne.objects.create(campagne=campaign, face=occupied_face)
        LigneCampagne.objects.create(campagne=campaign, face=market_face)
        cache.clear()
        request = RequestFactory().get('/', {'ville': 'Ouagadougou'})
        view = DashboardView()
        view.setup(request)
        context = view.get_context_data()
        rendered = render_to_string(view.template_name, context, request=request)

        self.assertEqual(context['ecrans_total'], 1)
        self.assertEqual(context['ecrans_panne'], 1)
        self.assertEqual(context['marches_total'], 2)
        self.assertEqual(
            context['marches_stats'],
            [
                {
                    'nom': 'Marché carte',
                    'nb_panneaux': 1,
                    'nb_faces': 2,
                    'nb_faces_occupees': 1,
                    'nb_faces_reservees': 0,
                    'nb_faces_libres': 1,
                },
                {
                    'nom': 'Marché sans panneau',
                    'nb_panneaux': 0,
                    'nb_faces': 0,
                    'nb_faces_occupees': 0,
                    'nb_faces_reservees': 0,
                    'nb_faces_libres': 0,
                },
            ],
        )
        market_face_stats = DashboardView._build_market_stats(
            'Ouagadougou', {market_face.pk}, set()
        )[0]
        self.assertEqual(market_face_stats['nb_faces_occupees'], 1)
        self.assertEqual(market_face_stats['nb_faces_libres'], 1)
        self.assertIn('grid-template-columns: minmax(0, 1fr);', rendered)
        self.assertIn('aria-label="Statistiques par format de panneau"', rendered)
        self.assertIn('12m² · DASH-12', rendered)
        self.assertIn('Géant · DASH-GEA', rendered)
        self.assertIn('DASH-GEA', rendered)
        self.assertNotIn('Panneaux ·', rendered)

        static_section = rendered.split(
            'aria-label="Statistiques par format de panneau"', 1
        )[1].split('class="parc-cats"', 1)[0]
        self.assertNotIn('DASH-MAR', static_section)
        panels_card = static_section.split(
            'aria-label="Format DASH-12"', 1
        )[1].split('</section>', 1)[0]
        self.assertLess(
            panels_card.index('Panneaux</span>'),
            panels_card.index('<strong class="parc-inventory-value">1</strong>'),
        )
        self.assertEqual(panels_card.count('class="parc-inventory-label"'), 4)
        for label in ('Faces', 'Occupées', 'Libres'):
            self.assertIn(f'{label}</span>', panels_card)
        self.assertIn('Écrans</span>', rendered)
        self.assertIn('Écrans au total</span>', rendered)
        self.assertIn('Occupation selon les campagnes en cours.', rendered)
        self.assertIn('Marché carte', rendered)
        self.assertIn('Marché sans panneau', rendered)

    def test_screen_inventory_counts_occupancy_per_resolution(self):
        today = timezone.now().date()
        occupied_screen = Support.objects.create(
            code='DASH-SCREEN-OCCUPIED',
            nom='Écran HD occupé',
            type_support=Support.TYPE_ECRAN,
            latitude=12.34,
            longitude=-1.52,
            ville='Ouagadougou',
        )
        EcranNumerique.objects.create(support=occupied_screen, resolution='hd')
        free_screen = Support.objects.create(
            code='DASH-SCREEN-FREE',
            nom='Écran HD libre',
            type_support=Support.TYPE_ECRAN,
            latitude=12.34,
            longitude=-1.52,
            ville='Ouagadougou',
        )
        EcranNumerique.objects.create(support=free_screen, resolution='hd')
        fullhd_screen = Support.objects.create(
            code='DASH-SCREEN-FULLHD',
            nom='Écran Full HD libre',
            type_support=Support.TYPE_ECRAN,
            latitude=12.34,
            longitude=-1.52,
            ville='Ouagadougou',
        )
        EcranNumerique.objects.create(support=fullhd_screen, resolution='fullhd')
        campaign = self.create_campaign(
            'Campagne écran en cours',
            today,
            today + datetime.timedelta(days=1),
            Campagne.STATUT_EN_COURS,
        )
        LigneCampagne.objects.create(campagne=campaign, support=occupied_screen)

        stats = DashboardView._build_screen_resolution_stats(
            'Ouagadougou', today
        )

        self.assertEqual(
            stats,
            [
                {
                    'resolution': 'HD 1280×720',
                    'nb_ecrans': 2,
                    'nb_occupees': 1,
                    'nb_libres': 1,
                },
                {
                    'resolution': 'Full HD 1920×1080',
                    'nb_ecrans': 1,
                    'nb_occupees': 0,
                    'nb_libres': 1,
                },
            ],
        )

    def test_dashboard_counts_campaigns_separately_and_filters_by_city(self):
        today = timezone.now().date()
        ouaga_support = self.create_support('DASH-OUA', 'DASH-12')
        bobo_support = self.create_support('DASH-BOB', 'DASH-12', ville='Bobo-Dioulasso')
        ouaga_face = FacePanneau.objects.create(support=ouaga_support, label='A')
        bobo_face = FacePanneau.objects.create(support=bobo_support, label='A')

        active_campaign = self.create_campaign(
            'Campagne à Ouagadougou',
            today,
            today + datetime.timedelta(days=2),
            Campagne.STATUT_EN_COURS,
        )
        future_campaign = self.create_campaign(
            'Campagne à Bobo',
            today + datetime.timedelta(days=10),
            today + datetime.timedelta(days=12),
            Campagne.STATUT_A_VENIR,
        )
        LigneCampagne.objects.create(campagne=active_campaign, face=ouaga_face)
        LigneCampagne.objects.create(campagne=future_campaign, face=bobo_face)

        cache.clear()
        request = RequestFactory().get('/', {'ville': 'Ouagadougou'})
        view = DashboardView()
        view.setup(request)
        context = view.get_context_data()
        rendered = render_to_string(view.template_name, context, request=request)

        self.assertEqual(context['total_campagnes'], 1)
        self.assertEqual(context['campagnes_actives'], 1)
        self.assertEqual(context['campagnes_a_venir'], 0)
        self.assertEqual(
            next(group for group in context['support_groups'] if group['key'] == 'statique_12m2')['nb_supports'],
            1,
        )
        self.assertIn('Supports', rendered)
        self.assertIn('Campagnes', rendered)
        self.assertNotIn('Supports au total', rendered)
        self.assertNotIn('Supports recensés', rendered)
        self.assertNotIn('Supports en panne', rendered)
        self.assertIn('Campagnes en cours', rendered)
        self.assertIn('Campagnes à venir', rendered)
        self.assertIn('data-alert-fold="parcAlertsContent"', rendered)
        self.assertIn('aria-expanded="false"', rendered)
        self.assertIn('id="parcAlertsContent" class="d-none"', rendered)
        self.assertNotIn('DASH-OUA', rendered)
        self.assertNotIn('DASH-BOB', rendered)
        self.assertIn('<strong>1</strong><span>Bon état</span>', rendered)

    def test_dashboard_hides_cities_without_classified_supports_and_empty_categories(self):
        self.create_support('DASH-VALID', 'DASH-12')
        Support.objects.create(
            code='DASH-NO-FORMAT',
            nom='Support sans format',
            type_support=Support.TYPE_PANNEAU,
            latitude=12.34,
            longitude=-1.52,
            ville='Ville sans catégorie',
        )
        screen = Support.objects.create(
            code='DASH-SCREEN-CITY',
            nom='Écran dans une ville distincte',
            type_support=Support.TYPE_ECRAN,
            latitude=12.34,
            longitude=-1.52,
            ville='Ville avec écran',
        )
        EcranNumerique.objects.create(support=screen)

        cache.clear()
        request = RequestFactory().get('/', {'ville': 'Ville sans catégorie'})
        view = DashboardView()
        view.setup(request)
        context = view.get_context_data()
        rendered = render_to_string(view.template_name, context, request=request)

        self.assertIn('Ouagadougou', context['villes'])
        self.assertIn('Ville avec écran', context['villes'])
        self.assertNotIn('Ville sans catégorie', context['villes'])
        self.assertEqual(context['ville_active'], context['villes'][0])
        self.assertNotIn(
            'statique_sucettes',
            [group['key'] for group in context['support_groups_present']],
        )
        self.assertLess(
            rendered.index('id="parc-campagnes-title"'),
            rendered.index('id="parc-map-title"'),
        )

    def test_dashboard_defaults_to_city_with_most_supports_and_orders_cities_by_count(self):
        self.create_support('DASH-OUA-1', 'DASH-12', ville='Ouagadougou')
        self.create_support('DASH-OUA-2', 'DASH-12', ville='Ouagadougou')
        self.create_support('DASH-BOB-1', 'DASH-12', ville='Bobo-Dioulasso')

        cache.clear()
        request = RequestFactory().get('/')
        view = DashboardView()
        view.setup(request)
        context = view.get_context_data()
        rendered = render_to_string(view.template_name, context, request=request)
        city_tabs = rendered.split('<nav class="parc-tabs"', 1)[1].split('</nav>', 1)[0]

        self.assertEqual(context['villes'], ['Ouagadougou', 'Bobo-Dioulasso'])
        self.assertEqual(context['ville_active'], 'Ouagadougou')
        self.assertEqual(context['total_supports'], 2)
        self.assertNotIn('>Toutes</a>', city_tabs)
        self.assertLess(
            city_tabs.index('Ouagadougou'),
            city_tabs.index('Bobo-Dioulasso'),
        )

    def test_campaign_categories_follow_city_supports_even_without_campaigns(self):
        self.create_support('DASH-OUA-12', 'DASH-12', ville='Ouagadougou')
        self.create_support('DASH-OUA-SUC', 'DASH-SUC', ville='Ouagadougou')
        screen = Support.objects.create(
            code='DASH-BOB-SCREEN',
            nom='Écran à Bobo',
            type_support=Support.TYPE_ECRAN,
            latitude=11.18,
            longitude=-4.29,
            ville='Bobo-Dioulasso',
        )
        EcranNumerique.objects.create(support=screen)
        cache.clear()

        city_request = RequestFactory().get('/', {'ville': 'Ouagadougou'})
        city_view = DashboardView()
        city_view.setup(city_request)
        city_context = city_view.get_context_data()
        city_categories = city_context['campaign_category_groups_present']
        city_keys = {group['key'] for group in city_categories}

        self.assertEqual(city_keys, {'statique_12m2', 'statique_sucettes'})
        self.assertTrue(all(group['nb_campagnes'] == 0 for group in city_categories))
        city_rendered = render_to_string(
            city_view.template_name, city_context, request=city_request
        )
        campaign_section = city_rendered.split(
            'aria-labelledby="parc-campagnes-title"', 1
        )[1].split('aria-labelledby="parc-map-title"', 1)[0]
        self.assertIn('Statique — 12 m²', campaign_section)
        self.assertIn('Statique — Sucettes', campaign_section)
        self.assertNotIn('Écrans numériques', campaign_section)

        default_request = RequestFactory().get('/')
        default_view = DashboardView()
        default_view.setup(default_request)
        default_context = default_view.get_context_data()
        default_keys = {
            group['key'] for group in default_context['campaign_category_groups_present']
        }
        self.assertEqual(
            default_context['ville_active'], 'Ouagadougou'
        )
        self.assertEqual(
            default_keys, {'statique_12m2', 'statique_sucettes'}
        )


class ContractDocumentTests(TestCase):
    def setUp(self):
        self.client_obj = Client.objects.create(
            nom='Client contrat test',
            telephone='00000000',
            email='contrat-test@example.com',
        )

    def test_contract_reference_is_generated_using_document_type(self):
        contract = Contrat.objects.create(
            client=self.client_obj,
            type_document='contrat',
            date_debut=datetime.date(2026, 1, 1),
            date_fin=datetime.date(2026, 12, 31),
        )
        purchase_order = Contrat.objects.create(
            client=self.client_obj,
            type_document='bon_commande',
            date_debut=datetime.date(2026, 1, 1),
            date_fin=datetime.date(2026, 12, 31),
        )

        self.assertEqual(contract.reference, f'CTR-2026-{contract.pk:06d}')
        self.assertEqual(purchase_order.reference, f'BC-2026-{purchase_order.pk:06d}')
        self.assertNotEqual(contract.reference, purchase_order.reference)


class PreuveDiffusionFormTests(TestCase):
    def setUp(self):
        client_obj = Client.objects.create(
            nom='Client pige test',
            telephone='00000000',
            email='pige-test@example.com',
        )
        self.campagne = Campagne.objects.create(
            client=client_obj,
            nom='Campagne pige test',
            date_debut=datetime.date(2026, 10, 16),
            date_fin=datetime.date(2026, 10, 18),
            type_support='ecran',
        )
        support = Support.objects.create(
            code='PIGE-TEST-001',
            nom='Écran pige test',
            type_support='ecran',
            latitude=12.34,
            longitude=-1.23,
        )
        self.ligne = LigneCampagne.objects.create(
            campagne=self.campagne,
            support=support,
        )

    @staticmethod
    def image_file():
        from io import BytesIO
        from PIL import Image

        image = Image.new('RGB', (2, 2), color='red')
        buffer = BytesIO()
        image.save(buffer, format='PNG')
        return SimpleUploadedFile('preuve.png', buffer.getvalue(), content_type='image/png')

    def test_accepts_photo_for_a_campaign_support(self):
        form = PreuveDiffusionForm(
            data={
                'ligne': self.ligne.pk,
                'date_heure': '2026-10-17T10:15',
                'observations': 'Photo prise sur place',
            },
            files={'photo': self.image_file()},
            campagne=self.campagne,
        )

        self.assertTrue(form.is_valid(), form.errors)
        preuve = form.save(commit=False)
        self.assertEqual(preuve.campagne, self.campagne)
        self.assertEqual(preuve.ligne, self.ligne)

    def test_rejects_a_photo_date_outside_the_campaign_period(self):
        form = PreuveDiffusionForm(
            data={
                'ligne': self.ligne.pk,
                'date_heure': '2026-10-20T10:15',
                'observations': '',
            },
            files={'photo': self.image_file()},
            campagne=self.campagne,
        )

        self.assertFalse(form.is_valid())
        self.assertIn('date_heure', form.errors)

    def test_a_line_with_an_existing_proof_is_not_available_for_a_new_proof(self):
        PreuveDiffusion.objects.create(
            campagne=self.campagne,
            ligne=self.ligne,
            date_heure=datetime.datetime(2026, 10, 17, 10, 15),
            photo='piges/existing-proof.png',
        )

        form = PreuveDiffusionForm(campagne=self.campagne)

        self.assertNotIn(self.ligne, form.fields['ligne'].queryset)

    def test_existing_proof_can_be_edited_without_uploading_another_photo(self):
        preuve = PreuveDiffusion.objects.create(
            campagne=self.campagne,
            ligne=self.ligne,
            date_heure=datetime.datetime(2026, 10, 17, 10, 15),
            photo='piges/existing-proof.png',
        )
        form = PreuveDiffusionForm(
            data={
                'ligne': self.ligne.pk,
                'date_heure': '2026-10-17T11:30',
                'observations': 'Observation corrigée',
            },
            instance=preuve,
            campagne=self.campagne,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['photo'].name, 'piges/existing-proof.png')

    def test_campaign_detail_context_includes_existing_proofs(self):
        preuve = PreuveDiffusion.objects.create(
            campagne=self.campagne,
            ligne=self.ligne,
            date_heure=datetime.datetime(2026, 10, 17, 10, 15),
            photo='piges/existing-proof.png',
        )

        view = CampagneDetailView()
        view.object = self.campagne
        context = view.get_context_data()

        self.assertIn(preuve, context['preuves_diffusion'])

    def test_campaign_detail_context_includes_drive_piges(self):
        pige = PigeFichier.objects.create(
            campagne=self.campagne,
            ligne=self.ligne,
            support=self.ligne.support,
            support_nom='Écran pige test',
            ville='Ouagadougou',
            date_prise=datetime.date(2026, 10, 17),
            nom_fichier='Ouagadougou_Ecran_2026-10-17_1.jpg',
            drive_file_id='drive-file-campaign-detail-test',
        )

        view = CampagneDetailView()
        view.object = self.campagne
        context = view.get_context_data()

        self.assertIn(pige, context['pige_fichiers_drive'])


class NombreVisuelsSpotsTests(SimpleTestCase):
    def test_nombre_visuels_par_defaut_est_un(self):
        campagne = Campagne(
            nom="Campagne test",
            date_debut=datetime.date(2026, 1, 1),
            date_fin=datetime.date(2026, 1, 2),
            type_support="ecran",
            duree_passage=10,
            frequence=120,
            tranches_horaires="08:00-12:00",
        )

        self.assertEqual(campagne.nombre_visuels, 1)

    def test_calcul_des_spots_prend_en_compte_le_nombre_de_visuels(self):
        campagne = Campagne(
            nom="Campagne test",
            date_debut=datetime.date(2026, 1, 1),
            date_fin=datetime.date(2026, 1, 2),
            type_support="ecran",
            duree_passage=10,
            frequence=120,
            tranches_horaires="08:00-12:00",
            nombre_visuels=3,
        )
        support = Support(
            code='TEST-001',
            nom='Support test',
            type_support='ecran',
            latitude=12.34,
            longitude=1.23,
        )
        support.jours_disponibles_sur_periode = MethodType(lambda self, date_debut, date_fin: 2, support)
        ligne = LigneCampagne(campagne=campagne, support=support)

        self.assertEqual(ligne.calculer_spots(), 720)

    def test_simulation_prend_en_compte_le_nombre_de_visuels(self):
        self.assertEqual(_spots(240, 8, 5, 5, nombre_visuels=3), 9000.0)

    def test_calculer_suggestions_objectif(self):
        suggestions = calculer_suggestions_objectif(9600, 9000, 5, 5)

        self.assertEqual(suggestions["manque"], 600)
        self.assertEqual(suggestions["jours_ajout"], 1)
        self.assertEqual(suggestions["ecrans_ajout"], 1)

    def test_objectif_par_ecran_devient_un_objectif_global(self):
        self.assertEqual(calculer_objectif_total(500, 3), 1500)

    def test_objectif_total_n_est_pas_multiplie_par_le_nombre_ecrans(self):
        self.assertEqual(normaliser_objectif_spots(8, objectif_total=4500), 4500)

    def test_un_seul_type_objectif_peut_etre_renseigne(self):
        with self.assertRaises(ValueError):
            normaliser_objectif_spots(8, objectif_par_ecran=500, objectif_total=4500)

    def test_ajustements_meme_periode_atteignent_la_cible_sans_changer_le_parc(self):
        ajustements = generer_ajustements_meme_periode(
            objectif=1500,
            nb_jours=9,
            nb_ecrans=3,
            nombre_visuels=1,
            frequence_actuelle=240,
            heures_actuelles=2,
        )

        self.assertTrue(ajustements)
        self.assertTrue(all(abs(item["ecart_pct"]) <= 5 for item in ajustements))
        self.assertTrue(all(item["tranche_val"].startswith("06:00-") for item in ajustements))

    def test_ajustements_meme_periode_corrigent_aussi_un_surplus(self):
        ajustements = generer_ajustements_meme_periode(
            objectif=4000,
            nb_jours=9,
            nb_ecrans=8,
            nombre_visuels=1,
            frequence_actuelle=300,
            heures_actuelles=7,
        )

        self.assertTrue(ajustements)
        self.assertTrue(all(abs(item["ecart_pct"]) <= 5 for item in ajustements))
        self.assertTrue(all(item["frequence"] != 300 or item["heures"] != 7 for item in ajustements))

    def test_suggestions_extension_ne_depassent_pas_le_parc_selectionne(self):
        combinaisons = generer_combinaisons_complement(
            manque=600,
            nombre_visuels=1,
            ecrans_disponibles=3,
            max_ecrans_ajout=2,
        )

        self.assertTrue(combinaisons)
        self.assertLessEqual(max(item["ecrans_ajout"] for item in combinaisons), 2)

    def test_periode_seule_genere_des_scenarios_pour_plusieurs_nombres_ecrans(self):
        scenarios = generer_scenarios_simulation(
            duree_passage=10,
            nombre_visuels=1,
            nb_jours=9,
            nb_ecrans_candidates=[1, 2],
            frequence_fixe=300,
            tranches_fixes="08:00-10:00",
        )

        self.assertEqual({scenario["nb_ecrans"] for scenario in scenarios}, {1, 2})
        self.assertEqual({scenario["nb_jours"] for scenario in scenarios}, {9})

    def test_objectif_seul_calcule_la_duree_sans_modifier_frequence_ni_tranche(self):
        scenarios = generer_scenarios_simulation(
            duree_passage=10,
            nombre_visuels=1,
            nb_jours=None,
            nb_ecrans_candidates=[1],
            objectif_total=4500,
            frequence_fixe=120,
            tranches_fixes="08:00-18:00",
        )

        self.assertEqual(len(scenarios), 1)
        self.assertEqual(scenarios[0]["nb_jours"], 15)
        self.assertEqual(scenarios[0]["spots_total"], 4500)

    def test_cible_non_atteignable_en_jours_entiers_retourne_les_plus_proches(self):
        scenarios = generer_scenarios_simulation(
            duree_passage=10,
            nombre_visuels=1,
            nb_jours=None,
            nb_ecrans_candidates=[1],
            objectif_total=4500,
            frequence_fixe=60,
            tranches_fixes="08:00-18:00",
        )

        self.assertEqual([scenario["nb_jours"] for scenario in scenarios], [7, 8])
        self.assertTrue(all(not scenario["dans_tolerance"] for scenario in scenarios))

    def test_cible_et_periode_fixes_ajustent_frequence_et_tranche(self):
        scenarios = generer_scenarios_simulation(
            duree_passage=20,
            nombre_visuels=1,
            nb_jours=9,
            nb_ecrans_candidates=[8],
            objectif_total=4000,
            frequence_preferee=300,
            tranche_reference="06:00-09:00,17:00-21:00",
        )

        self.assertTrue(scenarios)
        meilleur = scenarios[0]
        self.assertEqual(meilleur["nb_jours"], 9)
        self.assertEqual(meilleur["nb_ecrans"], 8)
        self.assertLessEqual(abs(meilleur["ecart_pct"]), 5)
        self.assertEqual(meilleur["spots_total"], 4032)
        self.assertEqual(meilleur["frequence"], 900)

    def test_tranche_actuelle_et_frequence_actuelle_peuvent_etre_la_meilleure_proposition(self):
        tranche = "06:00-10:16,16:00-21:00"
        scenarios = generer_scenarios_simulation(
            duree_passage=20,
            nombre_visuels=1,
            nb_jours=9,
            nb_ecrans_candidates=[8],
            objectif_total=4000,
            frequence_preferee=600,
            tranche_reference=tranche,
        )

        meilleur = scenarios[0]
        self.assertEqual(meilleur["frequence"], 600)
        self.assertEqual(meilleur["tranches_val"], tranche)
        self.assertEqual(meilleur["spots_total"], 4003)
        self.assertLess(abs(meilleur["ecart_pct"]), 0.1)

    def test_calculer_repartition_blocs(self):
        repartition = calculer_repartition_blocs(
            nb_jours_principal=5,
            frequence_principale=240,
            heures_principales=8,
            nb_ecrans_principaux=5,
            nombre_visuels=3,
            jours_ajout=1,
            frequence_ajout=240,
            heures_ajout=4,
            nb_ecrans_ajout=2,
        )

        self.assertEqual(repartition["principal"], 9000.0)
        self.assertEqual(repartition["ajout"], 360.0)
        self.assertEqual(repartition["gain"], 360.0)
        self.assertEqual(repartition["total"], 9360.0)
        self.assertEqual(repartition["nb_ecrans_principal"], 5)
        self.assertEqual(repartition["nb_ecrans_ajout"], 2)
        self.assertEqual(repartition["nb_ecrans_total"], 7)

    def test_calculer_total_spots_avec_jours_ajoutes(self):
        total = calculer_total_spots_avec_jours_ajoutes(
            nb_jours_principal=5,
            frequence_principale=240,
            heures_principales=8,
            nb_ecrans_principaux=5,
            nombre_visuels=3,
            jours_ajout=1,
            frequence_ajout=240,
            heures_ajout=4,
            nb_ecrans_ajout=2,
        )

        self.assertEqual(total, 9360.0)

    def test_enrich_resultat_metriques_calculates_required_template_values(self):
        resultats = {
            "spots_total_tous": 15000,
            "heures_tranches": 8,
            "frequence": 120,
            "nombre_visuels": 2,
            "nb_ecrans": 3,
            "nb_jours": 5,
            "duree_passage": 10,
            "spots_ajout": 3000,
            "spots_principal": 12000,
        }

        normalise = _enrich_resultat_metriques(
            resultats,
            nb_jours=5,
            nb_ecrans=3,
            nombre_visuels=2,
            duree_passage=10,
            frequence=120,
            nb_spots_cible=16000,
        )

        self.assertEqual(normalise["spots_par_jour_total"], 3000)
        self.assertEqual(normalise["spots_par_visuel"], 7500)
        self.assertEqual(normalise["part_de_voix_pct"], 11.6)
        self.assertEqual(normalise["temps_pub_par_heure_sec"], 300.0)
        self.assertEqual(normalise["rotation_visuel_str"], "2 min")
        self.assertEqual(normalise["gain_spots"], 3000)
        self.assertEqual(normalise["gain_pct"], 25.0)
        self.assertEqual(normalise["pct_realisation_cible"], 93.8)
