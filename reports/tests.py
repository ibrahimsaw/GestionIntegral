import base64
from io import BytesIO
from types import SimpleNamespace

from django.test import SimpleTestCase
from PIL import Image

from reports.supports_report_view import _group_supports_by_quartier
from reports.views import _prepare_visuels_pdf


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