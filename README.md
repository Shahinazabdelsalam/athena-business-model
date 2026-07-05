# ♀ Athena Business Model

**Un outil web d'analyse de viabilité économique, au service de l'autonomie des femmes entrepreneuses.**

Athena reprend et améliore la logique de l'outil tableur (feuilles *BIENVENUE / FIXE / VARIABLE / VIABILITÉ*)
pour répondre à une seule question : **sur une année-type, mes revenus couvrent-ils les coûts nécessaires pour les générer ?**

Chaque utilisatrice crée un compte, construit un ou plusieurs modèles économiques, et visualise
en temps réel leur viabilité : coûts fixes, offres, marge, **seuil de rentabilité** et compte de résultat simplifié.

---

## ✨ Fonctionnalités

- **Comptes utilisatrices** — inscription, connexion, sessions signées (aucun mot de passe stocké en clair).
- **Modèles sauvegardés** — chaque femme gère ses propres projets, privés, en base Postgres.
- **Calcul en temps réel** — modifiez un prix, une charge, une rémunération : tout se recalcule instantanément.
- **Analyse complète** :
  - Charges externes (loyer, assurances, logiciels…) annualisées selon leur fréquence.
  - Équipe : coût employeur par ETP, avec ou sans cotisations patronales et taxe sur les salaires.
  - Investissements : amortissement annuel (montant ÷ durée).
  - Offres : prix, coût variable, volume → marge brute par offre.
  - Cotisation sur le chiffre d'affaires (CAE, portage, micro-entrepreneuse).
- **Seuil de rentabilité** — CA nécessaire pour l'équilibre + coût de revient et seuil par offre.
- **Compte de résultat simplifié** et **graphique revenus vs coûts**.
- **Mode découverte** (`/essayer`) — tester sans compte avant de s'inscrire.

## 🧱 Stack technique

- **Backend** : FastAPI (Python) + SQLAlchemy 2.0
- **Base de données** : PostgreSQL (SQLite en local par défaut)
- **Frontend** : Jinja2 + JavaScript vanilla + Chart.js
- **Moteur de calcul** : `app/engine.py`, pur et testé (`tests/test_engine.py`)
- **Déploiement** : Docker / Railway

## 🚀 Démarrage local

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
# → http://localhost:8000
```

Sans `DATABASE_URL`, l'app utilise automatiquement SQLite (`athena.db`).

## 🧪 Tests

```bash
python -m unittest tests.test_engine -v
```

## ☁️ Déploiement sur Railway

1. Créez un projet sur [railway.com](https://railway.com/dashboard) → **Deploy from GitHub repo**.
2. Ajoutez un service **PostgreSQL** (nommé *Athena Business Model*). Railway crée la variable `DATABASE_URL`.
3. Dans le service web, ajoutez la variable `SECRET_KEY` :
   ```bash
   python -c "import secrets; print(secrets.token_hex(32))"
   ```
4. Railway détecte le `Dockerfile` et déploie. Health check : `/sante`.

## 📁 Structure

```
athena-business-model/
├── app/
│   ├── main.py          # Routes FastAPI + API
│   ├── engine.py        # Moteur de calcul de viabilité (pur, testé)
│   ├── database.py      # Connexion SQLAlchemy (Postgres/SQLite)
│   ├── models.py        # Tables User + BusinessModel
│   ├── auth.py          # Hachage mots de passe + sessions
│   ├── templates/       # Pages (Jinja2)
│   └── static/          # CSS + JS du calculateur
├── tests/test_engine.py # 11 tests du moteur
├── Dockerfile
├── railway.json
├── requirements.txt
└── README.md
```

## ⚠️ Ce que l'outil n'intègre pas (comme l'original)

Impôt sur les sociétés, stocks et financement — sans influence directe sur la viabilité.
Les intérêts d'emprunt et coûts de stockage se saisissent comme charges externes.

---

*Athena — l'indépendance financière comme levier d'émancipation.* ✊♀
