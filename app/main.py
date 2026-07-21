"""
Athena Business Model — application FastAPI.

Un outil web d'analyse de viabilité économique, pensé pour les femmes
entrepreneuses. Chaque utilisatrice crée un compte, construit un ou plusieurs
modèles économiques et visualise leur viabilité (coûts fixes, offres,
marge, seuil de rentabilité, compte de résultat).
"""
import json
import logging
import os
import re
import time
import urllib.parse
from collections import defaultdict, deque
from pathlib import Path

import httpx
import stripe
from fastapi import FastAPI, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app import auth
from app.database import get_db, init_db, engine
from app.engine import calculer
from app.models import User, BusinessModel

logger = logging.getLogger("athena")
# uvicorn ne configure pas notre logger : on lui attache un handler explicite
# pour que les diagnostics (démarrage, Stripe, sécurité) soient visibles dans
# les logs Railway, y compris au niveau INFO.
if not logger.handlers:
    logger.setLevel(logging.INFO)
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(_h)
    logger.propagate = False

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Athena Business Model")

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
# Comparé à des emails stockés en minuscules → on normalise pour éviter
# qu'une majuscule dans la variable d'env verrouille l'accès admin.
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "").strip().lower()
SITE_URL = os.getenv("SITE_URL", "https://athenamodel.entangleeq.com")

# Stripe — abonnement Athena Pro : 30 jours gratuits puis 4,99 €/mois.
STRIPE_SECRET_KEY     = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID       = os.getenv("STRIPE_PRICE_ID", "")  # optionnel : sinon prix créé à la volée
PRO_PRICE_CENTS = 499
PRO_TRIAL_DAYS  = int(os.getenv("PRO_TRIAL_DAYS", "30"))

# Comptes gratuits : nombre maximum de modèles (illimité en Pro).
FREE_MODEL_LIMIT = 2

# Garde-fous de l'API publique de calcul (/api/calculer, sans authentification).
CALC_RATE_WINDOW = 60        # fenêtre glissante, en secondes
CALC_RATE_MAX    = 120       # requêtes max par IP et par fenêtre
CALC_MAX_BODY    = 256 * 1024  # taille max du corps JSON (256 Ko)
CALC_MAX_LIGNES  = 500       # nb max de lignes par section (offres, charges…)

# Validation d'email côté serveur (le formulaire n'est qu'une aide côté client).
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY
    # Échoue vite (et proprement) plutôt que de figer la page ~30 s si Stripe
    # est injoignable ou mal configuré en production.
    stripe.max_network_retries = 1
    try:
        try:
            from stripe import http_client as _stripe_http   # SDK < 15
        except Exception:
            from stripe import _http_client as _stripe_http   # SDK >= 15
        stripe.default_http_client = _stripe_http.new_default_http_client(timeout=8)
    except Exception as _exc:  # pragma: no cover — selon la version du SDK
        logger.warning("Timeout Stripe non configuré (%s) : les appels peuvent être lents.", _exc)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals["admin_email"] = ADMIN_EMAIL
templates.env.globals["site_url"] = SITE_URL

GOOGLE_AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USER_URL  = "https://www.googleapis.com/oauth2/v2/userinfo"


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------
# Valeurs par défaut connues (présentes dans le dépôt public) = clé NON secrète.
# Avec l'une d'elles, n'importe qui peut forger un cookie de session et usurper
# n'importe quel compte, y compris l'administratrice.
_INSECURE_SECRET_KEYS = {
    "",
    "dev-secret-change-me-in-production",
    "changez-moi-en-production",
}


def _is_production() -> bool:
    """Heuristique : sommes-nous sur un déploiement (Railway / Postgres) et non en local ?"""
    if os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_ENVIRONMENT_NAME"):
        return True
    if os.getenv("APP_ENV", "").lower() in ("prod", "production"):
        return True
    return os.getenv("DATABASE_URL", "").startswith(("postgres://", "postgresql://"))


