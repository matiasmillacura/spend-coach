"""Versión LangChain del extractor — compárala lado a lado con extractor.py.

Misma tarea (texto informal chileno → gasto estructurado), pero la plomería de
"structured output" la hace LangChain:

  extractor.py (SDK anthropic a mano)      | extractor_lc.py (LangChain)
  -----------------------------------------|------------------------------------
  _TOOL dict con input_schema JSON escrito | class GastoExtraido(BaseModel) — el
  a mano                                   | esquema sale de los tipos de Python
  tool_choice={"type":"tool", ...} forzado | llm.with_structured_output(...)
  recorrer resp.content buscando el bloque | (hace exactamente ESO por debajo:
  tool_use y sacar block.input             | fuerza un tool y parsea el bloque)
  validar tipos/campos a mano              | Pydantic valida y tipa solo

Lo que NO cambia: el prompt (se reutiliza el de extractor.py), la jerga chilena
(_parsear_monto) y las reglas de negocio (fecha no futura, categoría válida).
Un framework reemplaza plomería, nunca tu lógica de dominio.
"""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

import anthropic
from langchain_anthropic import ChatAnthropic
from pydantic import BaseModel, Field

from config import config
from db import CATEGORIAS
# Reutilizamos del extractor original: mismo prompt y misma validación chilena,
# para que la comparación entre ambos enfoques sea 1 a 1.
from extractor import ExtractorError, _parsear_monto, _prompt


class GastoExtraido(BaseModel):
    """El "input_schema" del original, pero como modelo Pydantic.

    LangChain genera el JSON Schema desde esta clase y Pydantic valida la
    respuesta: si Claude devolviera un monto no entero o una categoría fuera
    del enum, falla aquí, no silenciosamente más adelante.
    """
    es_gasto: bool = Field(description="true si el mensaje describe un gasto real")
    monto: Optional[int] = Field(description="monto en CLP (enteros), o null si no se menciona")
    # Literal[tuple(...)] construye el enum en runtime desde db.CATEGORIAS
    # (equivale al "enum": CATEGORIAS del schema escrito a mano).
    categoria: Literal[tuple(CATEGORIAS)]  # type: ignore[valid-type]
    descripcion: str = Field(description="descripción corta del gasto")
    fecha: str = Field(description="fecha del gasto en formato YYYY-MM-DD")


# Cliente perezoso, igual que el original: se crea al primer uso.
_llm = None


def get_llm():
    """ChatAnthropic = el "chat model" de LangChain sobre la API de Claude.

    Es la interfaz común del framework: si mañana quisieras probar otro
    proveedor, cambias esta clase y el resto del código no se toca.
    """
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
    """Misma firma y mismo contrato que extractor.extraer_gasto.

    Compara: aquí NO hay _TOOL, ni tool_choice, ni bucle sobre resp.content.
    Todo eso lo hace with_structured_output.
    """
    hoy = hoy or date.today()

    # El corazón del cambio: pídele al LLM que responda CON esta estructura.
    # Por debajo, para Claude, LangChain hace lo mismo que extractor.py:
    # define un tool con el schema de GastoExtraido y fuerza tool_choice.
    structured_llm = get_llm().with_structured_output(GastoExtraido)

    try:
        gasto = structured_llm.invoke(_prompt(texto, hoy))  # → GastoExtraido, ya validado
    # langchain-anthropic usa el SDK anthropic por debajo, así que las
    # excepciones son las mismas y el manejo de errores no cambia.
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

    # Reglas de negocio idénticas al original (esto NUNCA lo hace el framework).
    d = {
        "es_gasto": True,
        "monto": _parsear_monto(gasto.monto),
        "categoria": gasto.categoria,          # Pydantic ya garantizó el enum
        "descripcion": (gasto.descripcion or texto).strip()[:200],
    }
    try:
        f = date.fromisoformat(gasto.fecha)
    except ValueError:
        f = hoy
    d["fecha"] = min(f, hoy).isoformat()       # nunca fecha futura
    return d
