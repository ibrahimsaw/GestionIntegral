import datetime
from types import MethodType

from django.test import SimpleTestCase

from campaigns.models import Campagne, LigneCampagne
from campaigns.simulation_view import (
    _enrich_resultat_metriques,
    _spots,
    calculer_repartition_blocs,
    calculer_suggestions_objectif,
    calculer_total_spots_avec_jours_ajoutes,
)
from inventory.models import Support


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
