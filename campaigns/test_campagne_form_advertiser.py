from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from campaigns.forms import CampagneForm
from campaigns.models import (
    Campagne,
    Client,
    LigneCampagne,
    Reservation,
    ReservationLigne,
    STATUT_CONFIRMEE,
)
from campaigns.views import _importer_faces_reservation
from inventory.models import FacePanneau, FormatSupport, Support


class CampagneFormAdvertiserTests(TestCase):
    def test_prices_accept_grouped_large_amounts_and_decimal_commas(self):
        client = Client.objects.create(
            nom="Client pour test de prix",
            telephone="00000000",
            email="client-prix@example.com",
        )
        form = CampagneForm(data={
            "client": str(client.pk),
            "nom": "Campagne avec grands tarifs",
            "date_debut": "2026-01-01",
            "date_fin": "2026-01-02",
            "type_support": "ecran",
            "duree_passage": "5",
            "frequence": "120",
            "tranches_horaires": "08:00-12:00",
            "nombre_visuels": "1",
            "prix": "154 824 205 451",
            "prix_affichage": "154824205451",
            "prix_impression": "1,25",
        })

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["prix"], Decimal("154824205451"))
        self.assertEqual(form.cleaned_data["prix_affichage"], Decimal("154824205451"))
        self.assertEqual(form.cleaned_data["prix_impression"], Decimal("1.25"))

    def test_default_zero_prices_are_blank(self):
        form = CampagneForm()

        for field_name in ("prix", "prix_affichage", "prix_impression"):
            self.assertIsNone(form[field_name].value())
            self.assertEqual(form.fields[field_name].widget.input_type, "text")

    def test_campaign_form_uses_selected_client_instead_of_separate_advertiser(self):
        client = Client.objects.create(
            nom="Client de test annonceur",
            telephone="00000001",
            email="client-annonceur-test@example.com",
        )
        form = CampagneForm(data={
            "client": str(client.pk),
            "nom": "Campagne de test",
            "date_debut": "2026-01-01",
            "date_fin": "2026-01-02",
            "type_support": "ecran",
            "duree_passage": "5",
            "frequence": "120",
            "tranches_horaires": "08:00-12:00",
            "nombre_visuels": "1",
            "prix": "",
            "prix_affichage": "",
            "prix_impression": "",
        })

        self.assertNotIn("annonceur", form.fields)
        self.assertTrue(form.is_valid(), form.errors)
        campagne = form.save()
        self.assertEqual(campagne.client, client)
        self.assertEqual(campagne.prix, Decimal("0"))
        self.assertEqual(campagne.prix_affichage, Decimal("0"))
        self.assertEqual(campagne.prix_impression, Decimal("0"))


