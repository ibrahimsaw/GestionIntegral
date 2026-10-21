import base64
import datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.template.loader import render_to_string
from django.urls import reverse
from PIL import Image

from accounts.models import User
from campaigns.models import Campagne, Client, LigneCampagne, PreuveDiffusion
from piges.drive_piges import DrivePigesError
from piges.models import PigeFichier
from reports.supports_report_view import _group_supports_by_quartier
from reports.views import (
    _build_context_campagne,
    _prepare_piges_drive_pdf,
    _prepare_visuels_pdf,
)


class PrepareVisuelsPdfTests(SimpleTestCase):
    def prepare_visuel(self, name, content):
        fichier = SimpleNamespace(
            name=name,
            url=f"/media/{name}",
            open=lambda mode: BytesIO(content),
        )
        campagne = SimpleNamespace(
            visuels=SimpleNamespace(
                all=lambda: [SimpleNamespace(fichier=fichier)],
            ),
        )
        return _prepare_visuels_pdf(campagne)[0]

    def test_large_jpeg_is_resized_and_compressed_for_pdf(self):
        original = BytesIO()
        Image.new("RGB", (3000, 2000), (120, 60, 80)).save(
            original,
            format="JPEG",
            quality=95,
        )

        result = self.prepare_visuel("photo.jpg", original.getvalue())
        optimized = base64.b64decode(result["src"].partition(",")[2])
        optimized_image = Image.open(BytesIO(optimized))

        self.assertEqual(optimized_image.size, (1200, 800))
        self.assertEqual(optimized_image.format, "JPEG")
        self.assertLess(len(optimized), len(original.getvalue()))

    def test_png_poster_keeps_lossless_format_and_is_resized(self):
        original = BytesIO()
        Image.new("RGB", (1800, 1200), (12, 90, 170)).save(original, format="PNG")

        result = self.prepare_visuel("affiche.png", original.getvalue())
        optimized = base64.b64decode(result["src"].partition(",")[2])
        optimized_image = Image.open(BytesIO(optimized))

        self.assertLessEqual(max(optimized_image.size), 1200)
        self.assertEqual(optimized_image.format, "PNG")


