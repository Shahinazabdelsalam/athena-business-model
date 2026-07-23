"""
Tests du moteur de calcul de viabilité.

On vérifie chaque brique (charges annualisées, coût employeur, amortissement,
marge, seuil de rentabilité) avec des valeurs calculées à la main.
"""
import os
import sys
import math
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.engine import calculer, annualiser, cout_employeur, Parametres  # noqa: E402


class TestBriques(unittest.TestCase):
    def test_annualiser(self):
        self.assertEqual(annualiser(100, "mensuel"), 1200)
        self.assertEqual(annualiser(100, "annuel"), 100)
        self.assertEqual(annualiser(100, "trimestriel"), 400)

    def test_cout_employeur_sans_patronales(self):
        # Pas de cotisations patronales, taxe salaires 0 -> coût = net annuel.
        p = Parametres(cotisations_patronales=False, taux_taxe_salaires=0.0)
        self.assertEqual(cout_employeur(2000, 1, p), 24000)

    def test_cout_employeur_avec_patronales(self):
        p = Parametres(cotisations_patronales=True,
                       taux_cotisations_patronales=0.42, taux_taxe_salaires=0.0)
        # 2000*12 = 24000 ; +42% = 34080
        self.assertAlmostEqual(cout_employeur(2000, 1, p), 34080)

    def test_etp_partiel(self):
        p = Parametres(cotisations_patronales=False, taux_taxe_salaires=0.0)
        self.assertEqual(cout_employeur(2000, 0.5, p), 12000)


class TestScenarioComplet(unittest.TestCase):
    def setUp(self):
        self.data = {
            "parametres": {
                "cotisations_patronales": False,
                "taux_taxe_salaires": 0.0,
                "cotisation_ca_pct": 0.0,
            },
            "charges_externes": [
                {"description": "Loyer", "frequence": "mensuel", "montant_unitaire": 500},  # 6000/an
            ],
            "equipe": [
                {"description": "Fondatrice", "etp": 1, "remuneration_nette_mensuelle": 2000},  # 24000/an
            ],
            "investissements": [
                {"description": "Matériel", "duree_amortissement": 5, "montant_investi": 5000},  # 1000/an
            ],
            "offres": [
                {"description": "Atelier", "prix": 100, "cout_variable": 20, "quantite": 500},
            ],
        }

    def test_totaux(self):
        r = calculer(self.data)
        t = r["totaux"]
        # Coûts fixes = 6000 (charges) + 24000 (rému) + 1000 (amort) = 31000
        self.assertEqual(t["couts_fixes"], 31000)
        # CA = 100*500 = 50000
        self.assertEqual(t["ca_total"], 50000)
        # Coûts variables = 20*500 = 10000
        self.assertEqual(t["couts_variables_total"], 10000)
        # Marge brute = 50000 - 10000 = 40000
        self.assertEqual(t["marge_brute_total"], 40000)
        # Marge nette = 40000 - 31000 = 9000
        self.assertEqual(t["marge_nette"], 9000)
        self.assertTrue(t["viable"])

    def test_seuil_rentabilite(self):
        r = calculer(self.data)
        t = r["totaux"]
        # Taux de marge brute = 40000/50000 = 0.8
        self.assertAlmostEqual(t["taux_marge_brute"], 0.8)
        # Seuil CA = 31000 / 0.8 = 38750
        self.assertAlmostEqual(t["seuil_ca"], 38750)

    def test_offre_seuil_quantite(self):
        r = calculer(self.data)
        o = r["offres"][0]
        # Marge unitaire = 80 ; seuil quantité = 31000/80 = 387.5
        self.assertAlmostEqual(o["seuil_quantite"], 387.5)
        self.assertTrue(o["rentable"])

    def test_non_viable(self):
        data = dict(self.data)
        data["offres"] = [{"description": "Atelier", "prix": 30, "cout_variable": 20, "quantite": 100}]
        r = calculer(data)
        self.assertFalse(r["totaux"]["viable"])
        self.assertLess(r["totaux"]["marge_nette"], 0)

    def test_cotisation_ca(self):
        data = dict(self.data)
        data["parametres"] = dict(self.data["parametres"], cotisation_ca_pct=0.10)
        r = calculer(data)
        # Cotisation = 50000*0.10 = 5000
        self.assertAlmostEqual(r["totaux"]["cotisation_ca"], 5000)
        # Marge brute = (50000-10000) - 5000 = 35000
        self.assertAlmostEqual(r["totaux"]["marge_brute_total"], 35000)

    def test_compte_resultat_coherent(self):
        r = calculer(self.data)
        cr = r["compte_resultat"]
        recompute = (cr["marge_brute"] - cr["charges_externes"]
                     - cr["remunerations"] - cr["amortissements"])
        self.assertAlmostEqual(recompute, cr["marge_nette"])

    def test_vide_ne_plante_pas(self):
        r = calculer({})
        self.assertEqual(r["totaux"]["ca_total"], 0)
        self.assertIsNone(r["totaux"]["seuil_ca"])


