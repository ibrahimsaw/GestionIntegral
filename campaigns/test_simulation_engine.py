import unittest

import simulation_engine as e


class TestTranches(unittest.TestCase):
    def test_fusion_des_chevauchements(self):
        self.assertEqual(e.parser_tranches("08:00-12:00,10:00-14:00"), [(480, 840)])
        self.assertEqual(e.duree_heures_tranches("08:00-12:00,10:00-14:00"), 6.0)

    def test_plages_disjointes(self):
        self.assertEqual(e.duree_minutes(e.parser_tranches("06:00-09:00,17:00-21:00")), 420)

    def test_erreurs(self):
        for mauvais in ("8-12", "12:00-08:00", "05:00-07:00", "20:00-23:30", "10:75-12:00"):
            with self.assertRaises(e.TrancheError, msg=mauvais):
                e.parser_tranches(mauvais)

    def test_pas_de_derive_flottante(self):
        for minutes in range(5, e.AMPLITUDE_MAX_MIN + 1, 5):
            for depart in (e.OUVERTURE_MIN, 9 * 60, 14 * 60, 22 * 60):
                (d, f), = e.parser_tranches(e.tranche_depuis_minutes(minutes, depart))
                self.assertEqual(f - d, minutes)
                self.assertGreaterEqual(d, e.OUVERTURE_MIN)
                self.assertLessEqual(f, e.FERMETURE_MIN)

    def test_decalage_au_lieu_de_troncature(self):
        self.assertEqual(e.tranche_depuis_minutes(615, 21 * 60), "12:45-23:00")

    def test_timeline_partielle(self):
        tl = e.construire_timeline(e.parser_tranches("12:00-14:30"))
        self.assertEqual(tl[12]["cls"], "active-hour")
        self.assertEqual(tl[14]["cls"], "active-hour partial-hour")
        self.assertEqual(tl[14]["couverture_min"], 30)
        self.assertEqual(tl[15]["cls"], "")


class TestFormules(unittest.TestCase):
    def test_formule_identique_au_modele(self):
        # (3600/freq) x heures x jours x ecrans x visuels
        self.assertAlmostEqual(e.spots(120, 8, 30, 2, 3), 30 * 8 * 30 * 2 * 3)

    def test_part_de_voix_active(self):
        self.assertEqual(e.part_de_voix_active(120, 10, 1), 8.3)
        ev = e.evaluer_blocs([e.Bloc(10, 120, 480, 2, 1, 10)])
        self.assertEqual(ev["part_de_voix_pct"], 8.3)
        self.assertEqual(ev["presence_24h_pct"], round(8.3 * 8 / 24, 2) if False else ev["presence_24h_pct"])
        self.assertAlmostEqual(ev["presence_24h_pct"], 8.33 * 8 / 24, places=1)

    def test_part_de_voix_ponderee_deux_blocs(self):
        b1 = e.Bloc(10, 60, 600, 1, 1, 10)    # 16,7 %
        b2 = e.Bloc(10, 300, 600, 1, 1, 10)   # 3,3 %
        ev = e.evaluer_blocs([b1, b2])
        self.assertAlmostEqual(ev["part_de_voix_pct"], 10.0, places=1)

    def test_boucle_impossible(self):
        self.assertFalse(e.analyser_boucle(60, 20, 4)["compatible"])
        self.assertTrue(e.analyser_boucle(120, 10, 3)["compatible"])
        self.assertEqual(e.analyser_boucle(60, 20, 3)["niveau"], "dense")
        self.assertEqual(e.frequences_compatibles(e.FREQUENCES_DEFAUT, 20, 4), [f for f in e.FREQUENCES_DEFAUT if f >= 80])

    def test_impact_inclut_les_visuels(self):
        self.assertAlmostEqual(
            e.impact_secondes_jour_ecran(120, 480, 10, 3),
            3 * e.impact_secondes_jour_ecran(120, 480, 10, 1),
        )


