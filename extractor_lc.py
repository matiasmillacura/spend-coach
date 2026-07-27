"""Versión LangChain del extractor; equivalente a extractor.py."""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

import anthropic
from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel, Field

from config import config
from db import CATEGORIAS
from extractor import ExtractorError, _parsear_monto, _prompt


class GastoExtraido(BaseModel):
    """Esquema Pydantic del gasto extraído."""
    es_gasto: bool = Field(description="true si el mensaje describe un gasto real")
    monto: Optional[int] = Field(description="monto en CLP (enteros), o null si no se menciona")
    categoria: Literal[tuple(CATEGORIAS)]  # type: ignore[valid-type]
    descripcion: str = Field(description="descripción corta del gasto")
    fecha: str = Field(description="fecha del gasto en formato YYYY-MM-DD")


_llm = None


def get_llm():
    """Chat model de LangChain sobre la API de Claude."""
    global _llm
    if not config.ANTHROPIC_API_KEY:
        raise ExtractorError(
            "Falta la clave de la API de Claude. Define ANTHROPIC_API_KEY en tu .env "
            "(la obtienes en https://console.anthropic.com)."
        )
    if _llm is None:
        _llm = ChatAnthropic(
            model=config.CLAUDE_MODEL_EXTRACTOR,
            max_tokens=400,
            api_key=config.ANTHROPIC_API_KEY,
        )
    return _llm


def extraer_gasto(texto: str, hoy: date | None = None) -> dict:
    """Misma firma y contrato que extractor.extraer_gasto."""
    hoy = hoy or date.today()

    structured_llm = get_llm().with_structured_output(GastoExtraido)

    try:
        gasto = structured_llm.invoke(_prompt(texto, hoy))
    except anthropic.AuthenticationError as e:
        raise ExtractorError("La clave de la API de Claude es inválida o fue revocada.") from e
    except anthropic.RateLimitError as e:
        raise ExtractorError("La API de Claude está saturada. Intenta de nuevo en unos segundos.") from e
    except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
        raise ExtractorError("No hay conexión con la API de Claude. Revisa tu internet.") from e
    except anthropic.APIError as e:
        raise ExtractorError(f"Error de la API de Claude: {e}") from e

    if not gasto.es_gasto:
        return {"es_gasto": False}

    if gasto.monto is None:
        raise ValueError("No detecté el monto del gasto. Inténtalo más explícito.")

    d = {
        "es_gasto": True,
        "monto": _parsear_monto(gasto.monto),
        "categoria": gasto.categoria,
        "descripcion": (gasto.descripcion or texto).strip()[:200],
    }
    try:
        f = date.fromisoformat(gasto.fecha)
    except ValueError:
        f = hoy
    d["fecha"] = min(f, hoy).isoformat()
    return d
