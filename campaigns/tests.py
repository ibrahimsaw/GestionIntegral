import datetime
from types import MethodType

from django.test import SimpleTestCase

from campaigns.models import Campagne, LigneCampagne
from campaigns.simulation_view import (
    _enrich_resultat_metriques,
    _spots,
    calculer_objectif_total,
    calculer_repartition_blocs,
    calculer_suggestions_objectif,
    calculer_total_spots_avec_jours_ajoutes,
    generer_ajustements_meme_periode,
    generer_combinaisons_complement,
    generer_scenarios_simulation,
    normaliser_objectif_spots,
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

    def test_objectif_par_ecran_devient_un_objectif_global(self):
        self.assertEqual(calculer_objectif_total(500, 3), 1500)

    def test_objectif_total_n_est_pas_multiplie_par_le_nombre_ecrans(self):
        self.assertEqual(normaliser_objectif_spots(8, objectif_total=4500), 4500)

    def test_un_seul_type_objectif_peut_etre_renseigne(self):
        with self.assertRaises(ValueError):
            normaliser_objectif_spots(8, objectif_par_ecran=500, objectif_total=4500)

    def test_ajustements_meme_periode_atteignent_la_cible_sans_changer_le_parc(self):
        ajustements = generer_ajustements_meme_periode(
            objectif=1500,
            nb_jours=9,
            nb_ecrans=3,
            nombre_visuels=1,
            frequence_actuelle=240,
            heures_actuelles=2,
        )

        self.assertTrue(ajustements)
        self.assertTrue(all(abs(item["ecart_pct"]) <= 5 for item in ajustements))
        self.assertTrue(all(item["tranche_val"].startswith("06:00-") for item in ajustements))

    def test_ajustements_meme_periode_corrigent_aussi_un_surplus(self):
        ajustements = generer_ajustements_meme_periode(
            objectif=4000,
            nb_jours=9,
            nb_ecrans=8,
            nombre_visuels=1,
            frequence_actuelle=300,
            heures_actuelles=7,
        )

        self.assertTrue(ajustements)
        self.assertTrue(all(abs(item["ecart_pct"]) <= 5 for item in ajustements))
        self.assertTrue(all(item["frequence"] != 300 or item["heures"] != 7 for item in ajustements))

    def test_suggestions_extension_ne_depassent_pas_le_parc_selectionne(self):
        combinaisons = generer_combinaisons_complement(
            manque=600,
            nombre_visuels=1,
            ecrans_disponibles=3,
            max_ecrans_ajout=2,
        )

        self.assertTrue(combinaisons)
        self.assertLessEqual(max(item["ecrans_ajout"] for item in combinaisons), 2)

    def test_periode_seule_genere_des_scenarios_pour_plusieurs_nombres_ecrans(self):
        scenarios = generer_scenarios_simulation(
            duree_passage=10,
            nombre_visuels=1,
            nb_jours=9,
            nb_ecrans_candidates=[1, 2],
            frequence_fixe=300,
            tranches_fixes="08:00-10:00",
        )

        self.assertEqual({scenario["nb_ecrans"] for scenario in scenarios}, {1, 2})
        self.assertEqual({scenario["nb_jours"] for scenario in scenarios}, {9})

    def test_objectif_seul_calcule_la_duree_sans_modifier_frequence_ni_tranche(self):
        scenarios = generer_scenarios_simulation(
            duree_passage=10,
            nombre_visuels=1,
            nb_jours=None,
            nb_ecrans_candidates=[1],
            objectif_total=4500,
            frequence_fixe=120,
            tranches_fixes="08:00-18:00",
        )

        self.assertEqual(len(scenarios), 1)
        self.assertEqual(scenarios[0]["nb_jours"], 15)
        self.assertEqual(scenarios[0]["spots_total"], 4500)

    def test_cible_non_atteignable_en_jours_entiers_retourne_les_plus_proches(self):
        scenarios = generer_scenarios_simulation(
            duree_passage=10,
            nombre_visuels=1,
            nb_jours=None,
            nb_ecrans_candidates=[1],
            objectif_total=4500,
            frequence_fixe=60,
            tranches_fixes="08:00-18:00",
        )

        self.assertEqual([scenario["nb_jours"] for scenario in scenarios], [7, 8])
        self.assertTrue(all(not scenario["dans_tolerance"] for scenario in scenarios))

    def test_cible_et_periode_fixes_ajustent_frequence_et_tranche(self):
        scenarios = generer_scenarios_simulation(
            duree_passage=20,
            nombre_visuels=1,
            nb_jours=9,
            nb_ecrans_candidates=[8],
            objectif_total=4000,
            frequence_preferee=300,
            tranche_reference="06:00-09:00,17:00-21:00",
        )

        self.assertTrue(scenarios)
        meilleur = scenarios[0]
        self.assertEqual(meilleur["nb_jours"], 9)
        self.assertEqual(meilleur["nb_ecrans"], 8)
        self.assertLessEqual(abs(meilleur["ecart_pct"]), 5)
        self.assertEqual(meilleur["spots_total"], 4032)
        self.assertEqual(meilleur["frequence"], 900)

    def test_tranche_actuelle_et_frequence_actuelle_peuvent_etre_la_meilleure_proposition(self):
        tranche = "06:00-10:16,16:00-21:00"
        scenarios = generer_scenarios_simulation(
            duree_passage=20,
            nombre_visuels=1,
            nb_jours=9,
            nb_ecrans_candidates=[8],
            objectif_total=4000,
            frequence_preferee=600,
            tranche_reference=tranche,
        )

        meilleur = scenarios[0]
        self.assertEqual(meilleur["frequence"], 600)
        self.assertEqual(meilleur["tranches_val"], tranche)
        self.assertEqual(meilleur["spots_total"], 4003)
        self.assertLess(abs(meilleur["ecart_pct"]), 0.1)

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