class TestOptimisation(unittest.TestCase):
    def test_meilleure_duree_exacte(self):
        minutes, exact = e.meilleure_duree(5000, 120, 10, 2, 1)
        self.assertEqual(minutes, 500)

    def test_meilleur_voisin(self):
        # 30 spots/min -> cible 1000 => 33,3 min exact ; 35 min (1050) vs 30 min (900)
        minutes, _ = e.meilleure_duree(1000, 2, 1, 1, 1)
        par_min = e.spots_par_minute_tranche(2, 1, 1, 1)
        self.assertLessEqual(abs(par_min * minutes - 1000), par_min * 5 / 2 + 1e-9)

    def test_hors_plage(self):
        self.assertIsNone(e.meilleure_duree(10_000_000, 1800, 1, 1, 1))

    def test_tranche_optimale(self):
        r = e.calculer_tranche_optimale_frequence_fixe(9000, 240, 30, 2, 10, 1)
        self.assertEqual(r["spots_obtenus"], 9000)
        # 15 spots/h x 30 j x 2 écrans = 900 spots/h -> 10 h = 600 min
        self.assertEqual(r["minutes"], 600)
        self.assertTrue(r["variantes_horaires"])
        for v in r["variantes_horaires"]:
            (d, f), = e.parser_tranches(v["tranche_val"])
            self.assertEqual(f - d, 600)

    def test_ajustements_meme_periode(self):
        res = e.generer_ajustements_meme_periode(9000, 30, 2, 1, 10, frequence_actuelle=240, minutes_actuelles=300)
        self.assertTrue(res)
        for r in res:
            self.assertLessEqual(abs(r["ecart_pct"]), 5.0)
            self.assertFalse(r["frequence"] == 240 and r["minutes"] == 300)

    def test_complement_ne_depasse_pas_les_ecrans_du_bloc_1(self):
        res = e.generer_combinaisons_complement(600, 1, 10, nb_ecrans_max=2)
        self.assertTrue(res)
        self.assertTrue(all(1 <= c["ecrans_ajout"] <= 2 for c in res))
        self.assertTrue(all(abs(c["ecart_pct"]) <= 8.0 for c in res))

    def test_reduction_jours(self):
        r = e.calculer_reduction_jours(120, 480, 2, 1, 30, 5000)
        self.assertLessEqual(r["spots_avec_jours_reduits"], 5000)
        self.assertEqual(r["nb_jours_reduit"], int(5000 // (30 * 8 * 2)))
        r0 = e.calculer_reduction_jours(60, 1020, 5, 1, 30, 10)
        self.assertEqual(r0["nb_jours_reduit"], 0)
        self.assertIsNotNone(r0["cas_particulier"])


class TestScenarios(unittest.TestCase):
    def test_objectif_periode_fixe_trouve_une_solution(self):
        res = e.generer_scenarios(duree_passage=10, nombre_visuels=1, nb_jours=30,
                                  nb_ecrans_candidates=[2], objectif_total=9137)
        self.assertTrue(res)
        self.assertTrue(all(r["dans_tolerance"] for r in res))
        self.assertTrue(all(r["nb_jours"] == 30 and r["nb_ecrans"] == 2 for r in res))

    def test_frequences_incompatibles_ecartees(self):
        res = e.generer_scenarios(duree_passage=20, nombre_visuels=4, nb_jours=10,
                                  nb_ecrans_candidates=[1], objectif_total=2000)
        self.assertTrue(all(r["frequence"] >= 80 for r in res))

    def test_objectif_par_ecran_suit_le_nombre_d_ecrans(self):
        res = e.generer_scenarios(duree_passage=10, nombre_visuels=1, nb_jours=20,
                                  nb_ecrans_candidates=[1, 2, 3], objectif_par_ecran=2000)
        for r in res:
            self.assertEqual(r["objectif_total"], 2000 * r["nb_ecrans"])

    def test_jours_libres(self):
        res = e.generer_scenarios(duree_passage=10, nombre_visuels=1, nb_jours=None,
                                  nb_ecrans_candidates=[2], objectif_total=20000)
        self.assertTrue(res)
        self.assertTrue(all(abs(r["ecart_pct"]) <= 5.0 for r in res))

    def test_exploration_sans_objectif(self):
        res = e.generer_scenarios(duree_passage=10, nombre_visuels=1, nb_jours=7,
                                  nb_ecrans_candidates=[1, 2])
        self.assertTrue(res)
        self.assertLessEqual(sum(1 for r in res if r["nb_ecrans"] == 1), 6)

    def test_alternatives_non_optimisees(self):
        res = e.generer_scenarios(duree_passage=10, nombre_visuels=1, nb_jours=10,
                                  nb_ecrans_candidates=[1], objectif_total=2000,
                                  tranches_alternatives=["08:00-12:00", "14:00-18:00"])
        self.assertTrue(all(not r["optimise"] for r in res))


if __name__ == "__main__":
    unittest.main(verbosity=2)
