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
import urllib.parse
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app import auth
from app.database import get_db, init_db, engine
from app.engine import calculer
from app.models import User, BusinessModel

logger = logging.getLogger("athena")

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Athena Business Model")

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals["admin_email"] = ADMIN_EMAIL

GOOGLE_AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USER_URL  = "https://www.googleapis.com/oauth2/v2/userinfo"


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------
@app.on_event("startup")
def _startup():
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
    email: str = Form(...),
    nom: str = Form(""),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
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
    resp.set_cookie("session", auth.make_session_cookie(user.id), httponly=True, samesite="lax")
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
    resp.set_cookie("session", auth.make_session_cookie(user.id), httponly=True, samesite="lax")
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

    async with httpx.AsyncClient() as client:
        token_r = await client.post(GOOGLE_TOKEN_URL, data={
            "client_id":     client_id,
            "client_secret": client_secret,
            "code":          code,
            "redirect_uri":  redirect_uri,
            "grant_type":    "authorization_code",
        })
        if token_r.status_code != 200:
            return RedirectResponse("/connexion?error=google_token_invalid", status_code=303)

        access_token = token_r.json().get("access_token", "")
        user_r = await client.get(
            GOOGLE_USER_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        profile = user_r.json()

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
    resp.set_cookie("session", auth.make_session_cookie(user.id), httponly=True, samesite="lax")
    return resp


def _google_redirect_uri(request: Request) -> str:
    env_uri = os.getenv("GOOGLE_REDIRECT_URI", "")
    if env_uri:
        return env_uri
    # Reconstruit l'URI depuis la requête en forçant HTTPS si derrière un proxy.
    base = str(request.base_url).rstrip("/")
    if base.startswith("http://") and not base.startswith("http://localhost"):
        base = "https://" + base[7:]
    return base + "/auth/google/callback"


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
# Page abonnement / upgrade
# --------------------------------------------------------------------------
@app.get("/upgrade", response_class=HTMLResponse)
def upgrade_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    return templates.TemplateResponse("upgrade.html", {"request": request, "user": user})


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
    require_admin(request, db)
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404)
    target.is_premium = not target.is_premium
    db.commit()
    return RedirectResponse(url="/admin", status_code=303)


@app.delete("/admin/users/{user_id}")
def admin_delete_user(user_id: int, request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
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
    """Calcul de viabilité — sans authentification, calcul pur."""
    data = await request.json()
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


@app.get("/sante")
def health():
    return {"status": "ok", "service": "Athena Business Model"}