def _check_security_config():
    """
    Garde-fou de démarrage : en production, refuse de booter si les sessions
    seraient falsifiables. En local, se contente d'un avertissement bruyant.
    """
    if auth.SECRET_KEY in _INSECURE_SECRET_KEYS:
        msg = (
            "SECRET_KEY n'est pas configurée : les cookies de session seraient "
            "falsifiables et n'importe qui pourrait usurper un compte (y compris "
            "l'administratrice). Générez-en une avec "
            "`python -c \"import secrets; print(secrets.token_hex(32))\"` "
            "puis définissez la variable d'environnement SECRET_KEY."
        )
        if _is_production():
            raise RuntimeError("Démarrage refusé — " + msg)
        logger.warning("⚠️  %s (toléré en développement local)", msg)

    if _is_production() and not ADMIN_EMAIL:
        logger.warning(
            "⚠️  ADMIN_EMAIL n'est pas définie : la page /admin est inaccessible "
            "à tout le monde, y compris à vous."
        )


def _check_stripe_config():
    """
    Diagnostic de paiement au démarrage : dit clairement, dans les logs, si
    l'abonnement Pro est opérationnel — la cause n°1 du « 503 Stripe » en prod
    est une variable d'environnement manquante.
    """
    missing = [name for name, val in (
        ("STRIPE_SECRET_KEY", STRIPE_SECRET_KEY),
        ("STRIPE_WEBHOOK_SECRET", STRIPE_WEBHOOK_SECRET),
    ) if not val]

    if not STRIPE_SECRET_KEY:
        logger.warning(
            "💳  Stripe non configuré (%s manquante%s) : l'abonnement Athena Pro "
            "est DÉSACTIVÉ, le calculateur gratuit fonctionne normalement.",
            ", ".join(missing), "s" if len(missing) > 1 else "",
        )
        return

    # Le mode se lit au milieu de la clé : vaut pour les clés standard (sk_…)
    # comme pour les clés restreintes (rk_…), recommandées côté sécurité.
    if "_live_" in STRIPE_SECRET_KEY:
        mode = "LIVE 🔴"
    elif "_test_" in STRIPE_SECRET_KEY:
        mode = "TEST 🧪"
    else:
        mode = "inconnu ⚠️"
    kind = "restreinte" if STRIPE_SECRET_KEY.startswith("rk_") else "standard"

    logger.info(
        "💳  Stripe actif — mode %s · clé %s · price_id=%s · webhook=%s",
        mode,
        kind,
        STRIPE_PRICE_ID or "(créé à la volée — nécessite Prices:write sur la clé)",
        "configuré" if STRIPE_WEBHOOK_SECRET else "MANQUANT (bascule Premium non automatique)",
    )


@app.on_event("startup")
def _startup():
    _check_security_config()  # peut interrompre volontairement le démarrage en production
    _check_stripe_config()

    try:
        init_db()
        logger.info("Database initialized successfully.")
    except Exception as exc:
        logger.error("Database init failed at startup: %s — app will start without DB.", exc)
        return

    # Migration : ajoute les colonnes manquantes sans casser les données existantes.
    _migrate()


def _migrate():
    try:
        with engine.connect() as conn:
            insp = inspect(engine)
            existing = {c["name"] for c in insp.get_columns("users")}

            if "is_premium" not in existing:
                if engine.dialect.name == "postgresql":
                    conn.execute(text(
                        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_premium BOOLEAN NOT NULL DEFAULT FALSE"
                    ))
                else:
                    conn.execute(text(
                        "ALTER TABLE users ADD COLUMN is_premium BOOLEAN NOT NULL DEFAULT 0"
                    ))
                conn.commit()
                logger.info("Migration: added is_premium column to users.")

            for col in ("stripe_customer_id", "stripe_subscription_id"):
                if col not in existing:
                    if engine.dialect.name == "postgresql":
                        conn.execute(text(
                            f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} VARCHAR(255)"
                        ))
                    else:
                        conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} VARCHAR(255)"))
                    conn.commit()
                    logger.info("Migration: added %s column to users.", col)

            if "password_hash" in existing:
                # Rend la colonne nullable pour les comptes Google (si ce n'est pas déjà le cas).
                # Postgres uniquement — SQLite ne supporte pas ALTER COLUMN.
                if engine.dialect.name == "postgresql":
                    conn.execute(text(
                        "ALTER TABLE users ALTER COLUMN password_hash DROP NOT NULL"
                    ))
                    conn.commit()
    except Exception as exc:
        logger.warning("Migration warning: %s", exc)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def get_current_user(request: Request, db: Session) -> User | None:
    uid = auth.current_user_id(request)
    if not uid:
        return None
    return db.query(User).filter(User.id == uid).first()


def require_user(request: Request, db: Session) -> User:
    user = get_current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Non authentifiée")
    return user