class CampagneReservationImportTests(TestCase):
    def test_reference_imports_faces_only_for_the_selected_agency(self):
        agency = Client.objects.create(
            nom="Agence import",
            telephone="00000000",
            email="agence-import@example.com",
        )
        other_agency = Client.objects.create(
            nom="Autre agence",
            telephone="00000001",
            email="autre-agence@example.com",
        )
        format_support = FormatSupport.objects.create(
            code="IMPORT-4X3",
            categorie="Standard",
            dimensions="4m × 3m",
        )
        other_format = FormatSupport.objects.create(
            code="IMPORT-GEANT",
            categorie="Géant",
            dimensions="8m × 6m",
        )
        second_standard_format = FormatSupport.objects.create(
            code="IMPORT-5X4",
            categorie="Standard",
            dimensions="5m × 4m",
        )
        support = Support.objects.create(
            code="IMPORT-PANNEAU",
            nom="Panneau de réservation import",
            type_support=Support.TYPE_PANNEAU,
            format=format_support.code,
            latitude=12.34,
            longitude=-1.52,
            ville="Ouagadougou",
        )
        face = FacePanneau.objects.create(support=support, label="A")
        other_support = Support.objects.create(
            code="IMPORT-PANNEAU-GEANT",
            nom="Panneau géant de réservation import",
            type_support=Support.TYPE_PANNEAU,
            format=other_format.code,
            latitude=12.35,
            longitude=-1.53,
            ville="Ouagadougou",
        )
        other_face = FacePanneau.objects.create(support=other_support, label="A")
        second_standard_support = Support.objects.create(
            code="IMPORT-PANNEAU-5X4",
            nom="Panneau standard 5x4 de réservation import",
            type_support=Support.TYPE_PANNEAU,
            format=second_standard_format.code,
            latitude=12.36,
            longitude=-1.54,
            ville="Ouagadougou",
        )
        second_standard_face = FacePanneau.objects.create(
            support=second_standard_support,
            label="A",
        )
        start = timezone.now() + timedelta(days=5)
        end = start + timedelta(days=2)
        reservation = Reservation.objects.create(
            nom="Réservation à importer",
            client=agency,
            date_debut=start,
            date_fin=end,
            statut=STATUT_CONFIRMEE,
        )
        ReservationLigne.objects.create(
            reservation=reservation,
            support=support,
            face=face,
        )
        ReservationLigne.objects.create(
            reservation=reservation,
            support=other_support,
            face=other_face,
        )
        ReservationLigne.objects.create(
            reservation=reservation,
            support=second_standard_support,
            face=second_standard_face,
        )

        data = {
            "client": str(agency.pk),
            "nom": "Campagne liée à réservation",
            "date_debut": timezone.localdate(start).isoformat(),
            "date_fin": timezone.localdate(end).isoformat(),
            "type_support": format_support.categorie,
            "nombre_visuels": "1",
            "reservation_reference": reservation.reference,
        }
        form = CampagneForm(data=data)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            form.fields["reservation_reference"].widget.attrs["list"],
            "reservations-agence",
        )
        self.assertIn(reservation, form.reservations_existantes)
        self.assertEqual(form.reservation_categorie, "Standard")
        self.assertEqual(
            form.reservation_formats,
            {format_support.code, second_standard_format.code},
        )
        self.assertEqual(
            {
                category["nom"]
                for category in next(
                    item for item in form.reservations_existantes
                    if item.pk == reservation.pk
                ).categories_pour_formulaire
            },
            {"Standard", "Géant"},
        )
        campagne = form.save()
        self.assertEqual(
            _importer_faces_reservation(
                campagne,
                form.reservation_source,
                form.reservation_formats,
            ),
            2,
        )
        ligne = LigneCampagne.objects.get(campagne=campagne, face=face)
        self.assertEqual(ligne.face, face)
        self.assertEqual(ligne.support, support)
        self.assertFalse(LigneCampagne.objects.filter(campagne=campagne, face=other_face).exists())
        self.assertTrue(
            LigneCampagne.objects.filter(
                campagne=campagne,
                face=second_standard_face,
            ).exists()
        )
        self.assertEqual(
            _importer_faces_reservation(campagne, reservation, form.reservation_formats),
            0,
        )

        data["type_support"] = other_format.code
        other_category_form = CampagneForm(data=data)
        self.assertTrue(other_category_form.is_valid(), other_category_form.errors)
        campagne_geant = other_category_form.save()
        self.assertEqual(other_category_form.reservation_categorie, "Géant")
        self.assertEqual(
            _importer_faces_reservation(
                campagne_geant,
                other_category_form.reservation_source,
                other_category_form.reservation_formats,
            ),
            1,
        )
        self.assertTrue(
            LigneCampagne.objects.filter(campagne=campagne_geant, face=other_face).exists()
        )
        self.assertFalse(
            LigneCampagne.objects.filter(campagne=campagne_geant, face=face).exists()
        )

        data["type_support"] = format_support.code
        data["client"] = str(other_agency.pk)
        wrong_agency_form = CampagneForm(data=data)
        self.assertFalse(wrong_agency_form.is_valid())
        self.assertIn("reservation_reference", wrong_agency_form.errors)

        before_reservation = data.copy()
        before_reservation["client"] = str(agency.pk)
        before_reservation["date_debut"] = (
            timezone.localdate(start) - timedelta(days=1)
        ).isoformat()
        before_reservation_form = CampagneForm(data=before_reservation)
        self.assertFalse(before_reservation_form.is_valid())
        self.assertIn("date_debut", before_reservation_form.errors)

        after_reservation = data.copy()
        after_reservation["client"] = str(agency.pk)
        after_reservation["date_fin"] = (
            timezone.localdate(end) + timedelta(days=1)
        ).isoformat()
        after_reservation_form = CampagneForm(data=after_reservation)
        self.assertFalse(after_reservation_form.is_valid())
        self.assertIn("date_fin", after_reservation_form.errors)
