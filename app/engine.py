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

# Horizon de la projection pluriannuelle (An 1 → An 3).
ANNEES_PROJECTION = 3


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


def _params_projection(data: Dict[str, Any]) -> Dict[str, float]:
    """
    Extrait les taux de la projection (bloc `pro`) en fractions.

    Défauts alignés sur le frontend : +10 %/an de CA, +3 %/an de charges,
    scénarios ×0,6 (pessimiste) et ×1,5 (optimiste). Ces taux ne servent qu'à
    la projection ; ils n'influencent pas l'analyse de l'An 1.
    """
    pro = data.get("pro") or {}

    def num(key: str, default: float) -> float:
        v = pro.get(key)
        if v is None:
            return float(default)
        try:
            return float(v)
        except (TypeError, ValueError):
            return float(default)

    return {
        "croissance_ca": num("croissance_ca", 10.0) / 100.0,
        "croissance_charges": num("croissance_charges", 3.0) / 100.0,
        "sc_pessimiste": num("sc_pessimiste", 0.6),
        "sc_optimiste": num("sc_optimiste", 1.5),
    }


def normaliser_volumes(offre: Dict[str, Any], annees: int, croissance_ca: float) -> list:
    """
    Volume (nb vendu/an) d'une offre pour chacune des `annees` de projection.

    Priorité au champ `volumes` (une valeur par année : ex. pilotes [3, 0, 0],
    licences [0, 10, 11]). Rétro-compatibilité : un ancien modèle ne portant
    qu'un `quantite`/`nbParAn` unique le traite comme le volume de l'An 1.

    Les années non renseignées (None, absente ou vide) sont auto-remplies à
    partir de l'année précédente et du taux de croissance du CA — ce qui
    reproduit exactement l'ancienne projection uniforme. L'auto-remplissage
    n'arrondit pas, pour garantir la non-régression des modèles existants
    (l'arrondi entier suggéré à la saisie est une aide d'affichage, côté
    frontend, pas une valeur imposée). Un 0 explicite reste un 0 : c'est ainsi
    qu'on modélise une offre qui n'existe pas (encore ou plus) une année donnée.
    """
    raw = offre.get("volumes")
    if not isinstance(raw, list):
        q = offre.get("quantite")
        if q is None:
            q = offre.get("nbParAn", offre.get("nb_par_an", 0))
        raw = [q]

    vols = []
    for n in range(annees):
        v = raw[n] if n < len(raw) else None
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.strip() != ""):
            vols.append(float(v))          # valeur explicite (0 compris)
        else:
            prev = vols[n - 1] if n > 0 else 0.0
            vols.append(prev * (1 + croissance_ca))   # auto-remplissage
    return vols


def _verdict_trajectoire(annees: list) -> Dict[str, Any]:
    """
    Lit la trajectoire de viabilité au-delà du seul verdict de l'An 1.

    - `viable`     : rentable dès l'An 1.
    - `amorcage`   : An 1 déficitaire mais viable à partir d'une année suivante
                     (profil normal de démarrage) — on expose l'année de bascule
                     et le besoin de trésorerie à financer d'ici là.
    - `non_viable` : jamais viable sur l'horizon.

    Le besoin de trésorerie = cumul des marges nettes négatives avant la
    première année viable (ce qu'il faut couvrir pour tenir : apport, ARE/ARCE,
    prêt d'honneur…).
    """
    viables = [a["viable"] for a in annees]
    if not viables:
        etat, premier_viable = "indetermine", None
    elif viables[0]:
        etat, premier_viable = "viable", 1
    elif any(viables):
        etat = "amorcage"
        premier_viable = next(i + 1 for i, v in enumerate(viables) if v)
    else:
        etat, premier_viable = "non_viable", None

    if premier_viable:
        besoin = -sum(min(0.0, a["marge_nette"]) for a in annees[: premier_viable - 1])
    else:
        besoin = 0.0

    return {
        "etat": etat,
        "premiere_annee_viable": premier_viable,
        "besoin_tresorerie": besoin,
    }