def require_admin(request: Request, db: Session) -> User:
    user = get_current_user(request, db)
    if not user or not ADMIN_EMAIL or user.email != ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Accès réservé à l'administratrice")
    return user


def _set_session_cookie(resp, user_id: int, request: Request) -> None:
    """
    Pose le cookie de session. Il est marqué `Secure` dès qu'on est en
    production (le proxy Railway termine le TLS, donc request.url.scheme peut
    valoir http en interne — on se fie aussi à _is_production()).
    """
    secure = _is_production() or request.url.scheme == "https"
    resp.set_cookie(
        "session",
        auth.make_session_cookie(user_id),
        httponly=True,
        samesite="lax",
        secure=secure,
    )


# Limiteur de débit en mémoire (par process) pour l'API publique de calcul.
_calc_hits: dict[str, deque] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    """IP de l'appelante — derrière le proxy Railway elle est dans X-Forwarded-For."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _calc_rate_limited(ip: str) -> bool:
    """Fenêtre glissante : vrai si l'IP dépasse CALC_RATE_MAX sur CALC_RATE_WINDOW."""
    now = time.monotonic()
    hits = _calc_hits[ip]
    while hits and hits[0] <= now - CALC_RATE_WINDOW:
        hits.popleft()
    if len(hits) >= CALC_RATE_MAX:
        return True
    hits.append(now)
    # Borne mémoire : purge les IP inactives, et repart de zéro si ça déborde.
    if len(_calc_hits) > 20_000:
        for k in [k for k, v in _calc_hits.items() if not v]:
            _calc_hits.pop(k, None)
        if len(_calc_hits) > 20_000:
            _calc_hits.clear()
    return False


# --------------------------------------------------------------------------
# Pages publiques
# --------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    return templates.TemplateResponse("index.html", {"request": request, "user": user})


@app.get("/inscription", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "error": None})


@app.post("/inscription")
def register(
    request: Request,
    email: str = Form(""),
    nom: str = Form(""),
    password: str = Form(""),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": "Veuillez saisir une adresse email valide."},
            status_code=400,
        )
    if db.query(User).filter(User.email == email).first():
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": "Cet email est déjà utilisé."},
            status_code=400,
        )
    if len(password) < 6:
        return templates.TemplateResponse(
            "register.html",
            {"request": request, "error": "Le mot de passe doit faire au moins 6 caractères."},
            status_code=400,
        )
    user = User(
        email=email,
        nom=nom.strip(),
        password_hash=auth.hash_password(password),
        is_premium=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    resp = RedirectResponse(url="/tableau-de-bord", status_code=303)
    _set_session_cookie(resp, user.id, request)
    return resp


@app.get("/connexion", response_class=HTMLResponse)
def login_page(request: Request):
    error = request.query_params.get("error")
    msg = {
        "google_non_configuré": "La connexion Google n'est pas encore configurée.",
        "google_annulé": "Connexion Google annulée.",
        "google_token_invalid": "Erreur lors de la connexion Google. Veuillez réessayer.",
        "google_email_manquant": "Google n'a pas fourni d'email. Veuillez réessayer.",
    }.get(error)
    return templates.TemplateResponse("login.html", {"request": request, "error": msg})


@app.post("/connexion")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if not user or not user.password_hash or user.password_hash == "__google_oauth__":
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Email ou mot de passe incorrect."},
            status_code=400,
        )
    if not auth.verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Email ou mot de passe incorrect."},
            status_code=400,
        )
    resp = RedirectResponse(url="/tableau-de-bord", status_code=303)
    _set_session_cookie(resp, user.id, request)
    return resp


@app.get("/deconnexion")
def logout():
    resp = RedirectResponse(url="/", status_code=303)
    resp.delete_cookie("session")
    return resp


# --------------------------------------------------------------------------
# Google OAuth
# --------------------------------------------------------------------------
@app.get("/auth/google", name="google_login")
def google_login(request: Request):
    client_id = os.getenv("GOOGLE_CLIENT_ID", "")
    if not client_id:
        return RedirectResponse("/connexion?error=google_non_configuré", status_code=303)
    redirect_uri = _google_redirect_uri(request)
    params = urllib.parse.urlencode({
        "client_id":     client_id,
        "redirect_uri":  redirect_uri,
        "response_type": "code",
        "scope":         "openid email profile",
        "access_type":   "online",
    })
    return RedirectResponse(f"{GOOGLE_AUTH_URL}?{params}", status_code=302)


