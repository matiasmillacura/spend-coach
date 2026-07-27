"""Resumen mensual + observaciones de hábitos (el lado 'coach' del proyecto)."""
from __future__ import annotations

import logging
from datetime import date

import anthropic

from config import config
from db import resumen_mes
from extractor import ExtractorError, get_client

log = logging.getLogger(__name__)


def formato_clp(monto: int) -> str:
    """12345 -> '$12.345' (formato chileno con punto de miles)."""
    return "$" + f"{monto:,}".replace(",", ".")


def resumen_texto(user_id: int, anio: int, mes: int) -> str:
    """Resumen tabular del mes, sin LLM (siempre disponible)."""
    r = resumen_mes(user_id, anio, mes)
    if r["total"] == 0:
        return f"No hay gastos registrados en {r['mes']}."
    lineas = [f"📅 Resumen {r['mes']}", f"Total: {formato_clp(r['total'])}", ""]
    for cat, datos in r["por_categoria"].items():
        pct = round(datos["total"] / r["total"] * 100)
        lineas.append(
            f"  {cat:<16} {formato_clp(datos['total']):>12}  ({pct}% · {datos['n']} gastos)"
        )
    return "\n".join(lineas)


def _comentario_llm(user_id: int, anio: int, mes: int) -> str:
    from db import get_regla, ingreso_mensual_total, listar_metas, total_ahorro_mes

    r = resumen_mes(user_id, anio, mes)
    ingreso = ingreso_mensual_total(user_id, anio, mes)
    if r["total"] == 0 and ingreso == 0:
        return ""
    desglose = "\n".join(
        f"- {cat}: {formato_clp(d['total'])} ({d['n']} gastos)"
        for cat, d in r["por_categoria"].items()
    ) or "- (sin gastos este mes)"
    balance = ingreso - r["total"]
    ahorro = total_ahorro_mes(user_id, anio, mes)
    regla = get_regla(user_id)
    metas = listar_metas(user_id)
    metas_txt = "; ".join(m["nombre"] for m in metas) or "ninguna aún"

    prompt = f"""Eres un coach de finanzas personales chileno: cercano, directo y que busca
mejorar la calidad de vida (no solo describir números). Mes {r['mes']} del usuario:

Ingreso mensual: {formato_clp(ingreso)}
Gasto total: {formato_clp(r['total'])}
Balance (lo que queda): {formato_clp(balance)}
Ahorro registrado este mes: {formato_clp(ahorro)}
Regla objetivo: {regla['pct_necesidades']}% necesidades / {regla['pct_deseos']}% deseos / {regla['pct_ahorro']}% ahorro
Metas de ahorro: {metas_txt}

Gastos por categoría:
{desglose}

Dale una devolución breve (máximo 4 frases): compara ingreso vs gasto, comenta si va
holgado o apretado / en déficit, y da UN consejo concreto y accionable orientado a que
ahorre y viva mejor. Nada de relleno ni listas largas. Tono amable, no culpabilizador."""

    client = get_client()
    resp = client.messages.create(
        model=config.CLAUDE_MODEL_COACH,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def resumen_semanal(user_id: int) -> str | None:
    """Resumen proactivo de la semana; None si no hubo movimiento."""
    from datetime import timedelta

    from db import gastos_rango, total_ahorro_rango

    hoy = date.today()
    ini_sem = hoy - timedelta(days=6)
    fin_prev = ini_sem - timedelta(days=1)
    ini_prev = fin_prev - timedelta(days=6)

    sem = gastos_rango(user_id, ini_sem, hoy)
    prev = gastos_rango(user_id, ini_prev, fin_prev)
    ahorro_sem = total_ahorro_rango(user_id, ini_sem, hoy)
    if sem["total"] == 0 and ahorro_sem == 0:
        return None

    top = sorted(sem["por_categoria"].items(), key=lambda kv: -kv[1])[:3]
    top_txt = ", ".join(f"{c}: {formato_clp(m)}" for c, m in top) or "sin gastos"

    prompt = f"""Eres un coach de finanzas personales chileno, cálido y breve.
Redacta el RESUMEN SEMANAL para tu usuario con estos datos reales:

- Gastado esta semana (últimos 7 días): {formato_clp(sem['total'])}
- Gastado la semana anterior: {formato_clp(prev['total'])}
- Top categorías de la semana: {top_txt}
- Ahorro registrado esta semana: {formato_clp(ahorro_sem)}

Máximo 3 frases: cómo estuvo la semana vs la anterior, dónde se fue la plata y UN
consejo accionable para la semana que empieza. Tono cercano, sin listas, sin saludo."""

    client = get_client()
    resp = client.messages.create(
        model=config.CLAUDE_MODEL_COACH,
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    texto = "".join(b.text for b in resp.content if b.type == "text").strip()
    return f"📬 Tu semana: {texto}" if texto else None


def coaching_mensual(user_id: int, anio: int, mes: int) -> str:
    """Resumen + comentario del coach. Degrada con gracia si la API no responde."""
    base = resumen_texto(user_id, anio, mes)
    try:
        comentario = _comentario_llm(user_id, anio, mes)
    except (ExtractorError, anthropic.APIError):
        comentario = ""
    except Exception:
        log.exception("Fallo inesperado generando el comentario del coach")
        comentario = ""
    if comentario:
        return f"{base}\n\n💬 {comentario}"
    return base
