import datetime

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase
from django.utils import timezone

from campaigns.models import Campagne, Client, LigneCampagne
from inventory.models import EcranNumerique, EcranPlageHoraire, EcranProgramme, Support
from portail.views import SupportDetailView


class PortalScreenTelemetryTests(TestCase):
    def test_telemetry_uses_fixed_spot_duration_and_effective_screen_program(self):
        today = timezone.localdate()
        support = Support.objects.create(
            code='PORTAL-SCREEN-TELEMETRY',
            nom='Écran télémétrie portail',
            type_support=Support.TYPE_ECRAN,
            latitude=12.34,
            longitude=-1.52,
        )
        screen = EcranNumerique.objects.create(support=support)
        screen.refresh_from_db()
        support = Support.objects.get(pk=support.pk)
        programme = EcranProgramme.objects.create(
            ecran=screen,
            date_debut=today,
        )
        EcranPlageHoraire.objects.create(
            programme=programme,
            jour_semaine=today.weekday(),
            heure_debut=datetime.time(8, 0),
            heure_fin=datetime.time(9, 0),
        )
        client = Client.objects.create(
            nom='Client télémétrie portail',
            telephone='00000001',
            email='portal-screen-telemetry@example.com',
        )
        campagne = Campagne.objects.create(
            client=client,
            nom='Campagne télémétrie',
            type_support='ecran',
            date_debut=today,
            date_fin=today,
            statut=Campagne.STATUT_EN_COURS,
            frequence=900,
            duree_passage=10,
            tranches_horaires='08:00-09:00',
        )
        LigneCampagne.objects.create(campagne=campagne, support=support)

        request = RequestFactory().get('/')
        request.user = AnonymousUser()
        view = SupportDetailView()
        view.setup(request, uuid=support.uuid)
        view.object = support

        context = view.get_context_data()

        self.assertEqual(context['spots_data'][0]['dur'], 20)
        self.assertEqual(context['spots_data'][0]['interval'], 900)
        self.assertEqual(
            context['ecran_programme_data'][0]['windows'],
            [['08:00', '09:00']],
        )