@app.get("/auth/google/callback", name="google_callback")
async def google_callback(
    request: Request,
    code: str = None,
    error: str = None,
    db: Session = Depends(get_db),
):
    if error or not code:
        return RedirectResponse("/connexion?error=google_annulé", status_code=303)

    client_id     = os.getenv("GOOGLE_CLIENT_ID", "")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
    redirect_uri  = _google_redirect_uri(request)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            token_r = await client.post(GOOGLE_TOKEN_URL, data={
                "client_id":     client_id,
                "client_secret": client_secret,
                "code":          code,
                "redirect_uri":  redirect_uri,
                "grant_type":    "authorization_code",
            })
            if token_r.status_code != 200:
                logger.error("Google token exchange failed %s: %s | redirect_uri=%s",
                             token_r.status_code, token_r.text, redirect_uri)
                return RedirectResponse("/connexion?error=google_token_invalid", status_code=303)

            access_token = token_r.json().get("access_token", "")
            user_r = await client.get(
                GOOGLE_USER_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if user_r.status_code != 200:
                logger.error("Google userinfo failed %s: %s", user_r.status_code, user_r.text)
                return RedirectResponse("/connexion?error=google_token_invalid", status_code=303)
            profile = user_r.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.error("Google OAuth callback error: %s", exc)
        return RedirectResponse("/connexion?error=google_token_invalid", status_code=303)

    email = (profile.get("email") or "").lower().strip()
    nom   = profile.get("name", "")

    if not email:
        return RedirectResponse("/connexion?error=google_email_manquant", status_code=303)

    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(email=email, nom=nom, password_hash="__google_oauth__", is_premium=False)
        db.add(user)
        db.commit()
        db.refresh(user)

    resp = RedirectResponse(url="/tableau-de-bord", status_code=303)
    _set_session_cookie(resp, user.id, request)
    return resp


def _external_base_url(request: Request) -> str:
    # Reconstruit l'URL publique depuis la requête en forçant HTTPS si derrière un proxy.
    base = str(request.base_url).rstrip("/")
    if base.startswith("http://") and not base.startswith("http://localhost"):
        base = "https://" + base[7:]
    return base


def _google_redirect_uri(request: Request) -> str:
    env_uri = os.getenv("GOOGLE_REDIRECT_URI", "")
    if env_uri:
        return env_uri
    return _external_base_url(request) + "/auth/google/callback"


# --------------------------------------------------------------------------
# Pages authentifiées
# --------------------------------------------------------------------------
@app.get("/tableau-de-bord", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/connexion", status_code=303)
    models = (
        db.query(BusinessModel)
        .filter(BusinessModel.user_id == user.id)
        .order_by(BusinessModel.updated_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "dashboard.html", {"request": request, "user": user, "models": models}
    )


@app.get("/modele/{model_id}", response_class=HTMLResponse)
def calculator_page(model_id: int, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/connexion", status_code=303)
    model = (
        db.query(BusinessModel)
        .filter(BusinessModel.id == model_id, BusinessModel.user_id == user.id)
        .first()
    )
    if not model:
        raise HTTPException(status_code=404, detail="Modèle introuvable")
    return templates.TemplateResponse(
        "calculator.html",
        {"request": request, "user": user, "model": model, "model_data": model.data},
    )


# Calculateur libre (sans compte) — pour essayer avant de s'inscrire.
@app.get("/essayer", response_class=HTMLResponse)
def try_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    return templates.TemplateResponse(
        "calculator.html",
        {"request": request, "user": user, "model": None, "model_data": "{}"},
    )


# --------------------------------------------------------------------------
# Page abonnement / upgrade + Stripe
# --------------------------------------------------------------------------
@app.get("/upgrade", response_class=HTMLResponse)
def upgrade_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    return templates.TemplateResponse("upgrade.html", {
        "request": request,
        "user": user,
        "stripe_enabled": bool(STRIPE_SECRET_KEY),
        "error": request.query_params.get("error"),
    })


@app.post("/stripe/checkout")
def stripe_checkout(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse("/connexion", status_code=303)
    if user.is_premium:
        return RedirectResponse("/tableau-de-bord", status_code=303)
    if not STRIPE_SECRET_KEY:
        return RedirectResponse("/upgrade?error=paiement_indisponible", status_code=303)

    if STRIPE_PRICE_ID:
        line_item = {"price": STRIPE_PRICE_ID, "quantity": 1}
    else:
        line_item = {
            "price_data": {
                "currency": "eur",
                "unit_amount": PRO_PRICE_CENTS,
                "recurring": {"interval": "month"},
                "product_data": {"name": "Athena Pro"},
            },
            "quantity": 1,
        }

    base = _external_base_url(request)
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[line_item],
            subscription_data={"trial_period_days": PRO_TRIAL_DAYS},
            customer=user.stripe_customer_id or None,
            customer_email=None if user.stripe_customer_id else user.email,
            client_reference_id=str(user.id),
            success_url=base + "/stripe/succes?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=base + "/upgrade",
            allow_promotion_codes=True,
        )
    except Exception as exc:
        logger.error("Stripe checkout creation failed: %s", exc)
        return RedirectResponse("/upgrade?error=paiement_indisponible", status_code=303)
    return RedirectResponse(session.url, status_code=303)


@app.get("/stripe/succes")
def stripe_success(request: Request, session_id: str = "", db: Session = Depends(get_db)):
    """Retour de Checkout : active Pro immédiatement sans attendre le webhook."""
    user = get_current_user(request, db)
    if user and session_id and STRIPE_SECRET_KEY:
        try:
            session = stripe.checkout.Session.retrieve(session_id)
            if session.client_reference_id == str(user.id) and session.status == "complete":
                user.is_premium = True
                user.stripe_customer_id = session.customer
                user.stripe_subscription_id = session.subscription
                db.commit()
        except Exception as exc:
            logger.error("Stripe success verification failed: %s", exc)
    return RedirectResponse("/tableau-de-bord?pro=1", status_code=303)


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    """Source de vérité : activation à la souscription, désactivation à la résiliation."""
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook Stripe non configuré")
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, signature, STRIPE_WEBHOOK_SECRET)
    except Exception as exc:
        logger.warning("Stripe webhook rejeté : %s", exc)
        raise HTTPException(status_code=400, detail="Signature invalide")

    obj = event["data"]["object"]

    if event["type"] == "checkout.session.completed":
        uid = obj.get("client_reference_id")
        try:
            target = db.query(User).filter(User.id == int(uid)).first() if uid else None
        except (ValueError, TypeError):
            target = None
        if target:
            target.is_premium = True
            target.stripe_customer_id = obj.get("customer")
            target.stripe_subscription_id = obj.get("subscription")
            db.commit()

    elif event["type"] in ("customer.subscription.updated", "customer.subscription.deleted"):
        target = (
            db.query(User)
            .filter(User.stripe_subscription_id == obj.get("id"))
            .first()
        ) or (
            db.query(User)
            .filter(User.stripe_customer_id == obj.get("customer"))
            .first()
        )
        if target:
            target.is_premium = obj.get("status") in ("active", "trialing")
            db.commit()

    return {"ok": True}


@app.post("/stripe/portail")
def stripe_portal(request: Request, db: Session = Depends(get_db)):
    """Portail client Stripe : gérer sa carte, ses factures, résilier."""
    user = require_user(request, db)
    if not (STRIPE_SECRET_KEY and user.stripe_customer_id):
        return RedirectResponse("/upgrade", status_code=303)
    try:
        session = stripe.billing_portal.Session.create(
            customer=user.stripe_customer_id,
            return_url=_external_base_url(request) + "/tableau-de-bord",
        )
    except Exception as exc:
        logger.error("Stripe portal creation failed: %s", exc)
        return RedirectResponse("/upgrade?error=paiement_indisponible", status_code=303)
    return RedirectResponse(session.url, status_code=303)


# --------------------------------------------------------------------------
# Administration
# --------------------------------------------------------------------------
@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, db: Session = Depends(get_db)):
    user = require_admin(request, db)
    users = (
        db.query(User)
        .order_by(User.created_at.desc())
        .all()
    )
    total_models = db.query(BusinessModel).count()
    return templates.TemplateResponse("admin.html", {
        "request":      request,
        "user":         user,
        "users":        users,
        "total_models": total_models,
    })


@app.post("/admin/toggle-premium/{user_id}")
def admin_toggle_premium(user_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Vous ne pouvez pas modifier votre propre statut.")
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404)
    target.is_premium = not target.is_premium
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.delete("/admin/users/{user_id}")
def admin_delete_user(user_id: int, request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Vous ne pouvez pas supprimer votre propre compte.")
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404)
    db.delete(target)
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# API publique
# --------------------------------------------------------------------------
@app.post("/api/calculer")
async def api_calculer(request: Request):
    """Calcul de viabilité — public (sans authentification), calcul pur."""
    if _calc_rate_limited(_client_ip(request)):
        raise HTTPException(status_code=429, detail="Trop de requêtes. Réessayez dans un instant.")

    cl = request.headers.get("content-length", "")
    if cl.isdigit() and int(cl) > CALC_MAX_BODY:
        raise HTTPException(status_code=413, detail="Charge utile trop volumineuse.")

    body = await request.body()
    if len(body) > CALC_MAX_BODY:
        raise HTTPException(status_code=413, detail="Charge utile trop volumineuse.")
    try:
        data = json.loads(body or b"{}")
    except Exception:
        raise HTTPException(status_code=400, detail="JSON invalide.")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Format attendu : un objet JSON.")
    for key in ("charges_externes", "equipe", "investissements", "offres"):
        v = data.get(key)
        if isinstance(v, list) and len(v) > CALC_MAX_LIGNES:
            raise HTTPException(status_code=413, detail=f"Trop de lignes dans « {key} ».")

    try:
        return JSONResponse(calculer(data))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erreur de calcul : {e}")


@app.get("/api/modeles")
def api_list_models(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    models = (
        db.query(BusinessModel)
        .filter(BusinessModel.user_id == user.id)
        .order_by(BusinessModel.updated_at.desc())
        .all()
    )
    return [
        {"id": m.id, "nom": m.nom, "updated_at": m.updated_at.isoformat()}
        for m in models
    ]


@app.post("/api/modeles")
async def api_create_model(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    if not user.is_premium:
        count = db.query(BusinessModel).filter(BusinessModel.user_id == user.id).count()
        if count >= FREE_MODEL_LIMIT:
            raise HTTPException(
                status_code=403,
                detail="Limite gratuite atteinte (2 modèles). Passez à Athena Pro pour créer des modèles illimités.",
            )
    body = await request.json()
    model = BusinessModel(
        user_id=user.id,
        nom=body.get("nom", "Mon modèle"),
        data=json.dumps(body.get("data", {}), ensure_ascii=False),
    )
    db.add(model)
    db.commit()
    db.refresh(model)
    return {"id": model.id, "nom": model.nom}


@app.put("/api/modeles/{model_id}")
async def api_update_model(model_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    model = (
        db.query(BusinessModel)
        .filter(BusinessModel.id == model_id, BusinessModel.user_id == user.id)
        .first()
    )
    if not model:
        raise HTTPException(status_code=404, detail="Modèle introuvable")
    body = await request.json()
    if "nom" in body:
        model.nom = body["nom"]
    if "data" in body:
        model.data = json.dumps(body["data"], ensure_ascii=False)
    db.commit()
    return {"id": model.id, "nom": model.nom}


@app.delete("/api/modeles/{model_id}")
def api_delete_model(model_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    model = (
        db.query(BusinessModel)
        .filter(BusinessModel.id == model_id, BusinessModel.user_id == user.id)
        .first()
    )
    if not model:
        raise HTTPException(status_code=404, detail="Modèle introuvable")
    db.delete(model)
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# SEO — référencement Google
# --------------------------------------------------------------------------
@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin\n"
        "Disallow: /tableau-de-bord\n"
        "Disallow: /modele/\n"
        "Disallow: /api/\n"
        f"Sitemap: {SITE_URL}/sitemap.xml\n"
    )


@app.get("/sitemap.xml")
def sitemap():
    pages = [
        ("/", "weekly", "1.0"),
        ("/essayer", "weekly", "0.9"),
        ("/upgrade", "monthly", "0.8"),
        ("/inscription", "monthly", "0.7"),
        ("/connexion", "monthly", "0.3"),
    ]
    urls = "".join(
        f"<url><loc>{SITE_URL}{path}</loc><changefreq>{freq}</changefreq><priority>{prio}</priority></url>"
        for path, freq, prio in pages
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{urls}</urlset>"
    )
    return Response(content=xml, media_type="application/xml")


@app.get("/sante")
def health():
    return {"status": "ok", "service": "Athena Business Model"}
