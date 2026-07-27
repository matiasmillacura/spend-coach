"""Convierte lenguaje natural en un gasto estructurado usando la API de Claude."""
from __future__ import annotations

import math
import re
from datetime import date, timedelta

import anthropic

from config import config
from db import CATEGORIAS


class ExtractorError(RuntimeError):
    """Problema al hablar con la API de Claude (sin clave, sin conexión, etc.)."""


_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    """Cliente Anthropic compartido (lo usan extractor y coach)."""
    global _client
    if not config.ANTHROPIC_API_KEY:
        raise ExtractorError(
            "Falta la clave de la API de Claude. Define ANTHROPIC_API_KEY en tu .env "
            "(la obtienes en https://console.anthropic.com)."
        )
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


_TOOL_NAME = "registrar_gasto"
_TOOL = {
    "name": _TOOL_NAME,
    "description": "Registra los datos estructurados de un gasto personal en Chile.",
    "input_schema": {
        "type": "object",
        "properties": {
            "es_gasto": {"type": "boolean", "description": "true si el mensaje describe un gasto real"},
            "monto": {"type": ["integer", "null"], "description": "monto en CLP (enteros), o null si no se menciona"},
            "categoria": {"type": "string", "enum": CATEGORIAS},
            "descripcion": {"type": "string", "description": "descripción corta del gasto"},
            "fecha": {"type": "string", "description": "fecha del gasto en formato YYYY-MM-DD"},
        },
        "required": ["es_gasto", "monto", "categoria", "descripcion", "fecha"],
    },
}


def _prompt(texto: str, hoy: date) -> str:
    ayer = hoy - timedelta(days=1)
    cats = ", ".join(CATEGORIAS)
    return f"""Eres un asistente que registra gastos personales en Chile.
Hoy es {hoy.isoformat()} ({hoy.strftime('%A')}). Ayer fue {ayer.isoformat()}.

El usuario escribe un gasto en español chileno informal. Extrae los datos.

Reglas de dinero (Chile), la moneda es peso chileno (CLP), enteros sin decimales:
- "luca" o "lucas" = miles. "12 lucas" = 12000. "una luca" = 1000. "media luca" = 500.
- "k" o "mil" también = miles. "5k" = 5000. "20 mil" = 20000.
- "gamba" = 100. "5 gambas" = 500.  "palo" = millón. "2 palos" = 2000000.
- Si no se menciona monto, deja "monto" en null.

Categoría: elige EXACTAMENTE una de: {cats}. Si dudas, usa "otros".

Fecha ("fecha", formato YYYY-MM-DD):
- Si no se menciona cuándo, usa hoy ({hoy.isoformat()}).
- "ayer" = {ayer.isoformat()}. Interpreta otras referencias relativas según hoy.
- Nunca uses una fecha futura.

"es_gasto": true si el mensaje describe un gasto real; false si es un saludo,
una pregunta o algo que no es un gasto.

Mensaje del usuario: "{texto}\""""


def extraer_gasto(texto: str, hoy: date | None = None) -> dict:
    """Devuelve un dict con es_gasto, monto, categoria, descripcion y fecha."""
    hoy = hoy or date.today()
    client = get_client()

    try:
        resp = client.messages.create(
            model=config.CLAUDE_MODEL_EXTRACTOR,
            max_tokens=400,
            system="Extraes gastos personales y devuelves los datos llamando a la herramienta registrar_gasto.",
            messages=[{"role": "user", "content": _prompt(texto, hoy)}],
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
        )
    except anthropic.AuthenticationError as e:
        raise ExtractorError("La clave de la API de Claude es inválida o fue revocada.") from e
    except anthropic.RateLimitError as e:
        raise ExtractorError("La API de Claude está saturada. Intenta de nuevo en unos segundos.") from e
    except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
        raise ExtractorError("No hay conexión con la API de Claude. Revisa tu internet.") from e
    except anthropic.APIError as e:
        raise ExtractorError(f"Error de la API de Claude: {e}") from e

    d = None
    for b in resp.content:
        if b.type == "tool_use" and b.name == _TOOL_NAME:
            d = dict(b.input)
            break
    if d is None:
        raise ValueError("Claude no devolvió el gasto estructurado.")

    if not d.get("es_gasto"):
        return {"es_gasto": False}

    monto = d.get("monto")
    if monto is None:
        raise ValueError("No detecté el monto del gasto. Inténtalo más explícito.")
    d["monto"] = _parsear_monto(monto)

    cat = str(d.get("categoria", "otros")).strip().lower()
    d["categoria"] = cat if cat in CATEGORIAS else "otros"

    d["descripcion"] = str(d.get("descripcion") or texto).strip()[:200]

    try:
        f = date.fromisoformat(str(d.get("fecha") or hoy.isoformat()))
    except ValueError:
        f = hoy
    if f > hoy:
        f = hoy
    d["fecha"] = f.isoformat()
    d["es_gasto"] = True
    return d


def _parsear_monto(monto) -> int:
    """Convierte el monto a entero CLP > 0, tolerando formato/jerga chilena."""
    if isinstance(monto, bool):
        raise ValueError("No entendí el monto del gasto.")

    if isinstance(monto, (int, float)):
        valor = float(monto)
    else:
        s = str(monto).strip().lower().replace("$", "")
        mult = 1
        if re.search(r"mill[oó]n|\bpalos?\b", s):
            mult = 1_000_000
        elif re.search(r"lucas?|\bmil\b|\d\s*k\b|\bk\b", s):
            mult = 1_000
        elif re.search(r"gambas?", s):
            mult = 100
        if mult != 1:
            s2 = s.replace(",", ".")
            m = re.search(r"\d+(?:\.\d+)?", s2)
            if m:
                base = float(m.group())
            elif "media" in s:
                base = 0.5
            else:
                raise ValueError(f"No entendí el monto: {monto!r}")
            valor = base * mult
        else:
            s2 = re.sub(r"[^0-9,.\-]", "", s).replace(".", "").replace(",", ".")
            if not re.search(r"\d", s2):
                raise ValueError(f"No entendí el monto: {monto!r}")
            valor = float(s2)

    if not math.isfinite(valor):
        raise ValueError("El monto no es un número válido.")
    entero = int(round(valor))
    if entero <= 0:
        raise ValueError("El monto debe ser mayor que 0 (¿fue una devolución o un ingreso?).")
    return entero
