"""
Athena Business Model — moteur de calcul de viabilité économique.

Ce module reproduit et améliore la logique de l'outil Google Sheets
(BIENVENUE / FIXE / VARIABLE / VIABILITÉ) pour analyser, sur une année-type,
si les revenus d'un projet couvrent les coûts nécessaires pour les générer.

Il est volontairement pur (aucune dépendance externe) pour être testable
et pour pouvoir être répliqué côté frontend en JavaScript.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Any

# Combien de fois par an revient une charge selon sa fréquence.
FREQUENCES: Dict[str, float] = {
    "annuel": 1,
    "semestriel": 2,
    "trimestriel": 4,
    "mensuel": 12,
    "hebdomadaire": 52,
    "quotidien": 365,
    "unique": 1,
}


def annualiser(montant: float, frequence: str) -> float:
    """Ramène un montant à son coût annuel selon sa fréquence."""
    return float(montant) * FREQUENCES.get(frequence, 1)


@dataclass
class Parametres:
    """Paramètres de base d'un scénario (feuille BIENVENUE)."""
    collecte_tva: bool = True
    cotisation_ca_pct: float = 0.0             # cotisation sur le CA (0.05 = 5%)
    cotisations_patronales: bool = False
    taux_cotisations_patronales: float = 0.42  # appliqué si cotisations_patronales
    taux_taxe_salaires: float = 0.03           # taxe sur les salaires
    heures_par_etp: float = 1582               # heures/an par équivalent temps plein
    type_montants: str = "HT"                  # "HT" ou "TTC"
    dividendes_cibles: float = 0.0             # prélèvements / dividendes annuels visés

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Parametres":
        d = d or {}

        def num(key, default):
            # N'utilise le défaut que si la clé est absente ou None ;
            # une valeur 0 explicite doit être respectée.
            v = d.get(key)
            return float(v) if v is not None else float(default)

        return cls(
            collecte_tva=bool(d.get("collecte_tva", True)),
            cotisation_ca_pct=num("cotisation_ca_pct", 0.0),
            cotisations_patronales=bool(d.get("cotisations_patronales", False)),
            taux_cotisations_patronales=num("taux_cotisations_patronales", 0.42),
            taux_taxe_salaires=num("taux_taxe_salaires", 0.03),
            heures_par_etp=num("heures_par_etp", 1582),
            type_montants=str(d.get("type_montants", "HT")),
            dividendes_cibles=num("dividendes_cibles", 0.0),
        )


def cout_employeur(net_mensuel: float, etp: float, p: Parametres) -> float:
    """
    Coût total employeur annuel pour un rôle.

    Part de la rémunération nette mensuelle par ETP. Ajoute, si applicable,
    les cotisations patronales puis la taxe sur les salaires. Si aucune
    cotisation patronale n'est due (ex. micro-entrepreneuse), le coût
    employeur est égal au net (hors taxe sur salaires).
    """
    net_annuel = float(net_mensuel) * 12 * float(etp)
    cout = net_annuel
    if p.cotisations_patronales:
        cout += net_annuel * p.taux_cotisations_patronales
    cout += net_annuel * p.taux_taxe_salaires
    return cout


