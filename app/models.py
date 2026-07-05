"""Modèles ORM : utilisatrices et modèles économiques sauvegardés."""
from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    nom = Column(String(255), nullable=True)
    password_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    models = relationship(
        "BusinessModel", back_populates="owner", cascade="all, delete-orphan"
    )


class BusinessModel(Base):
    __tablename__ = "business_models"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    nom = Column(String(255), nullable=False, default="Mon modèle")
    # Données complètes du modèle (paramètres, coûts, offres) en JSON.
    data = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    owner = relationship("User", back_populates="models")
