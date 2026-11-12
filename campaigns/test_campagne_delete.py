import datetime

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TransactionTestCase
from django.urls import reverse

from accounts.models import AuditLog
from campaigns.models import Campagne, Client, LigneCampagne
from inventory.models import Support


class CampagneDeleteTests(TransactionTestCase):
    def setUp(self):
        staff = get_user_model().objects.create_user(
            username="campaign-delete-staff",
            password="test-password",
            role="staff",
        )
        self.client.force_login(staff)
        self.agency = Client.objects.create(
            nom="Agence suppression test",
            telephone="70000000",
            email="suppression-campagne@example.com",
        )

    def create_campaign(self):
        return Campagne.objects.create(
            client=self.agency,
            nom="Campagne à supprimer",
            date_debut=datetime.date(2026, 1, 1),
            date_fin=datetime.date(2026, 1, 31),
            type_support="Standard",
        )

    def test_post_for_missing_campaign_redirects_with_message(self):
        response = self.client.post(reverse("campagne_delete", args=[2692]))

        self.assertRedirects(response, reverse("campagne_list"))
        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(
            messages[0].message,
            "Cette campagne a déjà été supprimée ou n'existe plus.",
        )

    def test_post_deletes_existing_campaign(self):
        campagne = self.create_campaign()

        response = self.client.post(reverse("campagne_delete", args=[campagne.pk]))

        self.assertRedirects(response, reverse("campagne_list"))
        self.assertFalse(Campagne.objects.filter(pk=campagne.pk).exists())

    def test_post_deletes_campaign_with_lines_and_audits_without_error(self):
        campagne = self.create_campaign()
        support = Support.objects.create(
            code="DELETE-CAMPAIGN-SUPPORT",
            nom="Support lié à la campagne",
            type_support=Support.TYPE_PANNEAU,
            latitude=12.34,
            longitude=-1.52,
            ville="Ouagadougou",
        )
        LigneCampagne.objects.create(campagne=campagne, support=support)

        response = self.client.post(reverse("campagne_delete", args=[campagne.pk]))

        self.assertRedirects(response, reverse("campagne_list"))
        self.assertFalse(Campagne.objects.filter(pk=campagne.pk).exists())
        self.assertTrue(AuditLog.objects.filter(
            action=AuditLog.ACTION_DELETE,
            object_repr="Campagne à supprimer -> DELETE-CAMPAIGN-SUPPORT",
        ).exists())

    def test_client_detail_offers_bulk_actions_and_limits_them_to_its_campaigns(self):
        campagne = self.create_campaign()
        other_client = Client.objects.create(
            nom="Autre client",
            telephone="70000001",
            email="autre-client-campagne@example.com",
        )
        other_campaign = Campagne.objects.create(
            client=other_client,
            nom="Campagne autre client",
            date_debut=datetime.date(2026, 2, 1),
            date_fin=datetime.date(2026, 2, 28),
        )

        detail_url = reverse("client_detail", args=[self.agency.pk])
        detail_response = self.client.get(detail_url)
        self.assertContains(detail_response, 'id="client-campaign-select-all"')
        self.assertContains(detail_response, 'value="archiver"')

        response = self.client.post(
            detail_url,
            {
                "action": "archiver",
                "selected_ids": [str(campagne.pk), str(other_campaign.pk)],
            },
        )

        self.assertEqual(response.status_code, 302)
        campagne.refresh_from_db()
        other_campaign.refresh_from_db()
        self.assertFalse(campagne.actif)
        self.assertTrue(other_campaign.actif)

    def test_bulk_delete_from_client_detail_only_deletes_its_campaigns(self):
        campagne = self.create_campaign()
        other_client = Client.objects.create(
            nom="Client préservé",
            telephone="70000002",
            email="client-preserve-campagne@example.com",
        )
        other_campaign = Campagne.objects.create(
            client=other_client,
            nom="Campagne à préserver",
            date_debut=datetime.date(2026, 3, 1),
            date_fin=datetime.date(2026, 3, 31),
        )
        detail_url = reverse("client_detail", args=[self.agency.pk])

        response = self.client.post(
            detail_url,
            {
                "action": "supprimer",
                "selected_ids": [str(campagne.pk), str(other_campaign.pk)],
            },
        )
        self.assertRedirects(response, reverse("campagne_selected_delete"))

        confirmation = self.client.get(reverse("campagne_selected_delete"))
        self.assertEqual(confirmation.status_code, 200)
        self.assertEqual(
            list(confirmation.context["associated_campaigns"].values_list("pk", flat=True)),
            [campagne.pk],
        )

        delete_response = self.client.post(reverse("campagne_selected_delete"))

        self.assertEqual(
            delete_response.url,
            f"{detail_url}#section-campagnes",
        )
        self.assertFalse(Campagne.objects.filter(pk=campagne.pk).exists())
        self.assertTrue(Campagne.objects.filter(pk=other_campaign.pk).exists())
