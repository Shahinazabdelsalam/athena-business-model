"""
Authentification : hachage de mots de passe et sessions signées par cookie.

Volontairement simple et sans dépendance lourde : hachage PBKDF2 (hashlib) et
cookies signés (itsdangerous). Aucun mot de passe n'est stocké en clair.
"""
import hashlib
import hmac
import os
import secrets

from fastapi import Request
from itsdangerous import URLSafeSerializer, BadSignature

SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me-in-production")
_serializer = URLSafeSerializer(SECRET_KEY, salt="athena-session")

_PBKDF2_ROUNDS = 260_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ROUNDS)
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, hexhash = stored.split("$", 1)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ROUNDS)
    return hmac.compare_digest(dk.hex(), hexhash)


def make_session_cookie(user_id: int) -> str:
    return _serializer.dumps({"uid": user_id})


def read_session_cookie(token: str):
    try:
        data = _serializer.loads(token)
        return data.get("uid")
    except BadSignature:
        return None


def current_user_id(request: Request):
    token = request.cookies.get("session")
    if not token:
        return None
    return read_session_cookie(token)
