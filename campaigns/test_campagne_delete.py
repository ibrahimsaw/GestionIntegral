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