def calculer(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calcule l'analyse de viabilité complète d'un scénario.

    `data` attend les clés : parametres, charges_externes, equipe,
    investissements, offres. Renvoie un dictionnaire de résultats détaillés.
    """
    p = Parametres.from_dict(data.get("parametres", {}))

    # ---- COÛTS FIXES (feuille FIXE) -------------------------------------
    lignes_charges = []
    total_charges_externes = 0.0
    for c in (data.get("charges_externes") or []):
        annuel = annualiser(c.get("montant_unitaire", 0) or 0, c.get("frequence", "annuel"))
        total_charges_externes += annuel
        lignes_charges.append({
            "description": c.get("description", ""),
            "frequence": c.get("frequence", "annuel"),
            "montant_unitaire": float(c.get("montant_unitaire", 0) or 0),
            "cout_annuel": annuel,
        })

    lignes_equipe = []
    total_remunerations = 0.0
    total_etp = 0.0
    for r in (data.get("equipe") or []):
        etp = float(r.get("etp", 0) or 0)
        net = float(r.get("remuneration_nette_mensuelle", 0) or 0)
        cout = cout_employeur(net, etp, p)
        total_remunerations += cout
        total_etp += etp
        lignes_equipe.append({
            "description": r.get("description", ""),
            "etp": etp,
            "remuneration_nette_mensuelle": net,
            "cout_total_annuel": cout,
        })

    lignes_invest = []
    total_amortissements = 0.0
    for i in (data.get("investissements") or []):
        montant = float(i.get("montant_investi", 0) or 0)
        duree = float(i.get("duree_amortissement", 0) or 0)
        amort = montant / duree if duree > 0 else 0.0
        total_amortissements += amort
        lignes_invest.append({
            "description": i.get("description", ""),
            "duree_amortissement": duree,
            "montant_investi": montant,
            "amortissement_annuel": amort,
        })

    couts_fixes = total_charges_externes + total_remunerations + total_amortissements

    # ---- OFFRES & COÛTS VARIABLES (feuille VARIABLE) --------------------
    lignes_offres = []
    ca_total = 0.0
    couts_variables_total = 0.0
    for o in (data.get("offres") or []):
        prix = float(o.get("prix", 0) or 0)
        cvar = float(o.get("cout_variable", 0) or 0)
        qte = float(o.get("quantite", 0) or 0)
        marge_unit = prix - cvar
        ca = prix * qte
        cv = cvar * qte
        ca_total += ca
        couts_variables_total += cv
        lignes_offres.append({
            "description": o.get("description", ""),
            "prix": prix,
            "cout_variable": cvar,
            "quantite": qte,
            "marge_brute_unitaire": marge_unit,
            "ca": ca,
            "couts_variables": cv,
            "marge_brute": ca - cv,
        })

    # Cotisation sur le chiffre d'affaires (CAE, portage, micro-entrepreneuse).
    cotisation_ca = ca_total * p.cotisation_ca_pct
    marge_brute_total = (ca_total - couts_variables_total) - cotisation_ca
    marge_nette = marge_brute_total - couts_fixes
    taux_marge_brute = (marge_brute_total / ca_total) if ca_total > 0 else 0.0

    # ---- SEUIL DE RENTABILITÉ (feuille VIABILITÉ) ----------------------
    seuil_ca = (couts_fixes / taux_marge_brute) if taux_marge_brute > 0 else None
    # Seuil incluant les prélèvements / dividendes cibles de la fondatrice.
    seuil_ca_avec_dividendes = (
        ((couts_fixes + p.dividendes_cibles) / taux_marge_brute)
        if taux_marge_brute > 0 else None
    )

    marge_brute_brute = sum(l["marge_brute"] for l in lignes_offres)
    for l in lignes_offres:
        part = (l["marge_brute"] / marge_brute_brute) if marge_brute_brute > 0 else 0.0
        fixes_alloues = couts_fixes * part
        cout_revient_unit = (
            l["cout_variable"] + (fixes_alloues / l["quantite"]) if l["quantite"] > 0
            else l["cout_variable"]
        )
        l["couts_fixes_alloues"] = fixes_alloues
        l["cout_revient_unitaire"] = cout_revient_unit
        l["seuil_quantite"] = (
            couts_fixes / l["marge_brute_unitaire"]
            if l["marge_brute_unitaire"] > 0 else None
        )
        l["rentable"] = l["prix"] >= cout_revient_unit

    taux_couverture = (marge_brute_total / couts_fixes) if couts_fixes > 0 else None

    return {
        "parametres": p.__dict__,
        "charges_externes": lignes_charges,
        "equipe": lignes_equipe,
        "investissements": lignes_invest,
        "offres": lignes_offres,
        "totaux": {
            "total_charges_externes": total_charges_externes,
            "total_remunerations": total_remunerations,
            "total_amortissements": total_amortissements,
            "total_etp": total_etp,
            "couts_fixes": couts_fixes,
            "ca_total": ca_total,
            "couts_variables_total": couts_variables_total,
            "cotisation_ca": cotisation_ca,
            "marge_brute_total": marge_brute_total,
            "marge_nette": marge_nette,
            "taux_marge_brute": taux_marge_brute,
            "seuil_ca": seuil_ca,
            "seuil_ca_avec_dividendes": seuil_ca_avec_dividendes,
            "dividendes_cibles": p.dividendes_cibles,
            "taux_couverture": taux_couverture,
            "viable": marge_nette >= p.dividendes_cibles,
        },
        "compte_resultat": {
            "chiffre_affaires": ca_total,
            "cotisation_ca": cotisation_ca,
            "couts_variables": couts_variables_total,
            "marge_brute": marge_brute_total,
            "charges_externes": total_charges_externes,
            "amortissements": total_amortissements,
            "remunerations": total_remunerations,
            "marge_nette": marge_nette,
        },
    }
