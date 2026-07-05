"""
Configuration de la base de données (SQLAlchemy).

Utilise DATABASE_URL (Postgres sur Railway). En développement local, bascule
automatiquement sur SQLite si aucune URL Postgres n'est fournie.
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./athena.db")

# Railway/Heroku fournissent parfois "postgres://" ; SQLAlchemy attend
# "postgresql://". On corrige au vol.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Dépendance FastAPI : fournit une session et la ferme proprement."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Crée les tables si elles n'existent pas."""
    from app import models  # noqa: F401  (enregistre les modèles)
    Base.metadata.create_all(bind=engine)