class TestProjectionMonteeEnCharge(unittest.TestCase):
    """
    Projection pluriannuelle avec volumes par année (montée en charge :
    An 1 = pilotes d'amorçage, An 2+ = offres de croisière). Les chiffres du
    cas réel viennent du brief « Montée en charge » (modèle EntangleEQ).
    """

    def _cas_reel(self):
        return {
            "parametres": {
                "cotisations_patronales": False,
                "taux_taxe_salaires": 0.0,
                "cotisation_ca_pct": 0.0,
            },
            "charges_externes": [
                {"description": "Récurrent", "frequence": "annuel", "montant_unitaire": 5795},
                {"description": "Frais de création", "frequence": "unique", "montant_unitaire": 450},
                {"description": "Adhésion Willa", "frequence": "unique", "montant_unitaire": 1450},
            ],
            "offres": [
                {"description": "Pilote payant", "prix": 1000, "cout_variable": 0, "volumes": [3, 0, 0]},
                {"description": "Licence Essentiel", "prix": 2499, "cout_variable": 0, "volumes": [0, 10, 11]},
                {"description": "Licence Performance", "prix": 6000, "cout_variable": 0, "volumes": [0, 5, 6]},
            ],
            "pro": {"croissance_ca": 10, "croissance_charges": 3,
                    "sc_pessimiste": 0.6, "sc_optimiste": 1.5},
        }

    def test_cas_reel_ca_par_annee(self):
        annees = calculer(self._cas_reel())["projection"]["annees"]
        self.assertAlmostEqual(annees[0]["ca"], 3000)
        self.assertAlmostEqual(annees[1]["ca"], 54990)   # 10×2499 + 5×6000
        self.assertAlmostEqual(annees[2]["ca"], 63489)   # 11×2499 + 6×6000

    def test_cas_reel_charges_par_annee(self):
        annees = calculer(self._cas_reel())["projection"]["annees"]
        # An 1 = récurrent 5795 + uniques (450 + 1450) = 7695
        self.assertAlmostEqual(annees[0]["charges"], 7695)
        # An 2/3 : seul le récurrent croît de 3 %/an ; les uniques ont disparu
        self.assertAlmostEqual(annees[1]["charges"], 5795 * 1.03)
        self.assertAlmostEqual(annees[2]["charges"], 5795 * 1.03 ** 2)

    def test_cas_reel_marge_nette_et_verdicts(self):
        annees = calculer(self._cas_reel())["projection"]["annees"]
        self.assertAlmostEqual(annees[0]["marge_nette"], -4695)
        self.assertAlmostEqual(annees[1]["marge_nette"], 49021.15, places=2)
        self.assertAlmostEqual(annees[2]["marge_nette"], 57341.08, places=2)
        self.assertEqual([a["viable"] for a in annees], [False, True, True])

    def test_cas_reel_trajectoire_amorcage(self):
        traj = calculer(self._cas_reel())["projection"]["trajectoire"]
        self.assertEqual(traj["etat"], "amorcage")
        self.assertEqual(traj["premiere_annee_viable"], 2)
        # Besoin de trésorerie An 1 = |marge nette An 1|
        self.assertAlmostEqual(traj["besoin_tresorerie"], 4695)

    def test_an1_amorcage_ne_gonfle_pas_le_ca(self):
        # L'analyse An 1 (KPIs, verdict gratuit) doit refléter l'amorçage,
        # pas les licences de croisière : CA An 1 = 3 pilotes × 1000.
        t = calculer(self._cas_reel())["totaux"]
        self.assertAlmostEqual(t["ca_total"], 3000)
        self.assertFalse(t["viable"])

    def test_charge_unique_non_reconduite(self):
        data = {
            "charges_externes": [
                {"description": "Frais de création", "frequence": "unique", "montant_unitaire": 2000},
            ],
            "offres": [{"description": "Presta", "prix": 500, "cout_variable": 0, "volumes": [10, 10, 10]}],
            "pro": {"croissance_charges": 3},
        }
        annees = calculer(data)["projection"]["annees"]
        self.assertAlmostEqual(annees[0]["charges"], 2000)   # payée une fois
        self.assertAlmostEqual(annees[1]["charges"], 0)      # jamais reconduite
        self.assertAlmostEqual(annees[2]["charges"], 0)

    def test_scenario_pessimiste_par_volumes(self):
        # ×0,6 s'applique aux volumes de chaque année : An 2 = 54990 × 0,6.
        scen = calculer(self._cas_reel())["projection"]["scenarios"]
        self.assertAlmostEqual(scen["pessimiste"]["annees"][1]["ca"], 32994)
        self.assertAlmostEqual(scen["optimiste"]["annees"][1]["ca"], 54990 * 1.5)

    def test_offre_lancement_an3(self):
        # Une offre [0, 0, X] ne produit du CA qu'à partir de l'An 3.
        data = {
            "offres": [{"description": "Nouvelle offre", "prix": 800, "cout_variable": 0, "volumes": [0, 0, 25]}],
        }
        annees = calculer(data)["projection"]["annees"]
        self.assertAlmostEqual(annees[0]["ca"], 0)
        self.assertAlmostEqual(annees[1]["ca"], 0)
        self.assertAlmostEqual(annees[2]["ca"], 800 * 25)

    def test_non_regression_modele_legacy(self):
        # Ancien modèle : offre avec `quantite` unique, aucune charge unique.
        # La projection doit reproduire EXACTEMENT l'ancienne extrapolation
        # uniforme : CA[N] = CA0×(1+g)^N, charges[N] = CF0×(1+gCh)^N.
        data = {
            "parametres": {"cotisations_patronales": False, "taux_taxe_salaires": 0.0},
            "charges_externes": [{"description": "Loyer", "frequence": "mensuel", "montant_unitaire": 500}],
            "equipe": [{"description": "Fondatrice", "etp": 1, "remuneration_nette_mensuelle": 2000}],
            "investissements": [{"description": "Matériel", "duree_amortissement": 5, "montant_investi": 5000}],
            "offres": [{"description": "Atelier", "prix": 100, "cout_variable": 20, "quantite": 500}],
            "pro": {"croissance_ca": 10, "croissance_charges": 3},
        }
        r = calculer(data)
        t, annees = r["totaux"], r["projection"]["annees"]
        ca0, mb0, cf0 = t["ca_total"], t["marge_brute_total"], t["couts_fixes"]
        for n, a in enumerate(annees):
            self.assertAlmostEqual(a["ca"], ca0 * 1.10 ** n)
            self.assertAlmostEqual(a["marge_brute"], mb0 * 1.10 ** n)
            self.assertAlmostEqual(a["charges"], cf0 * 1.03 ** n)
            self.assertAlmostEqual(a["marge_nette"], mb0 * 1.10 ** n - cf0 * 1.03 ** n)
        # An 1 de la projection == analyse mono-année d'aujourd'hui.
        self.assertAlmostEqual(annees[0]["marge_nette"], t["marge_nette"])

    def test_verdict_trajectoire_viable_des_an1(self):
        data = {
            "offres": [{"description": "Presta", "prix": 1000, "cout_variable": 0, "volumes": [50, 55, 60]}],
            "charges_externes": [{"description": "Loyer", "frequence": "mensuel", "montant_unitaire": 500}],
        }
        traj = calculer(data)["projection"]["trajectoire"]
        self.assertEqual(traj["etat"], "viable")
        self.assertEqual(traj["premiere_annee_viable"], 1)
        self.assertAlmostEqual(traj["besoin_tresorerie"], 0)

    def test_verdict_trajectoire_non_viable(self):
        data = {
            "offres": [{"description": "Presta", "prix": 30, "cout_variable": 20, "volumes": [10, 11, 12]}],
            "charges_externes": [{"description": "Loyer", "frequence": "mensuel", "montant_unitaire": 500}],
            "pro": {"croissance_ca": 10, "croissance_charges": 3},
        }
        traj = calculer(data)["projection"]["trajectoire"]
        self.assertEqual(traj["etat"], "non_viable")
        self.assertIsNone(traj["premiere_annee_viable"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
