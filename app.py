"""App web multiusuario del coach de gastos (Flask).

Reutiliza el core: db (SQLAlchemy), extractor y coach (API de Claude), dashboard.
La autenticación (Google OAuth) vive en auth.py. TODA ruta de datos exige sesión
y opera solo sobre los gastos del usuario logueado.

Ejecutar:
    local (desarrollo):   python app.py
    producción (WSGI):    gunicorn 'app:app'
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import logging

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

import coach_agent
import dashboard
import db
from auth import auth_bp, current_user_id, init_auth, login_required
from coach import coaching_mensual, resumen_semanal
from config import config
from extractor import ExtractorError

log = logging.getLogger(__name__)

# Tipos de imagen aceptados para la foto de boleta.
TIPOS_IMAGEN = {"image/jpeg", "image/png", "image/webp"}

WEB_DIR = Path(__file__).resolve().parent / "web"


def _static(nombre: str):
    resp = send_from_directory(WEB_DIR, nombre)
    # Sin caché: al iterar la UI, un refresco siempre trae lo último.
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


def create_app() -> Flask:
    # Fail-fast: en producción, config insegura => la app NO arranca.
    for aviso in config.validar():
        print(aviso)

    app = Flask(__name__, static_folder=None)
    app.config.update(
        SECRET_KEY=config.SECRET_KEY,
        SESSION_COOKIE_HTTPONLY=True,                 # la cookie no es legible por JS
        SESSION_COOKIE_SAMESITE="Lax",                # mitiga CSRF (no viaja en POST cross-site)
        SESSION_COOKIE_SECURE=config.SESSION_COOKIE_SECURE,  # Secure siempre en producción
        MAX_CONTENT_LENGTH=8 * 1024 * 1024,           # 8 MB: da espacio a la foto de boleta
        JSON_AS_ASCII=False,
    )

    # Detrás del proxy de la plataforma (Render, etc.): respeta X-Forwarded-Proto/Host
    # para que url_for(_external=True) genere https:// (crítico para el callback de Google).
    if config.IS_PRODUCTION or config.HTTPS:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    db.init_db()
    init_auth(app)
    app.register_blueprint(auth_bp)

    # --- estáticos (el "shell"; no exponen datos) ---
    @app.get("/")
    def index():
        return _static("index.html")

    @app.get("/app.js")
    def appjs():
        return _static("app.js")

    @app.get("/styles.css")
    def styles():
        return _static("styles.css")

    # PWA: manifest, service worker (scope raíz) e íconos.
    @app.get("/manifest.json")
    def manifest():
        return _static("manifest.json")

    @app.get("/sw.js")
    def sw():
        return _static("sw.js")

    @app.get("/icon-192.png")
    def icon192():
        return _static("icon-192.png")

    @app.get("/icon-512.png")
    def icon512():
        return _static("icon-512.png")

    # --- API ---
    @app.get("/api/me")
    def me():
        uid = current_user_id()
        estado = {"auth_google": config.auth_habilitada(), "demo": config.demo_permitido()}
        if not uid:
            return jsonify({"ok": False, **estado}), 401
        u = db.get_perfil(uid)
        if not u:                       # sesión apunta a un usuario borrado
            return jsonify({"ok": False, **estado}), 401
        return jsonify({"ok": True, "user": u, **estado})

    @app.get("/api/dashboard")
    @login_required
    def api_dashboard():
        # ?mes=YYYY-MM permite navegar meses anteriores; sin él, el mes en curso.
        mes_raw = (request.args.get("mes") or "").strip()
        anio = mes = None
        if mes_raw:
            try:
                anio, mes = int(mes_raw[:4]), int(mes_raw[5:7])
                assert mes_raw[4] == "-" and 1 <= mes <= 12 and 2000 <= anio <= 2100
            except (ValueError, AssertionError, IndexError):
                return jsonify({"ok": False, "error": "Parámetro mes inválido (YYYY-MM)."}), 400
        return jsonify(dashboard.construir(current_user_id(), anio=anio, mes=mes))

    @app.get("/api/gastos")
    @login_required
    def api_gastos():
        return jsonify(db.ultimos_gastos(current_user_id(), 50))

    @app.post("/api/perfil")
    @login_required
    def api_perfil():
        """Onboarding express: nombre (apodo) + fecha de nacimiento por formulario.
        Es más rápido y barato que pedirlos por chat."""
        data = request.get_json(silent=True) or {}
        nombre = (data.get("nombre") or "").strip()[:40]
        fecha_raw = (data.get("fecha_nacimiento") or "").strip()
        if not nombre:
            return jsonify({"ok": False, "error": "Dinos cómo te llamamos 🙂"}), 400
        try:
            nac = date.fromisoformat(fecha_raw)
        except ValueError:
            return jsonify({"ok": False, "error": "La fecha de nacimiento no es válida."}), 400
        hoy = date.today()
        edad = hoy.year - nac.year - ((hoy.month, hoy.day) < (nac.month, nac.day))
        if not (5 <= edad <= 120):
            return jsonify({"ok": False, "error": "Revisa la fecha de nacimiento."}), 400
        p = db.guardar_perfil(current_user_id(), nombre=nombre, fecha_nacimiento=nac)
        return jsonify({"ok": True, "user": db.get_perfil(current_user_id()) or p})

    def _maybe_resumen_semanal(uid: int) -> None:
        """Coach proactivo: una vez por semana ISO, al abrir el chat, deja un
        resumen semanal como mensaje del coach. Nunca rompe /api/historial."""
        try:
            p = db.get_perfil(uid)
            if not p or not p["onboarding_completo"]:
                return
            hoy = date.today()
            iso = hoy.isocalendar()
            semana = f"{iso[0]}-W{iso[1]:02d}"
            if db.get_ultima_semana_resumen(uid) == semana:
                return
            texto = resumen_semanal(uid)          # None si no hubo movimiento
            if texto:
                db.agregar_mensaje(uid, "assistant", texto)
            # Semana cubierta (con o sin mensaje): no insistir hasta la próxima.
            db.set_ultima_semana_resumen(uid, semana)
        except ExtractorError:
            pass                                   # sin clave/API caída: reintenta otra apertura
        except Exception:
            log.exception("Fallo generando el resumen semanal")

    @app.get("/api/historial")
    @login_required
    def api_historial():
        """Historial del chat para reconstruir la conversación al abrir la app."""
        uid = current_user_id()
        _maybe_resumen_semanal(uid)
        return jsonify({"ok": True, "mensajes": db.historial_mensajes(uid, 40)})

    @app.post("/api/chat")
    @login_required
    def api_chat():
        """Un turno de conversación con el coach: texto y/o foto de boleta."""
        data = request.get_json(silent=True) or {}
        texto = (data.get("texto") or "").strip()
        imagen = data.get("imagen") or None            # base64 sin prefijo data:
        imagen_tipo = (data.get("imagen_tipo") or "image/jpeg").lower()
        if imagen:
            if imagen_tipo not in TIPOS_IMAGEN:
                return jsonify({"ok": False, "error": "Formato de imagen no soportado."}), 400
            if len(imagen) > 6_000_000:                # ~4.5 MB reales
                return jsonify({"ok": False, "error": "La foto es muy pesada. Intenta de nuevo."}), 400
        if not texto and not imagen:
            return jsonify({"ok": False, "error": "Mensaje vacío."}), 400
        try:
            reply = coach_agent.responder(current_user_id(), texto,
                                          imagen_b64=imagen, imagen_tipo=imagen_tipo)
        except ExtractorError as e:
            return jsonify({"ok": False, "error": str(e)}), 503
        return jsonify({"ok": True, "reply": reply})

    @app.delete("/api/gasto/<int:gasto_id>")
    @login_required
    def api_borrar_gasto(gasto_id: int):
        ok = db.borrar_gasto(current_user_id(), gasto_id)
        return jsonify({"ok": ok}), (200 if ok else 404)

    @app.get("/api/coaching")
    @login_required
    def api_coaching():
        hoy = date.today()
        texto = coaching_mensual(current_user_id(), hoy.year, hoy.month)
        return jsonify({"ok": True, "texto": texto})

    return app


app = create_app()


def main() -> None:
    modo = "Google OAuth" if config.auth_habilitada() else "DEMO (sin login, usuario local)"
    print("💰 Coach de gastos — app web multiusuario")
    print(f"   Auth: {modo}")
    print(f"   http://{config.HOST}:{config.PORT}/")
    print("   Ctrl+C para detener.")
    app.run(host=config.HOST, port=config.PORT, debug=False)


if __name__ == "__main__":
    main()