class CampagnePdfPreuvesTests(TestCase):
    def test_pdf_includes_download_link_to_campaign_drive_folder(self):
        client = Client.objects.create(
            nom="Client lien PDF",
            telephone="00000000",
            email="lien-pdf@example.com",
        )
        campagne = Campagne.objects.create(
            client=client,
            nom="Campagne lien PDF",
            date_debut=datetime.date(2026, 10, 1),
            date_fin=datetime.date(2026, 10, 31),
            lien_piges="https://drive.google.com/drive/folders/piges-pdf-test",
        )
        context = _build_context_campagne(campagne)
        context["preuves_diffusion_pdf"] = []
        context["piges_drive_pdf"] = []

        html = render_to_string("reports/campagne_pdf.html", context)

        self.assertIn(campagne.lien_piges, html)
        self.assertIn("Ouvrir le dossier Google Drive de la campagne", html)

    def test_contexte_pdf_inclut_les_preuves_de_la_mere_et_de_ses_enfants(self):
        client = Client.objects.create(
            nom="Client test PDF",
            telephone="00000000",
            email="pdf@example.com",
        )
        campagne = Campagne.objects.create(
            client=client,
            nom="Campagne mère",
            date_debut=datetime.date(2026, 10, 1),
            date_fin=datetime.date(2026, 10, 31),
            est_mere=True,
        )
        enfant = Campagne.objects.create(
            client=client,
            nom="Sous-campagne",
            date_debut=datetime.date(2026, 10, 1),
            date_fin=datetime.date(2026, 10, 31),
            campagne_parente=campagne,
        )
        for campagne_preuve in (campagne, enfant):
            ligne = LigneCampagne.objects.create(campagne=campagne_preuve)
            PreuveDiffusion.objects.create(
                campagne=campagne_preuve,
                ligne=ligne,
                photo="piges/preuve-test.jpg",
            )
            PigeFichier.objects.create(
                campagne=campagne_preuve,
                support_nom="Support photo",
                ville="Ouagadougou",
                date_prise=datetime.date(2026, 10, 17),
                nom_fichier="preuve.jpg",
                drive_file_id=f"drive-file-{campagne_preuve.pk}",
            )

        context = _build_context_campagne(campagne)

        self.assertEqual(
            set(context["preuves_diffusion"].values_list("campagne_id", flat=True)),
            {campagne.pk, enfant.pk},
        )
        self.assertEqual(
            set(context["piges_drive"].values_list("campagne_id", flat=True)),
            {campagne.pk, enfant.pk},
        )

    @patch("reports.views.download_photo")
    @patch("reports.views.get_drive_service")
    def test_drive_photos_are_embedded_in_pdf_as_optimized_data_urls(self, get_service, get_photo):
        client = Client.objects.create(
            nom="Client test PDF",
            telephone="00000000",
            email="photo-pdf@example.com",
        )
        campagne = Campagne.objects.create(
            client=client,
            nom="Campagne photo Drive",
            date_debut=datetime.date(2026, 10, 1),
            date_fin=datetime.date(2026, 10, 31),
        )
        pige = PigeFichier.objects.create(
            campagne=campagne,
            support_nom="Panneau Centre",
            ville="Ouagadougou",
            date_prise=datetime.date(2026, 10, 17),
            nom_fichier="preuve.jpg",
            drive_file_id="drive-file-pdf-embed-test",
        )
        original = BytesIO()
        Image.new("RGB", (2400, 1200), (100, 40, 80)).save(original, format="JPEG")
        get_photo.return_value = original.getvalue()

        prepared = _prepare_piges_drive_pdf([pige])

        self.assertEqual(get_service.call_count, 1)
        get_photo.assert_called_once_with(get_service.return_value, pige.drive_file_id)
        self.assertEqual(prepared[0]["pige"], pige)
        self.assertTrue(prepared[0]["src"].startswith("data:image/jpeg;base64,"))
        embedded = base64.b64decode(prepared[0]["src"].partition(",")[2])
        with Image.open(BytesIO(embedded)) as image:
            self.assertEqual(image.size, (1200, 600))

    @patch(
        "reports.views.get_drive_service",
        side_effect=DrivePigesError(
            "Le refresh token Google Drive a expiré ou a été révoqué."
        ),
    )
    def test_preview_shows_staff_error_when_drive_refresh_token_is_invalid(self, _get_service):
        client = Client.objects.create(
            nom="Client erreur OAuth",
            telephone="00000000",
            email="oauth-preview@example.com",
        )
        campagne = Campagne.objects.create(
            client=client,
            nom="Campagne erreur OAuth",
            date_debut=datetime.date(2026, 10, 1),
            date_fin=datetime.date(2026, 10, 31),
        )
        PigeFichier.objects.create(
            campagne=campagne,
            support_nom="Panneau Centre",
            ville="Ouagadougou",
            date_prise=datetime.date(2026, 10, 17),
            nom_fichier="preuve.jpg",
            drive_file_id="drive-file-invalid-refresh-test",
        )
        staff = User.objects.create_user(
            username="staff-oauth-preview",
            password="test-password",
            role=User.ROLE_STAFF,
        )
        self.client.force_login(staff)

        response = self.client.get(reverse("campagne_preview", args=[campagne.pk]))

        self.assertEqual(response.status_code, 503)
        self.assertContains(
            response,
            "Le refresh token Google Drive a expiré ou a été révoqué.",
            status_code=503,
        )
        self.assertContains(
            response,
            "Les preuves de diffusion enregistrées dans l'application",
            status_code=503,
        )


class GroupSupportsByQuartierTests(SimpleTestCase):
    def test_groups_supports_by_city_and_quartier_and_sorts_groups(self):
        supports = [
            {"ville": "Ouagadougou", "quartier": "Dapoya", "type": "Panneau", "nom": "Support B", "code": "B", "nb_faces": 2},
            {"ville": "Bobo-Dioulasso", "quartier": "Dapoya", "type": "Écran", "nom": "Écran A", "code": "A", "nb_faces": 1},
            {"ville": "Ouagadougou", "quartier": "Dapoya", "type": "Écran", "nom": "Écran A", "code": "A", "nb_faces": 1},
            {"ville": "", "quartier": "", "type": "Marché", "nom": "Marché sans zone", "code": "M", "nb_faces": 3},
        ]

        sections = _group_supports_by_quartier(supports)

        self.assertEqual(
            [section["nom"] for section in sections],
            [
                "Bobo-Dioulasso · Dapoya",
                "Ouagadougou · Dapoya",
                "Ville non renseignée · Quartier non renseigné",
            ],
        )
        self.assertEqual(sections[1]["total"], 2)
        self.assertEqual(sections[1]["unites"], 3)
        self.assertEqual(
            [item["code"] for item in sections[1]["items"]],
            ["B", "A"],
        )