"""
Athena Business Model — application FastAPI.

Un outil web d'analyse de viabilité économique, pensé pour les femmes
entrepreneuses. Chaque utilisatrice crée un compte, construit un ou plusieurs
modèles économiques et visualise leur viabilité (coûts fixes, offres,
marge, seuil de rentabilité, compte de résultat).
"""
import json
import os
from pathlib import Path

from fastapi import FastAPI, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import auth
from app.database import get_db, init_db
from app.engine import calculer
from app.models import User, BusinessModel

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Athena Business Model")

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.on_event("startup")
def _startup():
    init_db()


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


# --------------------------------------------------------------------------
# Pages
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
    user = User(email=email, nom=nom.strip(), password_hash=auth.hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    resp = RedirectResponse(url="/tableau-de-bord", status_code=303)
    resp.set_cookie("session", auth.make_session_cookie(user.id), httponly=True, samesite="lax")
    return resp


@app.get("/connexion", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@app.post("/connexion")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if not user or not auth.verify_password(password, user.password_hash):
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
# API
# --------------------------------------------------------------------------
@app.post("/api/calculer")
async def api_calculer(request: Request):
    """Calcul de viabilité — sans authentification, calcul pur."""
    data = await request.json()
    try:
        return JSONResponse(calculer(data))
    except Exception as e:  # calcul défensif
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
