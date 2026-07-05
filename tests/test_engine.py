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


if __name__ == "__main__":
    unittest.main(verbosity=2)