def _projeter(
    offres_norm: list,
    cotisation_ca_pct: float,
    couts_fixes_recurrents_0: float,
    couts_fixes_uniques_0: float,
    dividendes_cibles: float,
    params_proj: Dict[str, float],
    annees: int = ANNEES_PROJECTION,
) -> Dict[str, Any]:
    """
    Projection pluriannuelle fondée sur les volumes par année de chaque offre.

    Le CA et les coûts variables suivent les volumes propres à chaque année (et
    non un taux global) : c'est ce qui permet de représenter une montée en
    charge (An 1 = pilotes d'amorçage, An 2+ = offres de croisière). Les charges
    récurrentes croissent au taux d'évolution des charges ; les charges à
    fréquence `unique` (frais de création, adhésion…) ne pèsent que sur l'An 1.
    """
    g_ch = params_proj["croissance_charges"]

    def annee(n: int, volume_mult: float = 1.0) -> Dict[str, Any]:
        ca = sum(o["prix"] * o["volumes"][n] * volume_mult for o in offres_norm)
        cvar = sum(o["cout_variable"] * o["volumes"][n] * volume_mult for o in offres_norm)
        cotisation = ca * cotisation_ca_pct
        marge_brute = (ca - cvar) - cotisation
        charges = couts_fixes_recurrents_0 * ((1 + g_ch) ** n)
        if n == 0:
            charges += couts_fixes_uniques_0     # charges uniques : An 1 seulement
        marge_nette = marge_brute - charges
        return {
            "annee": n + 1,
            "ca": ca,
            "couts_variables": cvar,
            "cotisation_ca": cotisation,
            "marge_brute": marge_brute,
            "charges": charges,
            "marge_nette": marge_nette,
            "viable": marge_nette >= dividendes_cibles,
        }

    def serie(volume_mult: float = 1.0) -> list:
        return [annee(n, volume_mult) for n in range(annees)]

    base = serie(1.0)
    return {
        "annees": base,
        "trajectoire": _verdict_trajectoire(base),
        "scenarios": {
            "pessimiste": {"mult": params_proj["sc_pessimiste"],
                           "annees": serie(params_proj["sc_pessimiste"])},
            "realiste":   {"mult": 1.0, "annees": base},
            "optimiste":  {"mult": params_proj["sc_optimiste"],
                           "annees": serie(params_proj["sc_optimiste"])},
        },
    }


def calculer(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Calcule l'analyse de viabilité complète d'un scénario.

    `data` attend les clés : parametres, charges_externes, equipe,
    investissements, offres. Renvoie un dictionnaire de résultats détaillés,
    dont une `projection` pluriannuelle (volumes par année, verdict de
    trajectoire, scénarios de volume).
    """
    p = Parametres.from_dict(data.get("parametres", {}))

    # ---- COÛTS FIXES (feuille FIXE) -------------------------------------
    lignes_charges = []
    total_charges_externes = 0.0
    # Pour la projection : on distingue le récurrent (qui court chaque année) des
    # charges à fréquence `unique` (frais de création, adhésion…), qui ne pèsent
    # que sur l'An 1 et ne doivent donc pas être reconduites/augmentées ensuite.
    charges_externes_recurrentes = 0.0
    charges_externes_uniques = 0.0
    for c in (data.get("charges_externes") or []):
        freq = c.get("frequence", "annuel")
        annuel = annualiser(c.get("montant_unitaire", 0) or 0, freq)
        total_charges_externes += annuel
        if freq == "unique":
            charges_externes_uniques += annuel
        else:
            charges_externes_recurrentes += annuel
        lignes_charges.append({
            "description": c.get("description", ""),
            "frequence": freq,
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
    # Base récurrente de la projection : rémunérations et amortissements courent
    # chaque année (comme le récurrent externe) et croissent au taux d'évolution
    # des charges. Seul le récurrent grossit ; les charges uniques restent An 1.
    couts_fixes_recurrents = (
        charges_externes_recurrentes + total_remunerations + total_amortissements
    )
    couts_fixes_uniques = charges_externes_uniques

    # ---- OFFRES & COÛTS VARIABLES (feuille VARIABLE) --------------------
    params_proj = _params_projection(data)
    lignes_offres = []
    offres_norm = []          # {prix, cout_variable, volumes[par année]} pour la projection
    ca_total = 0.0
    couts_variables_total = 0.0
    for o in (data.get("offres") or []):
        prix = float(o.get("prix", 0) or 0)
        cvar = float(o.get("cout_variable", 0) or 0)
        # Volume par année (rétro-compatible avec l'ancien `quantite` unique).
        # L'analyse An 1 s'appuie sur le volume de la 1re année (amorçage).
        volumes = normaliser_volumes(o, ANNEES_PROJECTION, params_proj["croissance_ca"])
        qte = volumes[0]
        marge_unit = prix - cvar
        ca = prix * qte
        cv = cvar * qte
        ca_total += ca
        couts_variables_total += cv
        offres_norm.append({"prix": prix, "cout_variable": cvar, "volumes": volumes})
        lignes_offres.append({
            "description": o.get("description", ""),
            "prix": prix,
            "cout_variable": cvar,
            "quantite": qte,
            "volumes": volumes,
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

    # ---- PROJECTION PLURIANNUELLE (montée en charge) -------------------
    # Volumes par année → CA/marges par année ; charges récurrentes indexées,
    # charges uniques cantonnées à l'An 1 ; verdict de trajectoire + scénarios.
    projection = _projeter(
        offres_norm=offres_norm,
        cotisation_ca_pct=p.cotisation_ca_pct,
        couts_fixes_recurrents_0=couts_fixes_recurrents,
        couts_fixes_uniques_0=couts_fixes_uniques,
        dividendes_cibles=p.dividendes_cibles,
        params_proj=params_proj,
    )

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
        "projection": projection,
    }
