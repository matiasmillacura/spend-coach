"""Autenticación: Google (OpenID Connect via Authlib) y correo+contraseña."""
from __future__ import annotations

import re
from datetime import date
from functools import wraps

from authlib.integrations.flask_client import OAuth
from flask import Blueprint, jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import db
from config import config

oauth = OAuth()
auth_bp = Blueprint("auth", __name__)


def init_auth(app) -> None:
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
    if config.demo_permitido():
        u = db.get_or_create_demo_user()
        session.clear()
        session["user_id"] = u["id"]
        return redirect("/")
    return ("El login no está disponible: falta configurar el acceso con Google "
            "(GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET)."), 503


@auth_bp.route("/auth/callback")
def callback():
    if not config.auth_habilitada():
        return redirect("/login")
    token = oauth.google.authorize_access_token()
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
    session.clear()
    session["user_id"] = u["id"]
    return redirect("/")


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


def validar_rut(rut: str) -> str | None:
    """Valida un RUT chileno (módulo 11); devuelve el RUT normalizado (12345678-5) o None."""
    limpio = re.sub(r"[^0-9kK]", "", rut or "")
    if not (2 <= len(limpio) <= 9):
        return None
    cuerpo, dv = limpio[:-1], limpio[-1].upper()
    if not cuerpo.isdigit():
        return None
    suma, factor = 0, 2
    for d in reversed(cuerpo):
        suma += int(d) * factor
        factor = 2 if factor == 7 else factor + 1
    resto = 11 - (suma % 11)
    dv_ok = "0" if resto == 11 else ("K" if resto == 10 else str(resto))
    return f"{cuerpo}-{dv_ok}" if dv == dv_ok else None


def _edad_valida(fecha_iso: str) -> date | None:
    try:
        nac = date.fromisoformat(fecha_iso)
    except (TypeError, ValueError):
        return None
    hoy = date.today()
    edad = hoy.year - nac.year - ((hoy.month, hoy.day) < (nac.month, nac.day))
    return nac if 5 <= edad <= 120 else None


@auth_bp.post("/auth/register")
def register():
    """Crea la cuenta manual y abre sesión. Errores como JSON amable, nunca 500."""
    d = request.get_json(silent=True) or {}
    email = (d.get("email") or "").strip().lower()
    password = d.get("password") or ""
    nombre_completo = (d.get("nombre_completo") or "").strip()
    apodo = (d.get("apodo") or "").strip() or nombre_completo.split(" ")[0]
    fecha = _edad_valida(d.get("fecha_nacimiento") or "")

    if not _EMAIL_RE.match(email):
        return jsonify({"ok": False, "error": "Ese correo no se ve válido."}), 400
    if len(password) < 8:
        return jsonify({"ok": False, "error": "La contraseña debe tener al menos 8 caracteres."}), 400
    if len(nombre_completo) < 3:
        return jsonify({"ok": False, "error": "Cuéntanos tu nombre completo."}), 400
    rut = validar_rut(d.get("rut") or "")
    if not rut:
        return jsonify({"ok": False, "error": "El RUT no es válido. Revisa el dígito verificador."}), 400
    if not fecha:
        return jsonify({"ok": False, "error": "Revisa tu fecha de nacimiento."}), 400

    try:
        u = db.crear_usuario_password(
            email=email, password_hash=generate_password_hash(password),
            apodo=apodo, nombre_completo=nombre_completo, rut=rut, fecha_nacimiento=fecha,
        )
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 409
    session.clear()
    session["user_id"] = u["id"]
    return jsonify({"ok": True, "user": db.get_perfil(u["id"])})


@auth_bp.post("/auth/login")
def login_password():
    d = request.get_json(silent=True) or {}
    cred = db.get_credenciales_por_email(d.get("email") or "")
    if not cred or not check_password_hash(cred["password_hash"], d.get("password") or ""):
        return jsonify({"ok": False, "error": "Correo o contraseña incorrectos."}), 401
    session.clear()
    session["user_id"] = cred["id"]
    return jsonify({"ok": True, "user": db.get_perfil(cred["id"])})


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect("/")


def current_user_id() -> int | None:
    return session.get("user_id")


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"ok": False, "error": "No autorizado."}), 401
        return fn(*args, **kwargs)
    return wrapper
