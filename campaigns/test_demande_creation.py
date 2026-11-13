import datetime
import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from campaigns.models import (
    Campagne,
    Client,
    DemandeReservation,
    LigneCampagne,
    Reservation,
    ReservationLigne,
    STATUT_CONFIRMEE,
)
from inventory.models import FacePanneau, FormatSupport, Support


class DemandeCreationViewTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(
            username='demande-creation-staff',
            password='test-password',
            role='staff',
        )
        self.client.force_login(self.staff)
        self.panel = Support.objects.create(
            code='DEMANDE-CREATE-PANEL',
            nom='Panneau test demande',
            type_support=Support.TYPE_PANNEAU,
            latitude=12.34,
            longitude=-1.52,
            ville='Ouagadougou',
        )
        self.face = FacePanneau.objects.create(
            support=self.panel,
            label=FacePanneau.LABEL_A,
        )
        self.start = datetime.date.today() + datetime.timedelta(days=10)
        self.end = self.start + datetime.timedelta(days=5)

    def complete_step_one(self, **overrides):
        data = {
            'faces_selectionnees': json.dumps([self.face.pk]),
            'supports_selectionnees': '[]',
            'date_debut': self.start.isoformat(),
            'date_fin': self.end.isoformat(),
            'type_client': DemandeReservation.TYPE_CLIENT_NOUVEAU,
            'nom_contact': 'Moussa Prospect',
            'societe': 'Nouvelle société',
            'email': 'moussa@example.com',
            'telephone': '71000000',
            'accepte_contact': 'on',
        }
        data.update(overrides)
        return self.client.post(reverse('demande_creation'), data)

    def complete_step_two(self):
        return self.client.post(reverse('demande_creation_etape2'), {})

    def test_format_prices_are_saved_on_demand_and_copied_to_reservation(self):
        standard = FormatSupport.objects.create(
            code='DEMANDE-PRICE-STANDARD',
            categorie='Standard',
            dimensions='4m x 3m',
        )
        geant = FormatSupport.objects.create(
            code='DEMANDE-PRICE-GEANT',
            categorie='Géant',
            dimensions='8m x 4m',
        )
        self.panel.format = standard.code
        self.panel.save(update_fields=['format'])
        other_panel = Support.objects.create(
            code='DEMANDE-PRICE-PANEL-2',
            nom='Panneau grand format',
            type_support=Support.TYPE_PANNEAU,
            format=geant.code,
            latitude=12.35,
            longitude=-1.53,
            ville='Ouagadougou',
        )
        other_face = FacePanneau.objects.create(
            support=other_panel,
            label=FacePanneau.LABEL_A,
        )
        tarifs = {
            standard.code: '12500',
            geant.code: '21000',
        }

        response = self.complete_step_one(
            faces_selectionnees=json.dumps([self.face.pk, other_face.pk]),
            tarifs_faces_par_format=json.dumps(tarifs),
        )
        self.assertRedirects(response, reverse('demande_creation_etape2'))
        self.assertRedirects(
            self.complete_step_two(),
            reverse(
                'demande_detail',
                kwargs={'uuid': DemandeReservation.objects.get().uuid},
            ),
        )
        demande = DemandeReservation.objects.get()
        self.assertEqual(demande.prix_faces_par_format, tarifs)

        response = self.client.post(
            reverse('demande_valider', kwargs={'uuid': demande.uuid}),
        )
        self.assertRedirects(
            response,
            reverse('demande_detail', kwargs={'uuid': demande.uuid}),
        )
        reservation = Reservation.objects.get()
        self.assertEqual(reservation.prix_faces_par_format, tarifs)
        self.assertEqual(reservation.nombre_mois_factures(), 1)
        self.assertEqual(reservation.montant_total(), 33500)
        detail_response = self.client.get(
            reverse(
                'reservation_detail',
                kwargs={
                    'client_pk': reservation.client_id,
                    'resa_pk': reservation.pk,
                },
            )
        )
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, 'Tarif / mois')

    def test_format_price_is_optional_and_retained_when_contact_is_invalid(self):
        format_support = FormatSupport.objects.create(
            code='DEMANDE-PRICE-REQUIRED',
            categorie='Standard',
            dimensions='4m x 3m',
        )
        self.panel.format = format_support.code
        self.panel.save(update_fields=['format'])

        response = self.complete_step_one()

        self.assertRedirects(response, reverse('demande_creation_etape2'))

        response = self.complete_step_one(
            email='adresse-invalide',
            tarifs_faces_par_format=json.dumps({format_support.code: '15000'}),
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'montant mensuel valide')
        self.assertContains(response, '15000')

    def test_campaign_months_use_thirty_day_billing_periods(self):
        debut = datetime.date(2026, 1, 1)
        self.assertEqual(
            Campagne(
                date_debut=debut,
                date_fin=debut + datetime.timedelta(days=14),
            ).nombre_mois_factures(),
            1,
        )
        self.assertEqual(
            Campagne(
                date_debut=debut,
                date_fin=debut + datetime.timedelta(days=34),
            ).nombre_mois_factures(),
            2,
        )
        self.assertEqual(
            Reservation(
                date_debut=timezone.make_aware(
                    datetime.datetime.combine(debut, datetime.time.min)
                ),
                date_fin=timezone.make_aware(
                    datetime.datetime.combine(
                        debut + datetime.timedelta(days=14),
                        datetime.time.max,
                    )
                ),
            ).nombre_mois_factures(),
            1,
        )
        self.assertEqual(
            Reservation(
                date_debut=timezone.make_aware(
                    datetime.datetime.combine(debut, datetime.time.min)
                ),
                date_fin=timezone.make_aware(
                    datetime.datetime.combine(
                        debut + datetime.timedelta(days=34),
                        datetime.time.max,
                    )
                ),
            ).nombre_mois_factures(),
            2,
        )

    def test_staff_location_filters_are_before_broadcast_period(self):
        response = self.client.get(reverse('demande_creation'))
        content = response.content.decode()
        self.assertLess(
            content.index('Filtres des emplacements'),
            content.index('Période de diffusion'),
        )

    def test_staff_tariff_controls_are_below_period_and_outside_selection_panel(self):
        response = self.client.get(reverse('demande_creation'))
        content = response.content.decode()
        period_index = content.index('Période de diffusion')
        tariff_index = content.index('id="tarification-faces"')
        locations_index = content.index('>Emplacements</h2>')
        selection_form_index = content.index('<form method="post" id="form-etape1">')

        self.assertLess(period_index, tariff_index)
        self.assertLess(tariff_index, locations_index)
        self.assertLess(tariff_index, selection_form_index)

    def test_default_demands_list_shows_every_status(self):
        statuts = [statut for statut, _ in DemandeReservation.STATUT_CHOICES]
        for index, statut in enumerate(statuts):
            DemandeReservation.objects.create(
                type_client=DemandeReservation.TYPE_CLIENT_NOUVEAU,
                nom_contact=f'Prospect {index}',
                email=f'prospect-{index}@example.com',
                telephone=f'7100000{index}',
                date_debut_souhaitee=self.start,
                date_fin_souhaitee=self.end,
                statut=statut,
            )
        for index in range(16):
            DemandeReservation.objects.create(
                type_client=DemandeReservation.TYPE_CLIENT_NOUVEAU,
                nom_contact=f'Autre prospect {index}',
                email=f'autre-prospect-{index}@example.com',
                telephone=f'720000{index:02d}',
                date_debut_souhaitee=self.start,
                date_fin_souhaitee=self.end,
                statut=DemandeReservation.STATUT_NOUVELLE,
            )

        response = self.client.get(reverse('demandes_liste'))
        nombre_demandes = len(statuts) + 16

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['demandes'].count(), nombre_demandes)
        self.assertEqual(response.context['compteurs']['total'], nombre_demandes)
        self.assertContains(response, 'data-client-pagination data-page-size="20"')
        self.assertContains(response, 'data-client-pagination-controls')
        self.assertEqual(
            response.content.decode().count('data-client-page-item>'),
            nombre_demandes,
        )
        for statut in statuts:
            self.assertContains(response, f'dl-row dl-st-{statut}')

        filtered_response = self.client.get(
            reverse('demandes_liste'),
            {'statut': DemandeReservation.STATUT_VALIDEE},
        )
        self.assertEqual(filtered_response.context['demandes'].count(), 1)

    def test_confirmation_page_renders_the_styled_status_header(self):
        demande = DemandeReservation.objects.create(
            type_client=DemandeReservation.TYPE_CLIENT_NOUVEAU,
            nom_contact='Prospect confirmation',
            societe='Société confirmation',
            email='confirmation@example.com',
            telephone='70000000',
            date_debut_souhaitee=self.start,
            date_fin_souhaitee=self.end,
            statut=DemandeReservation.STATUT_NOUVELLE,
            token_partage=uuid.uuid4(),
        )
        demande.faces_souhaitees.add(self.face)

        response = self.client.get(
            reverse('portail:confirmation', kwargs={'uuid': demande.uuid})
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'portail/confirmation.html')
        self.assertTrue(response.context['hide_portal_navigation'])
        self.assertContains(response, 'class="confirmation confirmation-header"')
        self.assertContains(response, 'Demande envoyée avec succès !')
        self.assertContains(response, 'Référence de demande')
        self.assertNotContains(response, 'alt="Logo"')
        self.assertNotContains(response, 'class="nav-label">Menu')
        self.assertNotContains(response, 'Suivre une autre demande')
        self.assertNotContains(response, 'Obtenir un devis gratuit')
        self.assertNotContains(response, 'class="cta-final"')
        self.assertContains(response, 'class="main-wrap no-sidebar"')

    def test_client_can_update_and_confirm_shared_request_before_staff_approval(self):
        standard_format = FormatSupport.objects.create(
            code='DEMANDE-CLIENT-STANDARD',
            categorie='Standard',
            dimensions='4m x 3m',
        )
        self.panel.format = standard_format.code
        self.panel.save(update_fields=['format'])
        client_obj = Client.objects.create(
            nom='Annonceur existant',
            contact_nom='Awa Contact',
            telephone='70000000',
            email='awa@example.com',
        )

        self.assertRedirects(
            self.complete_step_one(
                type_client=DemandeReservation.TYPE_CLIENT_EXISTANT,
                client=client_obj.pk,
                nom_contact='',
                societe='',
                email='',
                telephone='',
                accepte_contact='on',
            ),
            reverse('demande_creation_etape2'),
        )
        recap_response = self.client.get(reverse('demande_creation_etape2'))
        self.assertContains(recap_response, 'Awa Contact')
        self.assertContains(recap_response, '70000000')
        self.assertContains(recap_response, 'awa@example.com')

        response = self.complete_step_two()

        self.assertRedirects(
            response,
            reverse('demande_detail', kwargs={'uuid': DemandeReservation.objects.get().uuid}),
        )
        demande = DemandeReservation.objects.get()
        self.assertEqual(demande.statut, DemandeReservation.STATUT_EN_COURS)
        self.assertEqual(demande.client, client_obj)
        self.assertEqual(demande.reference_client_saisie, client_obj.reference)
        self.assertEqual(demande.nom_contact, 'Awa Contact')
        self.assertEqual(demande.societe, 'Annonceur existant')
        self.assertEqual(demande.nom_campagne, '')
        self.assertEqual(demande.message, '')
        self.assertTrue(demande.accepte_contact)
        self.assertEqual(list(demande.faces_souhaitees.all()), [self.face])

        detail_response = self.client.get(
            reverse('demande_detail', kwargs={'uuid': demande.uuid})
        )
        self.assertContains(detail_response, 'Autoriser la modification client')
        self.assertContains(detail_response, 'Autoriser la modification client')
        self.assertContains(detail_response, 'Créer une réservation')
        self.assertContains(detail_response, 'Refuser la demande')
        self.assertNotContains(detail_response, 'id="demande-share-url"')
        self.assertRedirects(
            self.client.get(
                reverse('portail:demande_partage', kwargs={'token': demande.token_partage})
            ),
            reverse('portail:confirmation', kwargs={'uuid': demande.uuid}),
        )
        response = self.client.post(
            reverse('demande_autoriser_modification', kwargs={'uuid': demande.uuid}),
        )
        self.assertRedirects(
            response,
            reverse('demande_detail', kwargs={'uuid': demande.uuid}),
        )
        demande.refresh_from_db()
        self.assertEqual(demande.statut, DemandeReservation.STATUT_BROUILLON)
        detail_response = self.client.get(
            reverse('demande_detail', kwargs={'uuid': demande.uuid})
        )
        self.assertContains(detail_response, 'id="demande-share-url"')
        self.assertContains(detail_response, 'Désactiver la modification client')
        self.assertNotContains(detail_response, 'Modification gestionnaire')
        self.assertNotContains(detail_response, 'Créer une réservation')
        self.assertNotContains(detail_response, 'Refuser la demande')
        self.assertRedirects(
            self.client.get(
                reverse('demande_modifier', kwargs={'uuid': demande.uuid})
            ),
            reverse('demande_detail', kwargs={'uuid': demande.uuid}),
        )
        self.client.post(
            reverse('demande_autoriser_modification', kwargs={'uuid': demande.uuid}),
        )
        demande.refresh_from_db()
        self.assertEqual(demande.statut, DemandeReservation.STATUT_EN_COURS)
        detail_response = self.client.get(
            reverse('demande_detail', kwargs={'uuid': demande.uuid})
        )
        self.assertContains(detail_response, 'Autoriser la modification client')
        self.assertContains(detail_response, 'Modifier')
        self.assertRedirects(
            self.client.get(
                reverse('portail:demande_partage', kwargs={'token': demande.token_partage})
            ),
            reverse('portail:confirmation', kwargs={'uuid': demande.uuid}),
        )
        self.client.post(
            reverse('demande_autoriser_modification', kwargs={'uuid': demande.uuid}),
        )
        demande.refresh_from_db()
        self.assertEqual(demande.statut, DemandeReservation.STATUT_BROUILLON)

        other_panel = Support.objects.create(
            code='DEMANDE-CLIENT-EDIT-PANEL',
            nom='Panneau modifié par le client',
            type_support=Support.TYPE_PANNEAU,
            format=standard_format.code,
            latitude=12.35,
            longitude=-1.53,
            ville='Ouagadougou',
        )
        other_face = FacePanneau.objects.create(
            support=other_panel,
            label=FacePanneau.LABEL_A,
        )
        other_category_format = FormatSupport.objects.create(
            code='DEMANDE-CLIENT-SUCETTE',
            categorie='Sucette',
            dimensions='1m x 2m',
        )
        other_category_panel = Support.objects.create(
            code='DEMANDE-CLIENT-OTHER-CATEGORY',
            nom='Panneau autre catégorie',
            type_support=Support.TYPE_PANNEAU,
            format=other_category_format.code,
            latitude=12.36,
            longitude=-1.54,
            ville='Ouagadougou',
        )
        other_category_face = FacePanneau.objects.create(
            support=other_category_panel,
            label=FacePanneau.LABEL_A,
        )
        updated_start = self.start + datetime.timedelta(days=2)
        updated_end = updated_start + datetime.timedelta(days=7)
        token = demande.token_partage

        self.client.logout()
        shared_page = self.client.get(
            reverse('portail:demande_partage', kwargs={'token': token})
        )
        self.assertEqual(shared_page.status_code, 200)
        self.assertTrue(shared_page.context['shared_edit_mode'])
        self.assertNotContains(shared_page, 'Modification de la demande')
        self.assertNotContains(shared_page, 'Vos coordonnées et la période sont affichées sans modification possible.')
        self.assertNotContains(shared_page, '<div class="cta-final">')
        self.assertContains(shared_page, 'Vos coordonnées')
        self.assertContains(shared_page, 'Nom du contact')
        self.assertContains(shared_page, 'Catégorie fixe')
        self.assertContains(shared_page, 'Standard')
        self.assertContains(shared_page, 'id="initial-selected-faces"')
        self.assertContains(shared_page, f'"id": "{self.face.pk}"')
        self.assertLess(
            shared_page.content.index(b'id="initial-selected-faces"'),
            shared_page.content.index(b'<script>\n  "use strict";'),
        )
        self.assertNotContains(shared_page, 'id="filter-categorie"')
        self.assertNotContains(shared_page, 'id="list-filter-categorie"')
        self.assertNotContains(shared_page, 'step-circle">3')
        self.assertNotContains(shared_page, 'name="nom_contact"')
        self.assertNotContains(shared_page, 'name="type_client"')
        self.assertContains(shared_page, 'id="date-debut"')
        self.assertContains(shared_page, 'disabled aria-label="Date de début non modifiable"')
        self.assertNotContains(shared_page, 'Choisir une catégorie')
        self.assertNotContains(shared_page, '<aside class="sidebar"')
        self.assertNotContains(shared_page, '<header class="topbar">')
        category_change_response = self.client.post(
            reverse('portail:demande_partage', kwargs={'token': token}),
            {
                'faces_selectionnees': json.dumps([other_category_face.pk]),
                'supports_selectionnees': '[999999]',
                'date_debut': updated_start.isoformat(),
                'date_fin': updated_end.isoformat(),
                'type_client': DemandeReservation.TYPE_CLIENT_NOUVEAU,
                'nom_contact': 'Coordonnée interdite',
                'societe': 'Nouvelle société',
                'email': 'adresse-invalide',
                'telephone': '71111111',
            },
        )
        self.assertEqual(category_change_response.status_code, 200)
        self.assertContains(
            category_change_response,
            'Vous ne pouvez sélectionner que des faces de la catégorie',
        )

        response = self.client.post(
            reverse('portail:demande_partage', kwargs={'token': token}),
            {
                'faces_selectionnees': json.dumps([other_face.pk]),
                'supports_selectionnees': '[999999]',
                'date_debut': updated_start.isoformat(),
                'date_fin': updated_end.isoformat(),
                'type_client': DemandeReservation.TYPE_CLIENT_NOUVEAU,
                'nom_contact': 'Coordonnée interdite',
                'societe': 'Nouvelle société',
                'email': 'adresse-invalide',
                'telephone': '71111111',
            },
        )
        self.assertRedirects(
            response,
            reverse('portail:demande_partage_etape2', kwargs={'token': token}),
        )
        recap_response = self.client.get(
            reverse('portail:demande_partage_etape2', kwargs={'token': token})
        )
        self.assertTemplateUsed(recap_response, 'portail/demande_partage_recap.html')
        self.assertContains(recap_response, 'Vérifiez le récapitulatif de votre demande')
        self.assertNotContains(recap_response, '<div class="cta-final">')
        self.assertContains(recap_response, 'aria-label="Étapes de création de la demande"')
        self.assertContains(recap_response, 'Client, période &amp; emplacements')
        self.assertContains(recap_response, 'Récapitulatif &amp; validation')
        self.assertContains(recap_response, 'aria-current="step"')
        self.assertContains(recap_response, 'Awa Contact')
        self.assertNotContains(recap_response, 'Coordonnée interdite')
        self.assertNotContains(recap_response, 'Nouvelle société')
        self.assertContains(recap_response, self.start.isoformat())
        self.assertNotContains(recap_response, updated_start.isoformat())
        self.assertNotContains(recap_response, 'Informations sur votre campagne')
        response = self.client.post(
            reverse('portail:demande_partage_etape2', kwargs={'token': token}),
            {},
        )
        self.assertRedirects(
            response,
            reverse('portail:confirmation', kwargs={'uuid': demande.uuid}),
        )

        demande.refresh_from_db()
        self.assertEqual(demande.statut, DemandeReservation.STATUT_NOUVELLE)
        self.assertEqual(demande.client, client_obj)
        self.assertEqual(
            demande.type_client,
            DemandeReservation.TYPE_CLIENT_EXISTANT,
        )
        self.assertEqual(demande.date_debut_souhaitee, self.start)
        self.assertEqual(demande.date_fin_souhaitee, self.end)
        self.assertEqual(demande.nom_campagne, '')
        self.assertEqual(demande.nom_contact, 'Awa Contact')
        self.assertEqual(demande.email, 'awa@example.com')
        self.assertEqual(demande.telephone, '70000000')
        self.assertFalse(demande.supports_souhaites.exists())
        self.assertEqual(list(demande.faces_souhaitees.all()), [other_face])
        self.assertFalse(Reservation.objects.exists())
        self.assertRedirects(
            self.client.get(
                reverse('portail:demande_partage', kwargs={'token': token})
            ),
            reverse('portail:confirmation', kwargs={'uuid': demande.uuid}),
        )

        self.client.force_login(self.staff)
        detail_response = self.client.get(
            reverse('demande_detail', kwargs={'uuid': demande.uuid})
        )
        self.assertContains(detail_response, 'Créer une réservation')
        self.assertContains(detail_response, 'Refuser la demande')
        self.assertContains(detail_response, 'Autoriser la modification client')
        self.assertNotContains(detail_response, 'id="demande-share-url"')
        self.client.post(
            reverse('demande_autoriser_modification', kwargs={'uuid': demande.uuid}),
        )
        detail_response = self.client.get(
            reverse('demande_detail', kwargs={'uuid': demande.uuid})
        )
        self.assertNotContains(detail_response, 'Modification gestionnaire')
        self.assertContains(detail_response, 'Désactiver la modification client')
        self.assertRedirects(
            self.client.get(
                reverse('demande_modifier', kwargs={'uuid': demande.uuid})
            ),
            reverse('demande_detail', kwargs={'uuid': demande.uuid}),
        )
        self.client.post(
            reverse('demande_autoriser_modification', kwargs={'uuid': demande.uuid}),
        )
        demande.refresh_from_db()
        self.assertEqual(demande.statut, DemandeReservation.STATUT_EN_COURS)
        self.client.post(
            reverse('demande_autoriser_modification', kwargs={'uuid': demande.uuid}),
        )
        self.assertEqual(
            self.client.get(
                reverse('portail:demande_partage', kwargs={'token': token})
            ).status_code,
            200,
        )
        demande.refresh_from_db()
        self.assertEqual(demande.statut, DemandeReservation.STATUT_BROUILLON)

        second_start = updated_start + datetime.timedelta(days=1)
        second_end = second_start + datetime.timedelta(days=3)
        response = self.client.post(
            reverse('portail:demande_partage', kwargs={'token': token}),
            {
                'faces_selectionnees': json.dumps([other_face.pk]),
                'supports_selectionnees': '[]',
                'date_debut': second_start.isoformat(),
                'date_fin': second_end.isoformat(),
                'type_client': DemandeReservation.TYPE_CLIENT_NOUVEAU,
                'nom_contact': 'Awa mise à jour',
                'societe': 'Nouvelle société',
                'email': 'awa.updated@example.com',
                'telephone': '71111111',
            },
        )
        self.assertRedirects(
            response,
            reverse('portail:demande_partage_etape2', kwargs={'token': token}),
        )
        response = self.client.post(
            reverse('portail:demande_partage_etape2', kwargs={'token': token}),
            {},
        )
        self.assertRedirects(
            response,
            reverse('portail:confirmation', kwargs={'uuid': demande.uuid}),
        )
        demande.refresh_from_db()
        self.assertEqual(demande.statut, DemandeReservation.STATUT_NOUVELLE)
        self.assertEqual(demande.date_debut_souhaitee, self.start)

        response = self.client.post(
            reverse('demande_valider', kwargs={'uuid': demande.uuid}),
        )
        self.assertRedirects(
            response,
            reverse('demande_detail', kwargs={'uuid': demande.uuid}),
        )
        demande.refresh_from_db()
        self.assertEqual(demande.statut, DemandeReservation.STATUT_VALIDEE)
        self.assertTrue(Reservation.objects.filter(pk=demande.reservation_id).exists())

    def test_staff_creates_prospect_request_in_progress_until_shared(self):
        self.assertRedirects(
            self.complete_step_one(),
            reverse('demande_creation_etape2'),
        )
        response = self.complete_step_two()

        demande = DemandeReservation.objects.get()
        self.assertRedirects(
            response,
            reverse('demande_detail', kwargs={'uuid': demande.uuid}),
        )
        self.assertEqual(demande.statut, DemandeReservation.STATUT_EN_COURS)
        self.assertIsNotNone(demande.client)
        self.assertEqual(demande.type_client, DemandeReservation.TYPE_CLIENT_NOUVEAU)
        self.assertEqual(demande.client.type_entite, Client.TYPE_CLIENT)
        self.assertEqual(demande.client.nom, 'Nouvelle société')
        self.assertEqual(demande.client.contact_nom, 'Moussa Prospect')
        self.assertEqual(demande.client.email, 'moussa@example.com')
        self.assertEqual(demande.client.telephone, '71000000')
        self.assertTrue(demande.accepte_contact)
        self.assertEqual(demande.nom_contact, 'Moussa Prospect')
        self.assertEqual(demande.nom_campagne, '')
        self.assertEqual(list(demande.faces_souhaitees.all()), [self.face])
        detail_response = self.client.get(
            reverse('demande_detail', kwargs={'uuid': demande.uuid})
        )
        self.assertContains(detail_response, 'Créer une réservation')
        self.assertContains(detail_response, 'Refuser la demande')
        self.assertFalse(Reservation.objects.exists())

    def test_request_approval_reuses_automatically_created_client(self):
        Client.objects.create(
            nom='Ancien compte avec le même e-mail',
            contact_nom='Autre contact',
            telephone='70000001',
            email='moussa@example.com',
        )
        self.assertRedirects(
            self.complete_step_one(),
            reverse('demande_creation_etape2'),
        )
        self.complete_step_two()
        demande = DemandeReservation.objects.get()
        client_cree = demande.client
        self.assertIsNotNone(client_cree)

        response = self.client.post(
            reverse('demande_valider', kwargs={'uuid': demande.uuid}),
        )

        self.assertRedirects(
            response,
            reverse('demande_detail', kwargs={'uuid': demande.uuid}),
        )
        demande.refresh_from_db()
        self.assertEqual(demande.client, client_cree)
        self.assertEqual(demande.reservation.client, client_cree)
        self.assertEqual(Client.objects.count(), 2)

    def test_staff_can_edit_confirmed_request_without_reenabling_client_link(self):
        demande = DemandeReservation.objects.create(
            type_client=DemandeReservation.TYPE_CLIENT_NOUVEAU,
            nom_contact='Client prospect',
            societe='Société initiale',
            email='client@example.com',
            telephone='70000000',
            date_debut_souhaitee=self.start,
            date_fin_souhaitee=self.end,
            nom_campagne='Campagne initiale',
            message='Informations déjà enregistrées',
            statut=DemandeReservation.STATUT_NOUVELLE,
            token_partage=uuid.uuid4(),
        )
        standard_format = FormatSupport.objects.create(
            code='DEMANDE-GESTIONNAIRE-STANDARD',
            categorie='Standard',
            dimensions='4m x 3m',
        )
        self.panel.format = standard_format.code
        self.panel.save(update_fields=['format'])
        demande.faces_souhaitees.add(self.face)
        detail_url = reverse('demande_detail', kwargs={'uuid': demande.uuid})
        token = demande.token_partage

        self.assertContains(self.client.get(detail_url), 'Modifier')
        self.assertRedirects(
            self.client.get(reverse('portail:demande_partage', kwargs={'token': token})),
            reverse('portail:confirmation', kwargs={'uuid': demande.uuid}),
        )
        self.assertRedirects(
            self.client.get(reverse('demande_modifier', kwargs={'uuid': demande.uuid})),
            f"{reverse('demande_creation')}?edit=1",
        )
        response = self.client.get(reverse('demande_creation') + '?edit=1')
        self.assertContains(response, 'Modifier la demande')
        self.assertContains(response, 'aria-label="Étapes de création de la demande"')
        self.assertContains(response, 'aria-current="step"')
        self.assertContains(response, 'Client, période & emplacements')
        self.assertContains(response, 'Récapitulatif & validation')
        self.assertContains(response, 'id="initial-selected-faces"')
        self.assertContains(response, f'"id": "{self.face.pk}"')
        self.assertEqual(response.context['filtres']['categorie'], 'Standard')
        self.assertEqual(response.context['filtres']['format'], standard_format.code)
        response = self.client.post(
            reverse('demande_creation'),
            {
                'faces_selectionnees': json.dumps([self.face.pk]),
                'supports_selectionnees': '[]',
                'date_debut': (self.start + datetime.timedelta(days=1)).isoformat(),
                'date_fin': (self.end + datetime.timedelta(days=1)).isoformat(),
                'type_client': DemandeReservation.TYPE_CLIENT_NOUVEAU,
                'nom_contact': 'Nom corrigé par le gestionnaire',
                'societe': 'Société corrigée',
                'email': 'corrige@example.com',
                'telephone': '71111111',
                'accepte_contact': 'on',
            },
        )
        self.assertRedirects(response, reverse('demande_creation_etape2'))
        self.assertContains(self.client.get(reverse('demande_creation_etape2')), 'Modifier les informations')
        response = self.complete_step_two()

        self.assertRedirects(response, detail_url)
        demande.refresh_from_db()
        self.assertEqual(demande.nom_contact, 'Nom corrigé par le gestionnaire')
        self.assertEqual(demande.societe, 'Société corrigée')
        self.assertEqual(demande.email, 'corrige@example.com')
        self.assertEqual(demande.nom_campagne, 'Campagne initiale')
        self.assertEqual(demande.message, 'Informations déjà enregistrées')
        self.assertEqual(
            demande.date_debut_souhaitee,
            self.start + datetime.timedelta(days=1),
        )
        self.assertEqual(demande.statut, DemandeReservation.STATUT_NOUVELLE)
        self.assertRedirects(
            self.client.get(reverse('portail:demande_partage', kwargs={'token': token})),
            reverse('portail:confirmation', kwargs={'uuid': demande.uuid}),
        )

    def test_create_new_request_clears_a_previous_edit_session(self):
        existing = DemandeReservation.objects.create(
            type_client=DemandeReservation.TYPE_CLIENT_NOUVEAU,
            nom_contact='Prospect existant',
            societe='Société existante',
            email='existant@example.com',
            telephone='70000000',
            date_debut_souhaitee=self.start,
            date_fin_souhaitee=self.end,
            statut=DemandeReservation.STATUT_NOUVELLE,
            token_partage=uuid.uuid4(),
        )
        existing.faces_souhaitees.add(self.face)
        self.client.get(reverse('demande_modifier', kwargs={'uuid': existing.uuid}))
        edit_page = self.client.get(reverse('demande_creation') + '?edit=1')
        self.assertTrue(edit_page.context['editing_request'])

        new_page = self.client.get(reverse('demande_creation'))
        self.assertFalse(new_page.context['editing_request'])
        self.assertFalse(self.client.session.get('gestion_demande_edit_uuid'))

        self.assertRedirects(
            self.complete_step_one(),
            reverse('demande_creation_etape2'),
        )
        response = self.complete_step_two()
        created = DemandeReservation.objects.exclude(pk=existing.pk).get()

        self.assertRedirects(
            response,
            reverse('demande_detail', kwargs={'uuid': created.uuid}),
        )
        existing.refresh_from_db()
        self.assertEqual(existing.nom_contact, 'Prospect existant')
        self.assertEqual(existing.statut, DemandeReservation.STATUT_NOUVELLE)

    def test_steps_render_the_portal_wizard_for_staff(self):
        response = self.client.get(reverse('demande_creation'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Informations client, période et emplacements')
        self.assertContains(response, 'Client et coordonnées')
        self.assertContains(response, 'name="type_client"')
        self.assertNotContains(response, 'name="nom_campagne"')
        self.assertNotContains(response, 'Message / précisions')
        self.assertNotContains(response, 'Tous les quartiers')
        self.assertNotContains(response, 'Tous états')
        self.assertContains(response, 'Choisir une catégorie')
        self.assertContains(response, 'aria-required="true"')
        self.assertContains(response, 'id="filter-format-wrap"')
        self.assertContains(response, 'id="list-filter-format-wrap"')
        self.assertTrue(response.context['contact_form']['accepte_contact'].value())
        self.assertContains(response, 'Carte Interactive')
        self.assertNotContains(response, 'Nos Supports')

        self.assertRedirects(self.complete_step_one(), reverse('demande_creation_etape2'))
        response = self.client.get(reverse('demande_creation_etape2'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Récapitulatif de la demande')
        self.assertContains(response, 'Nouveau prospect')
        self.assertContains(response, 'Valider et envoyer la demande')
        self.assertNotContains(response, 'Modifier les informations du demandeur')

        list_response = self.client.get(reverse('demandes_liste'))
        self.assertContains(list_response, 'Créer une demande')

    def test_invalid_contact_keeps_the_user_on_the_combined_first_step(self):
        response = self.complete_step_one(email='adresse-invalide')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'adresse-invalide')
        self.assertContains(response, 'id="initial-selected-faces"')
        self.assertContains(response, f'"id": "{self.face.pk}"')
        self.assertTrue(response.context['contact_form'].errors['email'])
        self.assertFalse(DemandeReservation.objects.exists())
        session = self.client.session
        self.assertEqual(session['gestion_demande_faces'], [self.face.pk])
        self.assertEqual(
            session['gestion_demande_periode']['date_debut'],
            self.start.isoformat(),
        )

    def test_invalid_dates_keep_selected_faces_for_correction(self):
        response = self.complete_step_one(date_fin=self.start.isoformat())

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].errors['date_fin'])
        self.assertContains(response, 'id="initial-selected-faces"')
        self.assertContains(response, f'"id": "{self.face.pk}"')
        self.assertEqual(
            self.client.session['gestion_demande_faces'],
            [self.face.pk],
        )
        self.assertFalse(DemandeReservation.objects.exists())

    def test_public_step_three_keeps_its_reference_based_client_flow(self):
        session = self.client.session
        session['demande_faces_uuids'] = [self.face.pk]
        session['demande_supports_uuids'] = []
        session['demande_periode'] = {
            'date_debut': self.start.isoformat(),
            'date_fin': self.end.isoformat(),
        }
        session['demande_etape2'] = {}
        session.save()

        response = self.client.get(reverse('portail:reserver_etape3'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="reference_client_saisie"')
        self.assertNotContains(response, 'Client enregistré')

    def test_public_reservation_keeps_optional_category_filter(self):
        response = self.client.get(reverse('portail:reserver_etape1'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Toutes catégories')
        self.assertNotContains(response, 'aria-required="true"')

    def test_non_staff_cannot_access_any_creation_step(self):
        self.client.logout()
        client_user = get_user_model().objects.create_user(
            username='demande-creation-client',
            password='test-password',
            role='client',
        )
        self.client.force_login(client_user)

        for route_name in (
            'demande_creation',
            'demande_creation_etape2',
            'demande_creation_etape3',
        ):
            with self.subTest(route=route_name):
                response = self.client.get(reverse(route_name))
                self.assertEqual(response.status_code, 403)

    def test_screen_request_is_not_partially_validated_as_panel_reservation(self):
        screen = Support.objects.create(
            code='DEMANDE-CREATE-SCREEN',
            nom='Écran test demande',
            type_support=Support.TYPE_ECRAN,
            latitude=12.34,
            longitude=-1.52,
            ville='Ouagadougou',
        )
        demande = DemandeReservation.objects.create(
            type_client=DemandeReservation.TYPE_CLIENT_NOUVEAU,
            nom_contact='Écran Prospect',
            societe='Société écran',
            email='screen@example.com',
            telephone='72000000',
            date_debut_souhaitee=self.start,
            date_fin_souhaitee=self.end,
        )
        demande.supports_souhaites.add(screen)

        detail_url = reverse('demande_detail', kwargs={'uuid': demande.uuid})
        detail_response = self.client.get(detail_url)
        self.assertContains(
            detail_response,
            'Cette demande comprend un écran numérique',
        )
        self.assertNotContains(detail_response, 'Créer une réservation')

        response = self.client.post(
            reverse('demande_valider', kwargs={'uuid': demande.uuid}),
        )

        self.assertRedirects(response, detail_url)
        demande.refresh_from_db()
        self.assertEqual(demande.statut, DemandeReservation.STATUT_EN_COURS)
        self.assertFalse(Reservation.objects.exists())

    def test_request_can_create_campaign_from_same_client_reservation_without_changing_it(self):
        agency = Client.objects.create(
            nom='Agence de la demande',
            contact_nom='Awa Contact',
            telephone='70000000',
            email='agency@example.com',
        )
        other_agency = Client.objects.create(
            nom='Autre agence',
            telephone='70000001',
            email='other-agency@example.com',
        )
        format_support = FormatSupport.objects.create(
            code='DEMANDE-CAMPAGNE-FORMAT',
            categorie='Standard',
            dimensions='4m x 3m',
        )
        self.panel.format = format_support.code
        self.panel.save(update_fields=['format'])
        other_panel = Support.objects.create(
            code='DEMANDE-OTHER-PANEL',
            nom='Panneau autre agence',
            type_support=Support.TYPE_PANNEAU,
            format=format_support.code,
            latitude=12.35,
            longitude=-1.53,
            ville='Ouagadougou',
        )
        other_face = FacePanneau.objects.create(
            support=other_panel,
            label=FacePanneau.LABEL_A,
        )
        reservation_start = timezone.make_aware(
            datetime.datetime.combine(
                self.start - datetime.timedelta(days=1),
                datetime.time.min,
            )
        )
        reservation_end = timezone.make_aware(
            datetime.datetime.combine(
                self.end + datetime.timedelta(days=1),
                datetime.time.max,
            )
        )
        reservation = Reservation.objects.create(
            nom='Réservation existante agence',
            client=agency,
            date_debut=reservation_start,
            date_fin=reservation_end,
            statut=STATUT_CONFIRMEE,
        )
        ReservationLigne.objects.create(
            reservation=reservation,
            support=self.panel,
            face=self.face,
        )
        other_reservation = Reservation.objects.create(
            nom='Réservation autre agence',
            client=other_agency,
            date_debut=reservation_start,
            date_fin=reservation_end,
            statut=STATUT_CONFIRMEE,
        )
        ReservationLigne.objects.create(
            reservation=other_reservation,
            support=other_panel,
            face=other_face,
        )
        demande = DemandeReservation.objects.create(
            type_client=DemandeReservation.TYPE_CLIENT_EXISTANT,
            client=agency,
            nom_contact='Awa Contact',
            societe=agency.nom,
            email=agency.email,
            telephone=agency.telephone,
            nom_campagne='Campagne depuis demande',
            date_debut_souhaitee=self.start,
            date_fin_souhaitee=self.end,
            statut=DemandeReservation.STATUT_EN_COURS,
            prix_faces_par_format={format_support.code: '12500'},
        )
        demande.faces_souhaitees.add(self.face)

        detail_response = self.client.get(
            reverse('demande_detail', kwargs={'uuid': demande.uuid})
        )
        self.assertContains(detail_response, 'Réservations du client sur cette période')
        self.assertContains(detail_response, reservation.reference)
        self.assertContains(detail_response, 'Face A')
        self.assertContains(detail_response, 'Créer une campagne')

        campaign_url = f"{reverse('campagne_create')}?demande={demande.uuid}"
        form_response = self.client.get(campaign_url)
        self.assertEqual(form_response.status_code, 200)
        form_reservations = form_response.context['form'].reservations_existantes
        self.assertEqual([item.pk for item in form_reservations], [reservation.pk])

        invalid_response = self.client.post(
            campaign_url,
            {
                'demande_uuid': str(demande.uuid),
                'client': str(other_agency.pk),
                'nom': 'Campagne depuis demande',
                'date_debut': self.start.isoformat(),
                'date_fin': self.end.isoformat(),
                'type_support': format_support.code,
                'nombre_visuels': '1',
                'reservation_reference': other_reservation.reference,
            },
        )
        self.assertEqual(invalid_response.status_code, 200)
        self.assertIn('reservation_reference', invalid_response.context['form'].errors)
        self.assertFalse(Campagne.objects.exists())

        response = self.client.post(
            campaign_url,
            {
                'demande_uuid': str(demande.uuid),
                'client': str(agency.pk),
                'annonceur': agency.nom,
                'nom': 'Campagne depuis demande',
                'date_debut': self.start.isoformat(),
                'date_fin': self.end.isoformat(),
                'type_support': 'Standard',
                'nombre_visuels': '1',
                'reservation_reference': reservation.reference,
            },
        )
        self.assertEqual(response.status_code, 302)

        campagne = Campagne.objects.get(nom='Campagne depuis demande')
        self.assertRedirects(
            response,
            reverse('campagne_detail', kwargs={'pk': campagne.pk}),
        )
        demande.refresh_from_db()
        reservation.refresh_from_db()
        self.assertEqual(demande.statut, DemandeReservation.STATUT_VALIDEE)
        self.assertEqual(demande.campagne, campagne)
        self.assertIsNone(demande.reservation)
        self.assertEqual(campagne.client, agency)
        self.assertEqual(
            campagne.prix_faces_par_format,
            {format_support.code: '12500'},
        )
        self.assertEqual(campagne.montant_total(), 12500)
        self.assertTrue(LigneCampagne.objects.filter(campagne=campagne, face=self.face).exists())
        self.assertEqual(reservation.lignes.count(), 1)
        self.assertEqual(Reservation.objects.count(), 2)
        self.assertContains(
            self.client.get(reverse('demande_detail', kwargs={'uuid': demande.uuid})),
            f'Voir la campagne {campagne.reference}',
        )

    def test_demand_reference_in_reservation_field_prefills_campaign(self):
        agency = Client.objects.create(
            nom='Agence demande saisie',
            contact_nom='Mariam Contact',
            telephone='70000002',
            email='mariam@example.com',
        )
        format_support = FormatSupport.objects.create(
            code='DREF-4X3',
            categorie='Standard',
            dimensions='4m x 3m',
        )
        support = Support.objects.create(
            code='DEMANDE-REFERENCE-PANEL',
            nom='Panneau demande référence',
            type_support=Support.TYPE_PANNEAU,
            format=format_support.code,
            latitude=12.34,
            longitude=-1.52,
            ville='Bobo-Dioulasso',
        )
        face = FacePanneau.objects.create(
            support=support,
            label=FacePanneau.LABEL_A,
        )
        demande = DemandeReservation.objects.create(
            type_client=DemandeReservation.TYPE_CLIENT_EXISTANT,
            client=agency,
            nom_contact='Mariam Contact',
            societe=agency.nom,
            email=agency.email,
            telephone=agency.telephone,
            nom_campagne='Campagne demande référence',
            date_debut_souhaitee=self.start,
            date_fin_souhaitee=self.end,
            statut=DemandeReservation.STATUT_EN_COURS,
        )
        demande.faces_souhaitees.add(face)

        campaign_url = reverse('campagne_create')
        form_response = self.client.get(campaign_url)
        self.assertContains(form_response, demande.reference)
        self.assertContains(form_response, 'data-lieu="Bobo-Dioulasso"')
        self.assertContains(form_response, 'data-categorie="Standard"')

        response = self.client.post(
            campaign_url,
            {
                'reservation_reference': demande.reference,
                'client': str(agency.pk),
                'annonceur': agency.nom,
                'nom': demande.nom_campagne,
                'lieu': 'Bobo-Dioulasso',
                'date_debut': self.start.isoformat(),
                'date_fin': self.end.isoformat(),
                'type_support': format_support.code,
                'nombre_visuels': '1',
            },
        )

        self.assertEqual(
            response.status_code,
            302,
            response.context['form'].errors if response.status_code == 200 else '',
        )
        campagne = Campagne.objects.get(nom=demande.nom_campagne)
        demande.refresh_from_db()
        self.assertEqual(campagne.client, agency)
        self.assertEqual(campagne.lieu, 'Bobo-Dioulasso')
        self.assertEqual(campagne.date_debut, self.start)
        self.assertEqual(campagne.date_fin, self.end)
        self.assertEqual(campagne.type_support, format_support.code)
        self.assertEqual(demande.campagne, campagne)
        self.assertEqual(demande.statut, DemandeReservation.STATUT_VALIDEE)
        self.assertTrue(
            LigneCampagne.objects.filter(campagne=campagne, face=face).exists()
        )
