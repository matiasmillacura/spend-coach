"""Configuración central de la app, leída desde variables de entorno.

En desarrollo local, las variables se cargan desde un archivo .env (via
python-dotenv). En producción se definen en el entorno del servidor / secretos
de la plataforma. NADA de secretos vive en el código.

Seguridad (fail-fast): en producción la app se NIEGA a arrancar si falta la clave
de sesión, si el login no está configurado, etc. Nunca arranca "en modo abierto".
"""
from __future__ import annotations

import os
import secrets

try:
    from dotenv import load_dotenv
    load_dotenv()  # carga .env si existe; en prod simplemente no hace nada
except ImportError:  # dotenv es opcional en producción
    pass


def _normalizar_db_url(url: str) -> str:
    """SQLAlchemy exige el prefijo 'postgresql+psycopg://'. Muchas plataformas
    entregan 'postgres://' o 'postgresql://' — lo normalizamos a psycopg v3."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


class ConfigError(RuntimeError):
    """Configuración inválida o insegura: la app no debe arrancar así."""


class Config:
    # --- Base de datos ---
    DATABASE_URL = _normalizar_db_url(os.environ.get("DATABASE_URL", "sqlite:///gastos.db"))
    _ES_SQLITE = DATABASE_URL.startswith("sqlite")

    # --- Servidor ---
    HOST = os.environ.get("COACH_HOST", "127.0.0.1")
    PORT = int(os.environ.get("COACH_PORT", "8000"))
    HTTPS = os.environ.get("COACH_HTTPS", "0") == "1"

    # --- Entorno: producción o desarrollo ---
    # Si no se declara COACH_ENV, se infiere: cualquier indicio de producción
    # (Postgres o HTTPS) => production; si no, development.
    _INDICIO_PROD = (not _ES_SQLITE) or HTTPS
    ENV = os.environ.get("COACH_ENV") or ("production" if _INDICIO_PROD else "development")
    IS_PRODUCTION = ENV == "production"

    # --- Sesión ---
    # SIN default usable: en dev se genera una clave efímera (la sesión se reinicia
    # al reiniciar el proceso, aceptable en local); en prod DEBE venir del entorno.
    _SECRET_ENV = os.environ.get("COACH_SECRET_KEY", "").strip()
    SECRET_KEY = _SECRET_ENV or (None if IS_PRODUCTION else secrets.token_hex(32))
    # Cookie Secure: siempre en producción; en dev depende de si sirves por HTTPS.
    SESSION_COOKIE_SECURE = HTTPS or IS_PRODUCTION

    # --- API de Claude ---
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL_EXTRACTOR = os.environ.get("CLAUDE_MODEL_EXTRACTOR", "claude-haiku-4-5")
    CLAUDE_MODEL_COACH = os.environ.get("CLAUDE_MODEL_COACH", "claude-opus-4-8")
    # Modelo del chat de coaching (conversación multi-turno con herramientas):
    # Sonnet equilibra calidad y costo; puedes subirlo a claude-opus-4-8.
    CLAUDE_MODEL_CHAT = os.environ.get("CLAUDE_MODEL_CHAT", "claude-sonnet-4-6")

    # --- Google OAuth ---
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

    # --- Modo demo (login local sin OAuth) ---
    # Opt-in explícito; por defecto solo se permite fuera de producción.
    _DEMO_ENV = os.environ.get("COACH_ALLOW_DEMO")
    ALLOW_DEMO = (_DEMO_ENV == "1") if _DEMO_ENV is not None else (not IS_PRODUCTION)

    @staticmethod
    def _es_real(valor: str) -> bool:
        """Un valor cuenta como configurado si no está vacío ni es un placeholder
        del .env.example (evita que 'xxxxxxxx' pase por credencial válida)."""
        v = (valor or "").strip()
        return bool(v) and not v.startswith("xxxxxxxx") and not v.startswith("pon-aqui")

    @classmethod
    def auth_habilitada(cls) -> bool:
        """True si hay credenciales de Google REALES configuradas (login real)."""
        return cls._es_real(cls.GOOGLE_CLIENT_ID) and cls._es_real(cls.GOOGLE_CLIENT_SECRET)

    @classmethod
    def demo_permitido(cls) -> bool:
        """True si /login puede abrir una sesión demo local."""
        return cls.ALLOW_DEMO and not cls.IS_PRODUCTION

    @classmethod
    def validar(cls) -> list[str]:
        """Valida la config al arrancar. Lanza ConfigError en producción si algo
        es inseguro; devuelve una lista de advertencias no fatales."""
        avisos: list[str] = []
        if cls.IS_PRODUCTION:
            if not cls.SECRET_KEY or len(cls.SECRET_KEY) < 32:
                raise ConfigError(
                    "COACH_SECRET_KEY es obligatoria en producción y debe tener al menos "
                    "32 caracteres. Genérala con: python3 -c \"import secrets; print(secrets.token_hex(32))\""
                )
            if not cls.auth_habilitada() and not (cls._DEMO_ENV == "1"):
                raise ConfigError(
                    "En producción debes configurar el login de Google "
                    "(GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET). El modo demo no se "
                    "habilita solo; si de verdad lo quieres, pon COACH_ALLOW_DEMO=1 (no recomendado)."
                )
            if cls._ES_SQLITE:
                avisos.append(
                    "⚠️  Estás en producción con SQLite. Para multiusuario real usa "
                    "PostgreSQL (define DATABASE_URL=postgresql://...)."
                )
        else:
            if not cls.auth_habilitada() and not cls.demo_permitido():
                avisos.append(
                    "Sin Google OAuth y sin modo demo: nadie puede iniciar sesión. "
                    "Configura Google o pon COACH_ALLOW_DEMO=1."
                )
        return avisos


config = Config()
