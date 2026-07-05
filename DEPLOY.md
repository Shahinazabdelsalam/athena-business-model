# 🚀 Déploiement d'Athena — GitHub + Railway

Ce guide vous mène de votre dossier local jusqu'à une application en ligne,
avec une base de données PostgreSQL nommée **Athena Business Model**.

> ⚠️ Ces étapes demandent VOS identifiants (GitHub, Railway). Elles ne peuvent
> pas être automatisées à votre place pour des raisons de sécurité — mais tout
> est prêt : il n'y a qu'à copier-coller.

---

## 0. Nettoyage préalable (une seule fois)

Le dossier contient un fichier `athena-repo.bundle` (toute l'histoire git déjà
committée) et un dossier `.git` incomplet à supprimer. Dans le dossier du projet :

```bash
# Supprimez le .git incomplet
rmdir /s /q .git        # Windows (PowerShell/cmd)
# ou :  rm -rf .git     # macOS/Linux
```

## 1. Créer le dépôt GitHub

1. Allez sur https://github.com/new
2. **Repository name** : `athena-business-model`
3. Laissez-le **vide** (ne cochez ni README ni .gitignore), puis *Create repository*.

## 2. Pousser le code

Deux options — la plus simple d'abord.

### Option A — à partir du bundle (recommandé, histoire incluse)
```bash
git clone athena-repo.bundle athena-business-model
cd athena-business-model
git remote set-url origin https://github.com/Shahinazabdelsalam/athena-business-model.git
git push -u origin main
```

### Option B — initialiser à neuf
```bash
git init -b main
git add -A
git commit -m "Athena Business Model — outil de viabilité économique"
git remote add origin https://github.com/Shahinazabdelsalam/athena-business-model.git
git push -u origin main
```

## 3. Déployer sur Railway

1. Allez sur https://railway.com/dashboard → **New Project** → **Deploy from GitHub repo**.
2. Sélectionnez `Shahinazabdelsalam/athena-business-model`. Railway lit le `Dockerfile`.
3. **Ajoutez la base de données** : bouton **+ New** → **Database** → **Add PostgreSQL**.
   - Renommez ce service **Athena Business Model** (Settings → Service name).
   - Railway crée automatiquement la variable `DATABASE_URL` partagée.
4. Sur le service **web**, onglet **Variables**, ajoutez :

   | Variable      | Valeur                                                              |
   |---------------|--------------------------------------------------------------------|
   | `SECRET_KEY`  | `ece1f7d0e846f098f272d8457539490f519cff1bde5bca8de06bb59620309055` |
   | `DATABASE_URL`| référencez celle du service Postgres (\${{Postgres.DATABASE_URL}}) |

5. Onglet **Settings** du service web → **Networking** → **Generate Domain**.
6. Le health check `/sante` confirme que tout tourne. Partagez l'URL publique ✊

---

## 🔑 Votre SECRET_KEY (générée, unique)

```
ece1f7d0e846f098f272d8457539490f519cff1bde5bca8de06bb59620309055
```

Gardez-la privée. Pour en régénérer une :
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```
