from django.contrib.auth.models import AnonymousUser
from django.forms import RadioSelect
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase

from campaigns.forms import CampagneForm, ClientForm
from campaigns.models import Client


class ClientAgencyFormTests(TestCase):
    def create_account(self, account_type, name, email):
        return Client.objects.create(
            type_entite=account_type,
            nom=name,
            telephone='70000000',
            email=email,
        )

    def campaign_form(self, client, agency=None, name='Campagne'):
        data = {
            'client': str(client.pk),
            'nom': name,
            'date_debut': '2026-12-01',
            'date_fin': '2026-12-31',
            'type_support': 'ecran',
            'duree_passage': '5',
            'frequence': '120',
            'tranches_horaires': '08:00-12:00',
            'nombre_visuels': '1',
            'agence': str(agency.pk) if agency else '',
        }
        return CampagneForm(data=data)

    def test_account_creation_does_not_assign_clients_to_agencies(self):
        client = self.create_account(
            Client.TYPE_CLIENT,
            'Client indépendant',
            'client-independant@example.com',
        )
        agency = self.create_account(
            Client.TYPE_AGENCE,
            'Agence indépendante',
            'agence-independante@example.com',
        )

        self.assertFalse(hasattr(client, 'agences'))
        self.assertFalse(hasattr(agency, 'clients'))
        self.assertTrue(agency.reference.startswith('AGE-'))

    def test_campaign_client_is_required_and_agency_is_optional(self):
        client = self.create_account(
            Client.TYPE_CLIENT,
            'Client campagne',
            'client-campagne@example.com',
        )
        agency_a = self.create_account(
            Client.TYPE_AGENCE,
            'Agence A',
            'agence-a@example.com',
        )
        agency_b = self.create_account(
            Client.TYPE_AGENCE,
            'Agence B',
            'agence-b@example.com',
        )

        first_form = self.campaign_form(client, agency_a, 'Campagne avec agence A')
        self.assertTrue(first_form.is_valid(), first_form.errors)
        first_campaign = first_form.save()

        second_form = self.campaign_form(client, agency_b, 'Campagne avec agence B')
        self.assertTrue(second_form.is_valid(), second_form.errors)
        second_campaign = second_form.save()

        no_agency_form = self.campaign_form(client, name='Campagne sans agence')
        self.assertTrue(no_agency_form.is_valid(), no_agency_form.errors)
        no_agency_campaign = no_agency_form.save()

        self.assertEqual(first_campaign.agence, agency_a)
        self.assertEqual(second_campaign.agence, agency_b)
        self.assertIsNone(no_agency_campaign.agence)
        self.assertEqual(first_campaign.client, client)
        self.assertEqual(second_campaign.client, client)

        missing_client_data = self.campaign_form(client).data.copy()
        missing_client_data.pop('client')
        form_without_client = CampagneForm(data=missing_client_data)
        self.assertFalse(form_without_client.is_valid())
        self.assertIn('client', form_without_client.errors)

    def test_campaign_form_only_offers_client_and_agency_accounts_in_their_fields(self):
        client = self.create_account(
            Client.TYPE_CLIENT,
            'Client de campagne',
            'client-choix@example.com',
        )
        agency = self.create_account(
            Client.TYPE_AGENCE,
            'Agence de campagne',
            'agence-choix@example.com',
        )

        form = CampagneForm()
        client_choices = set(form.fields['client'].queryset.values_list('pk', flat=True))
        agency_choices = set(form.fields['agence'].queryset.values_list('pk', flat=True))
        self.assertIn(client.pk, client_choices)
        self.assertNotIn(agency.pk, client_choices)
        self.assertIn(agency.pk, agency_choices)
        self.assertNotIn(client.pk, agency_choices)

    def test_account_form_only_renders_the_client_or_agency_type_choice(self):
        request = RequestFactory().get('/clients/creer/')
        request.user = AnonymousUser()
        form = ClientForm()
        html = render_to_string(
            'campaigns/client_form.html',
            {'form': form, 'title': 'Nouveau client ou agence', 'obj': None},
            request=request,
        )

        self.assertIsInstance(form.fields['type_entite'].widget, RadioSelect)
        self.assertNotIn('agences', form.fields)
        self.assertNotIn('clients', form.fields)
        self.assertIn('type="radio"', html)
        self.assertNotIn('id="agences-associees"', html)
        self.assertNotIn('id="clients-rattaches"', html)
