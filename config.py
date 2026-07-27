"""Configuración central de la app, leída desde variables de entorno."""
from __future__ import annotations

import os
import secrets

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _normalizar_db_url(url: str) -> str:
    """Normaliza la URL de la BD al prefijo 'postgresql+psycopg://' que exige SQLAlchemy."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


class ConfigError(RuntimeError):
    """Configuración inválida o insegura: la app no debe arrancar así."""


class Config:
    DATABASE_URL = _normalizar_db_url(os.environ.get("DATABASE_URL", "sqlite:///gastos.db"))
    _ES_SQLITE = DATABASE_URL.startswith("sqlite")

    CHECKPOINT_DB = os.environ.get("COACH_CHECKPOINT_DB", "checkpoints.db")

    HOST = os.environ.get("COACH_HOST", "127.0.0.1")
    PORT = int(os.environ.get("COACH_PORT", "8000"))
    HTTPS = os.environ.get("COACH_HTTPS", "0") == "1"

    _INDICIO_PROD = (not _ES_SQLITE) or HTTPS
    ENV = os.environ.get("COACH_ENV") or ("production" if _INDICIO_PROD else "development")
    IS_PRODUCTION = ENV == "production"

    _SECRET_ENV = os.environ.get("COACH_SECRET_KEY", "").strip()
    SECRET_KEY = _SECRET_ENV or (None if IS_PRODUCTION else secrets.token_hex(32))
    SESSION_COOKIE_SECURE = HTTPS or IS_PRODUCTION

    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL_EXTRACTOR = os.environ.get("CLAUDE_MODEL_EXTRACTOR", "claude-haiku-4-5")
    CLAUDE_MODEL_COACH = os.environ.get("CLAUDE_MODEL_COACH", "claude-opus-4-8")
    CLAUDE_MODEL_CHAT = os.environ.get("CLAUDE_MODEL_CHAT", "claude-sonnet-4-6")

    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

    _DEMO_ENV = os.environ.get("COACH_ALLOW_DEMO")
    ALLOW_DEMO = (_DEMO_ENV == "1") if _DEMO_ENV is not None else (not IS_PRODUCTION)

    @staticmethod
    def _es_real(valor: str) -> bool:
        """True si el valor no está vacío ni es un placeholder del .env.example."""
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
        """Valida la config al arrancar; lanza ConfigError si algo es inseguro en producción."""
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
