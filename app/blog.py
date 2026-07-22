"""
Blog Athena — les guides publiés.

Chaque article est un gabarit Jinja dans `templates/blog/`, et cette liste en
tient le registre : elle alimente la page /blog, les balises SEO de chaque
article et le sitemap. Pour publier un nouvel article : créer le gabarit
`templates/blog/<slug>.html` (il hérite de `blog/_article.html`) puis ajouter
une entrée ici, la plus récente en premier.
"""

ARTICLES = [
    {
        "slug": "comment-faire-un-business-model",
        "meta_title": "Comment faire un business model : guide en 6 étapes | Athena",
        "description": (
            "Comment créer un business model quand on lance son entreprise ? "
            "Méthode en 6 étapes, exemple concret et modèle gratuit pour vérifier "
            "que votre projet est viable."
        ),
        "titre": "Comment faire un business model : le guide en 6 étapes",
        "resume": (
            "Offre, cliente, prix, coûts, marge, scénarios : la méthode complète "
            "pour construire un modèle économique qui tient debout, sans jargon."
        ),
        "date": "2026-07-22",
        "date_fr": "22 juillet 2026",
        "lecture": "7 min",
    },
    {
        "slug": "calcul-seuil-de-rentabilite",
        "meta_title": "Calcul du seuil de rentabilité : méthode + exemple | Athena",
        "description": (
            "Apprenez à calculer votre seuil de rentabilité simplement : formule, "
            "exemple chiffré et point mort. Le chiffre d'affaires minimum pour ne "
            "plus perdre d'argent."
        ),
        "titre": "Calcul du seuil de rentabilité : la méthode simple (avec exemple)",
        "resume": (
            "La formule, un exemple chiffré de bout en bout et trois leviers pour "
            "faire baisser le chiffre d'affaires minimum dont vous avez besoin."
        ),
        "date": "2026-07-22",
        "date_fr": "22 juillet 2026",
        "lecture": "6 min",
    },
]

ARTICLES_PAR_SLUG = {a["slug"]: a for a in ARTICLES}
