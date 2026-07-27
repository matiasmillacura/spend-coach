"""Autenticación con Google (OpenID Connect) usando Authlib.

Flujo:
  /login          → redirige a Google (o login demo si no hay credenciales)
  /auth/callback  → Google vuelve aquí; creamos/actualizamos el usuario y abrimos sesión
  /logout         → cierra la sesión

La sesión guarda solo `user_id` en una cookie firmada por Flask. El decorador
`login_required` protege las rutas de API devolviendo 401 JSON si no hay sesión.

Modo demo: si no hay GOOGLE_CLIENT_ID/SECRET configurados, /login entra como un
usuario local único. Sirve para desarrollo sin montar OAuth.
"""
from __future__ import annotations

from functools import wraps

from authlib.integrations.flask_client import OAuth
from flask import Blueprint, jsonify, redirect, session, url_for

import db
from config import config

oauth = OAuth()
auth_bp = Blueprint("auth", __name__)


def init_auth(app) -> None:
    """Registra Authlib y el proveedor Google en la app (llamar al crear la app)."""
    oauth.init_app(app)
    if config.auth_habilitada():
        oauth.register(
            name="google",
            client_id=config.GOOGLE_CLIENT_ID,
            client_secret=config.GOOGLE_CLIENT_SECRET,
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )


@auth_bp.route("/login")
def login():
    if config.auth_habilitada():
        redirect_uri = url_for("auth.callback", _external=True)
        return oauth.google.authorize_redirect(redirect_uri)
    # Modo demo: SOLO si está explícitamente permitido (nunca en producción).
    if config.demo_permitido():
        u = db.get_or_create_demo_user()
        session.clear()                 # sesión nueva (evita fijación de sesión)
        session["user_id"] = u["id"]
        return redirect("/")
    return ("El login no está disponible: falta configurar el acceso con Google "
            "(GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET)."), 503


@auth_bp.route("/auth/callback")
def callback():
    if not config.auth_habilitada():
        return redirect("/login")
    token = oauth.google.authorize_access_token()  # valida state + intercambia el código
    info = token.get("userinfo") or {}
    sub = info.get("sub")
    if not sub:
        return "No se pudo autenticar con Google.", 400
    u = db.get_or_create_user(
        google_sub=sub,
        email=info.get("email", ""),
        nombre=info.get("name", ""),
        foto_url=info.get("picture"),
    )
    session.clear()               # evita fijación de sesión: sesión nueva tras login
    session["user_id"] = u["id"]
    return redirect("/")


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect("/")


def current_user_id() -> int | None:
    return session.get("user_id")


def login_required(fn):
    """Protege una ruta de API: 401 JSON si no hay sesión válida."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"ok": False, "error": "No autorizado."}), 401
        return fn(*args, **kwargs)
    return wrapper
