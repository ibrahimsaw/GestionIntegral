import datetime
import json
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from campaigns.models import Client, DemandeReservation, Reservation
from inventory.models import FacePanneau, Support


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

    def complete_step_one(self):
        return self.client.post(
            reverse('demande_creation'),
            {
                'faces_selectionnees': json.dumps([self.face.pk]),
                'supports_selectionnees': '[]',
                'date_debut': self.start.isoformat(),
                'date_fin': self.end.isoformat(),
            },
        )

    def complete_step_two(self):
        return self.client.post(
            reverse('demande_creation_etape2'),
            {
                'nom_campagne': 'Campagne saisie par le staff',
                'message': 'Informations du projet',
            },
        )

    def test_client_can_update_and_confirm_shared_request_before_staff_approval(self):
        client_obj = Client.objects.create(
            nom='Annonceur existant',
            contact_nom='Awa Contact',
            telephone='70000000',
            email='awa@example.com',
        )

        self.assertRedirects(
            self.complete_step_one(),
            reverse('demande_creation_etape2'),
        )
        self.assertRedirects(
            self.complete_step_two(),
            reverse('demande_creation_etape3'),
        )
        response = self.client.post(
            reverse('demande_creation_etape3'),
            {
                'type_client': DemandeReservation.TYPE_CLIENT_EXISTANT,
                'client': client_obj.pk,
                'accepte_contact': 'on',
            },
        )

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
        self.assertEqual(demande.nom_campagne, 'Campagne saisie par le staff')
        self.assertEqual(demande.message, 'Informations du projet')
        self.assertTrue(demande.accepte_contact)
        self.assertEqual(list(demande.faces_souhaitees.all()), [self.face])

        detail_response = self.client.get(
            reverse('demande_detail', kwargs={'uuid': demande.uuid})
        )
        self.assertContains(detail_response, 'Autoriser la modification client')
        self.assertContains(detail_response, 'Autoriser la modification client')
        self.assertContains(detail_response, 'Valider la demande')
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
        self.assertNotContains(detail_response, 'Valider la demande')
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
            latitude=12.35,
            longitude=-1.53,
            ville='Ouagadougou',
        )
        other_face = FacePanneau.objects.create(
            support=other_panel,
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
        self.assertNotContains(shared_page, '<aside class="sidebar"')
        self.assertNotContains(shared_page, '<header class="topbar">')
        response = self.client.post(
            reverse('portail:demande_partage', kwargs={'token': token}),
            {
                'faces_selectionnees': json.dumps([other_face.pk]),
                'supports_selectionnees': '[]',
                'date_debut': updated_start.isoformat(),
                'date_fin': updated_end.isoformat(),
            },
        )
        self.assertRedirects(
            response,
            reverse('portail:demande_partage_etape2', kwargs={'token': token}),
        )
        response = self.client.post(
            reverse('portail:demande_partage_etape2', kwargs={'token': token}),
            {
                'nom_campagne': 'Projet modifié par le client',
                'message': 'Nouvelle précision du client',
            },
        )
        self.assertRedirects(
            response,
            reverse('portail:demande_partage_etape3', kwargs={'token': token}),
        )
        response = self.client.post(
            reverse('portail:demande_partage_etape3', kwargs={'token': token}),
            {
                'type_client': DemandeReservation.TYPE_CLIENT_NOUVEAU,
                'nom_contact': 'Awa mise à jour',
                'societe': 'Nouvelle société',
                'email': 'awa.updated@example.com',
                'telephone': '71111111',
                'accepte_contact': 'on',
            },
        )
        self.assertRedirects(
            response,
            reverse('portail:confirmation', kwargs={'uuid': demande.uuid}),
        )

        demande.refresh_from_db()
        self.assertEqual(demande.statut, DemandeReservation.STATUT_NOUVELLE)
        self.assertIsNone(demande.client)
        self.assertEqual(demande.date_debut_souhaitee, updated_start)
        self.assertEqual(demande.date_fin_souhaitee, updated_end)
        self.assertEqual(demande.nom_campagne, 'Projet modifié par le client')
        self.assertEqual(demande.nom_contact, 'Awa mise à jour')
        self.assertEqual(demande.email, 'awa.updated@example.com')
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
        self.assertContains(detail_response, 'Valider la demande')
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
            },
        )
        self.assertRedirects(
            response,
            reverse('portail:demande_partage_etape2', kwargs={'token': token}),
        )
        response = self.client.post(
            reverse('portail:demande_partage_etape2', kwargs={'token': token}),
            {
                'nom_campagne': 'Projet modifié une seconde fois',
                'message': 'Nouvelle demande après réactivation',
            },
        )
        self.assertRedirects(
            response,
            reverse('portail:demande_partage_etape3', kwargs={'token': token}),
        )
        response = self.client.post(
            reverse('portail:demande_partage_etape3', kwargs={'token': token}),
            {
                'type_client': DemandeReservation.TYPE_CLIENT_NOUVEAU,
                'nom_contact': 'Awa mise à jour',
                'societe': 'Nouvelle société',
                'email': 'awa.updated@example.com',
                'telephone': '71111111',
                'accepte_contact': 'on',
            },
        )
        self.assertRedirects(
            response,
            reverse('portail:confirmation', kwargs={'uuid': demande.uuid}),
        )
        demande.refresh_from_db()
        self.assertEqual(demande.statut, DemandeReservation.STATUT_NOUVELLE)
        self.assertEqual(demande.date_debut_souhaitee, second_start)

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
        self.complete_step_one()
        self.complete_step_two()
        response = self.client.post(
            reverse('demande_creation_etape3'),
            {
                'type_client': DemandeReservation.TYPE_CLIENT_NOUVEAU,
                'nom_contact': 'Moussa Prospect',
                'societe': 'Nouvelle société',
                'email': 'moussa@example.com',
                'telephone': '71000000',
            },
        )

        demande = DemandeReservation.objects.get()
        self.assertRedirects(
            response,
            reverse('demande_detail', kwargs={'uuid': demande.uuid}),
        )
        self.assertEqual(demande.statut, DemandeReservation.STATUT_EN_COURS)
        self.assertIsNone(demande.client)
        self.assertEqual(demande.type_client, DemandeReservation.TYPE_CLIENT_NOUVEAU)
        self.assertEqual(demande.nom_contact, 'Moussa Prospect')
        self.assertEqual(demande.nom_campagne, 'Campagne saisie par le staff')
        self.assertEqual(list(demande.faces_souhaitees.all()), [self.face])
        detail_response = self.client.get(
            reverse('demande_detail', kwargs={'uuid': demande.uuid})
        )
        self.assertContains(detail_response, 'Valider la demande')
        self.assertContains(detail_response, 'Refuser la demande')
        self.assertFalse(Reservation.objects.exists())

    def test_staff_can_edit_confirmed_request_without_reenabling_client_link(self):
        demande = DemandeReservation.objects.create(
            type_client=DemandeReservation.TYPE_CLIENT_NOUVEAU,
            nom_contact='Client prospect',
            societe='Société initiale',
            email='client@example.com',
            telephone='70000000',
            date_debut_souhaitee=self.start,
            date_fin_souhaitee=self.end,
            statut=DemandeReservation.STATUT_NOUVELLE,
            token_partage=uuid.uuid4(),
        )
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
            reverse('demande_creation'),
        )
        response = self.client.get(reverse('demande_creation'))
        self.assertContains(response, 'Modifier les emplacements de la demande')
        response = self.client.post(
            reverse('demande_creation'),
            {
                'faces_selectionnees': json.dumps([self.face.pk]),
                'supports_selectionnees': '[]',
                'date_debut': (self.start + datetime.timedelta(days=1)).isoformat(),
                'date_fin': (self.end + datetime.timedelta(days=1)).isoformat(),
            },
        )
        self.assertRedirects(response, reverse('demande_creation_etape2'))
        response = self.client.post(
            reverse('demande_creation_etape2'),
            {
                'nom_campagne': 'Projet corrigé',
                'message': 'Correction interne par le gestionnaire',
            },
        )
        self.assertRedirects(response, reverse('demande_creation_etape3'))
        self.assertContains(
            self.client.get(reverse('demande_creation_etape3')),
            'Modifier les informations du demandeur',
        )
        response = self.client.post(
            reverse('demande_creation_etape3'),
            {
                'type_client': DemandeReservation.TYPE_CLIENT_NOUVEAU,
                'nom_contact': 'Nom corrigé par le gestionnaire',
                'societe': 'Société corrigée',
                'email': 'corrige@example.com',
                'telephone': '71111111',
            },
        )

        self.assertRedirects(response, detail_url)
        demande.refresh_from_db()
        self.assertEqual(demande.nom_contact, 'Nom corrigé par le gestionnaire')
        self.assertEqual(demande.societe, 'Société corrigée')
        self.assertEqual(demande.email, 'corrige@example.com')
        self.assertEqual(demande.nom_campagne, 'Projet corrigé')
        self.assertEqual(
            demande.date_debut_souhaitee,
            self.start + datetime.timedelta(days=1),
        )
        self.assertEqual(demande.statut, DemandeReservation.STATUT_NOUVELLE)
        self.assertRedirects(
            self.client.get(reverse('portail:demande_partage', kwargs={'token': token})),
            reverse('portail:confirmation', kwargs={'uuid': demande.uuid}),
        )

    def test_steps_render_the_portal_wizard_for_staff(self):
        response = self.client.get(reverse('demande_creation'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sélectionnez les emplacements du client')
        self.assertContains(response, 'Carte Interactive')
        self.assertNotContains(response, 'Nos Supports')

        self.complete_step_one()
        response = self.client.get(reverse('demande_creation_etape2'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Demande de réservation client')
        self.assertContains(response, 'Carte Interactive')
        self.assertNotContains(response, 'Nos Supports')

        self.complete_step_two()
        response = self.client.get(reverse('demande_creation_etape3'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Client enregistré')
        self.assertContains(response, 'Créer la demande')
        self.assertContains(response, 'Carte Interactive')
        self.assertNotContains(response, 'Nos Supports')

        list_response = self.client.get(reverse('demandes_liste'))
        self.assertContains(list_response, 'Créer une demande')

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
        self.assertNotContains(detail_response, 'Valider la demande')

        response = self.client.post(
            reverse('demande_valider', kwargs={'uuid': demande.uuid}),
        )

        self.assertRedirects(response, detail_url)
        demande.refresh_from_db()
        self.assertEqual(demande.statut, DemandeReservation.STATUT_EN_COURS)
        self.assertFalse(Reservation.objects.exists())
