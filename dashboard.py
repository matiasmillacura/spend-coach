"""Arma el payload del dashboard con métricas de alto valor.

Todo se calcula con SQL (determinístico) — no usa el LLM. Incluye el análisis
financiero completo: ingresos vs gastos, balance, tasa de ahorro, disponible,
alerta de déficit, metas con proyección y la regla 50/30/20 configurable.
"""
from __future__ import annotations

import calendar
import math
from datetime import date

from db import (
    GRUPO_CATEGORIA,
    gastos_del_mes,
    gastos_rango,
    get_perfil,
    get_regla,
    ingreso_mensual_total,
    listar_gastos_fijos,
    listar_metas,
    listar_presupuestos,
    mayor_gasto,
    resumen_mes,
    serie_diaria,
    total_ahorrado_meta,
    total_ahorrado_meta_mes,
    total_ahorro_mes,
    total_ingresos_variables_rango,
)

# No proyectar el gasto de fin de mes antes de tener al menos estos días de datos:
# con muy pocos días la extrapolación lineal es pura especulación.
MIN_DIAS_PROYECCION = 4

# Paleta por categoría (coincide con el frontend).
COLORES = {
    "comida": "#f97316",
    "supermercado": "#22c55e",
    "transporte": "#3b82f6",
    "servicios": "#a855f7",
    "salud": "#ef4444",
    "entretenimiento": "#ec4899",
    "hogar": "#14b8a6",
    "ropa": "#eab308",
    "educacion": "#6366f1",
    "otros": "#94a3b8",
}


def _mes_anterior(anio: int, mes: int) -> tuple[int, int]:
    return (anio - 1, 12) if mes == 1 else (anio, mes - 1)


def _proyeccion(montos: list[int], total: int, dia_actual: int, dias_mes: int) -> int | None:
    """Estima el gasto de fin de mes de forma robusta.

    - Devuelve None los primeros días (poca señal para extrapolar).
    - No extrapola gastos atípicos/únicos (p. ej. arriendo): se cuentan una vez,
      y solo el gasto "variable" se proyecta sobre los días que faltan.
    """
    if dia_actual < MIN_DIAS_PROYECCION:
        return None
    if not montos or total <= 0:
        return 0
    montos_ord = sorted(montos)
    mediana = montos_ord[len(montos_ord) // 2]
    # Atípico: individualmente supera 5x la mediana y además $100.000.
    umbral = max(mediana * 5, 100_000)
    grandes = sum(m for m in montos if m > umbral)   # ya pagados; no se repiten
    variable = max(0, total - grandes)
    tasa_variable = variable / dia_actual
    dias_restantes = dias_mes - dia_actual
    return round(total + tasa_variable * dias_restantes)


def _asignar_porcentajes(categorias: list[dict], total: int) -> None:
    """Asigna 'pct' entero a cada categoría garantizando que sumen 100
    (método del resto mayor), evitando el clásico 33+33+33=99."""
    if total <= 0 or not categorias:
        for c in categorias:
            c["pct"] = 0
        return
    exactos = [c["total"] / total * 100 for c in categorias]
    pisos = [math.floor(x) for x in exactos]
    resto = 100 - sum(pisos)
    # Reparte el resto a las categorías con mayor parte fraccionaria.
    orden = sorted(range(len(categorias)), key=lambda i: exactos[i] - pisos[i], reverse=True)
    for i in orden[:resto]:
        pisos[i] += 1
    for c, p in zip(categorias, pisos):
        c["pct"] = p


def _meses_entre(desde: date, hasta: date) -> int:
    """Meses (redondeados hacia arriba, mínimo 1) entre dos fechas — para cuotas."""
    if hasta <= desde:
        return 1
    meses = (hasta.year - desde.year) * 12 + (hasta.month - desde.month)
    if hasta.day > desde.day:
        meses += 1
    return max(1, meses)


def _analisis_meta(meta: dict, ahorrado: int, aporte_mes: int, ingreso: int, hoy: date) -> dict:
    """Progreso y cuota sugerida de una meta, según su tipo.
    - monto_fecha: progreso acumulado vs objetivo + cuota para llegar a tiempo (marca 'vencida').
    - monto_mensual / porcentaje: progreso del MES en curso vs el objetivo mensual."""
    out = {**meta, "ahorrado": ahorrado, "objetivo": None, "progreso_pct": None,
           "cuota_sugerida": None, "meses_restantes": None, "nota": None}

    if meta["tipo"] == "monto_fecha" and meta.get("monto_objetivo"):
        objetivo = meta["monto_objetivo"]
        out["objetivo"] = objetivo
        out["progreso_pct"] = min(100, round(ahorrado / objetivo * 100)) if objetivo else None
        falta = max(0, objetivo - ahorrado)
        if falta == 0:
            out["nota"] = "lograda"
            out["meses_restantes"] = 0
            out["cuota_sugerida"] = 0
        elif meta.get("fecha_objetivo"):
            try:
                fobj = date.fromisoformat(meta["fecha_objetivo"])
            except ValueError:
                fobj = None
            if fobj and fobj <= hoy:
                out["nota"] = "vencida"           # plazo cumplido y aún falta
                out["meses_restantes"] = 0
                out["cuota_sugerida"] = falta      # habría que aportarlo ya
            elif fobj:
                meses = _meses_entre(hoy, fobj)
                out["meses_restantes"] = meses
                out["cuota_sugerida"] = round(falta / meses)

    elif meta["tipo"] == "monto_mensual" and meta.get("monto_mensual"):
        objetivo = meta["monto_mensual"]
        out["objetivo"] = objetivo               # objetivo mensual
        out["cuota_sugerida"] = objetivo
        out["progreso_pct"] = min(100, round(aporte_mes / objetivo * 100)) if objetivo else None

    elif meta["tipo"] == "porcentaje" and meta.get("porcentaje"):
        objetivo = round(ingreso * meta["porcentaje"] / 100)
        out["objetivo"] = objetivo               # objetivo mensual derivado del ingreso
        out["cuota_sugerida"] = objetivo
        out["progreso_pct"] = min(100, round(aporte_mes / objetivo * 100)) if objetivo else None

    return out


def _finanzas(user_id: int, anio: int, mes: int, gasto_total: int,
              por_categoria: dict, hoy: date, fijos: list[dict] | None = None) -> dict:
    """Ingresos vs gastos, balance, tasa de ahorro, disponible, déficit, regla,
    metas, suscripciones (gastos fijos) y presupuestos por categoría."""
    fijos = fijos or []
    ingreso = ingreso_mensual_total(user_id, anio, mes)
    ahorro_reg = total_ahorro_mes(user_id, anio, mes)
    balance = ingreso - gasto_total

    # Gasto por grupo 50/30/20 (necesidades/deseos).
    grupos = {"necesidades": 0, "deseos": 0}
    for cat, d in por_categoria.items():
        grupos[GRUPO_CATEGORIA.get(cat, "deseos")] += d["total"]

    regla = get_regla(user_id)
    # Reparto real como % del ingreso (si hay ingreso).
    def pct(x):
        return round(x / ingreso * 100) if ingreso > 0 else None
    # En la regla 50/30/20, el "ahorro" es lo que queda tras necesidades y deseos
    # (= balance). Así los tres tramos suman 100% del ingreso y coinciden con la
    # tasa de ahorro. Los aportes explícitos a metas se muestran aparte (metas).
    reparto = {
        "necesidades": {"monto": grupos["necesidades"], "pct": pct(grupos["necesidades"]), "objetivo_pct": regla["pct_necesidades"]},
        "deseos":      {"monto": grupos["deseos"],      "pct": pct(grupos["deseos"]),      "objetivo_pct": regla["pct_deseos"]},
        "ahorro":      {"monto": balance,               "pct": pct(balance),               "objetivo_pct": regla["pct_ahorro"]},
    }

    tasa_ahorro = round(balance / ingreso * 100) if ingreso > 0 else None
    objetivo_ahorro_mes = round(ingreso * regla["pct_ahorro"] / 100) if ingreso > 0 else 0
    # Disponible para gastar sin comerte la meta de ahorro (según la regla).
    disponible = ingreso - objetivo_ahorro_mes - gasto_total if ingreso > 0 else None

    # MODO ARRANQUE: quien llega a mitad de mes declara cuánta plata tiene
    # disponible ese día (saldo inicial) en vez de reconstruir el mes hacia
    # atrás. Durante ESE mes, el balance parte del saldo declarado:
    #   balance = saldo + ingresos variables posteriores − gastos posteriores.
    # La tasa de ahorro no se calcula (sería engañosa con medio mes fantasma).
    modo_arranque = False
    p = get_perfil(user_id) or {}
    if p.get("saldo_inicial") is not None and p.get("saldo_inicial_fecha"):
        f_saldo = date.fromisoformat(p["saldo_inicial_fecha"])
        if (f_saldo.year, f_saldo.month) == (anio, mes):
            modo_arranque = True
            fin_mes = date(anio, mes, calendar.monthrange(anio, mes)[1])
            gastado_desde = gastos_rango(user_id, f_saldo, fin_mes)["total"]
            ingresado_desde = total_ingresos_variables_rango(user_id, f_saldo, fin_mes)
            balance = p["saldo_inicial"] + ingresado_desde - gastado_desde
            tasa_ahorro = None
            disponible = None

    metas = listar_metas(user_id)
    metas_an = [
        _analisis_meta(m, total_ahorrado_meta(user_id, m["id"]),
                       total_ahorrado_meta_mes(user_id, m["id"], anio, mes), ingreso, hoy)
        for m in metas
    ]

    # Presupuestos por categoría: tope vs gastado (incluye gastos fijos de esa categoría).
    presupuestos = []
    for p in listar_presupuestos(user_id):
        gastado = por_categoria.get(p["categoria"], {}).get("total", 0)
        presupuestos.append({
            "categoria": p["categoria"],
            "monto": p["monto"],
            "gastado": gastado,
            "pct": min(999, round(gastado / p["monto"] * 100)) if p["monto"] else None,
        })
    presupuestos.sort(key=lambda x: -(x["pct"] or 0))

    return {
        "ingreso_mensual": ingreso,
        "gasto_mensual": gasto_total,
        "balance": balance,
        "tasa_ahorro_pct": tasa_ahorro,
        "ahorro_registrado_mes": ahorro_reg,
        "disponible": disponible,
        "objetivo_ahorro_mes": objetivo_ahorro_mes,
        "deficit": (ingreso > 0 or modo_arranque) and balance < 0,
        "sin_ingreso": ingreso == 0,
        "modo_arranque": modo_arranque,
        "saldo_inicial": p.get("saldo_inicial") if modo_arranque else None,
        "regla": regla,
        "reparto": reparto,
        "metas": metas_an,
        "suscripciones": fijos,
        "gasto_fijo_mes": sum(f["monto"] for f in fijos),
        "presupuestos": presupuestos,
    }


def _con_fijos(por_categoria: dict, fijos: list[dict]) -> dict:
    """Suma los gastos fijos activos al desglose por categoría del mes.
    Los fijos cuentan cada mes aunque no tengan una fila con fecha."""
    buckets = {cat: {"total": d["total"], "n": d["n"]} for cat, d in por_categoria.items()}
    for f in fijos:
        b = buckets.setdefault(f["categoria"], {"total": 0, "n": 0})
        b["total"] += f["monto"]
        b["n"] += 1
    return buckets


def _insights(buckets: dict, prev_buckets: dict) -> list[str]:
    """Frases automáticas con los cambios más notables vs el mes anterior.
    Determinístico (sin LLM): delta relevante = ±25% y al menos $10.000."""
    out = []
    for cat in set(buckets) | set(prev_buckets):
        cur = buckets.get(cat, {}).get("total", 0)
        prev = prev_buckets.get(cat, {}).get("total", 0)
        diff = cur - prev
        if abs(diff) < 10_000:
            continue
        if prev == 0:
            out.append((abs(diff), f"🆕 {cat}: apareció este mes (+${diff:,})".replace(",", ".")))
        else:
            pct = round(diff / prev * 100)
            if abs(pct) < 25:
                continue
            flecha = "▲" if diff > 0 else "▼"
            out.append((abs(diff), f"{flecha} {cat}: {pct:+d}% vs mes anterior "
                                   f"({'+' if diff > 0 else '−'}${abs(diff):,})".replace(",", ".")))
    out.sort(key=lambda x: -x[0])
    return [t for _, t in out[:3]]


def construir(user_id: int, hoy: date | None = None,
              anio: int | None = None, mes: int | None = None) -> dict:
    """Payload del dashboard. Con anio/mes muestra un mes histórico (navegación);
    sin ellos, el mes en curso."""
    hoy = hoy or date.today()
    if anio is None or mes is None:
        anio, mes = hoy.year, hoy.month
    es_mes_actual = (anio, mes) == (hoy.year, hoy.month)

    fijos = listar_gastos_fijos(user_id)
    fijo_total = sum(f["monto"] for f in fijos)

    r = resumen_mes(user_id, anio, mes)
    pa, pm = _mes_anterior(anio, mes)
    r_prev = resumen_mes(user_id, pa, pm)

    # Los fijos cuentan en el mes visto y en el anterior (misma configuración),
    # así la comparación es justa y el donut cuadra con el total.
    buckets = _con_fijos(r["por_categoria"], fijos)
    prev_buckets = _con_fijos(r_prev["por_categoria"], fijos)
    total = sum(b["total"] for b in buckets.values())
    total_prev = sum(b["total"] for b in prev_buckets.values())

    dias_mes = calendar.monthrange(anio, mes)[1]
    dia_actual = hoy.day if es_mes_actual else dias_mes

    # Proyección: solo tiene sentido en el mes en curso. Se proyecta el gasto
    # variable y se suman los fijos (que son ciertos).
    proyeccion = None
    if es_mes_actual:
        montos = [g["monto"] for g in gastos_del_mes(user_id, anio, mes)]
        proy_var = _proyeccion(montos, r["total"], dia_actual, dias_mes)
        proyeccion = proy_var + fijo_total if proy_var is not None else None

    # Serie diaria del gasto variable (los fijos no tienen día).
    serie_raw = serie_diaria(user_id, anio, mes)
    dias_registrados = len(serie_raw)
    promedio_diario = round(r["total"] / dias_registrados) if dias_registrados else 0

    variacion = round((total - total_prev) / total_prev * 100) if total_prev > 0 else None

    serie = [
        {"fecha": f"{anio:04d}-{mes:02d}-{d:02d}", "monto": serie_raw.get(f"{anio:04d}-{mes:02d}-{d:02d}", 0)}
        for d in range(1, dias_mes + 1)
    ]
    gasto_hoy = serie_raw.get(hoy.isoformat(), 0) if es_mes_actual else 0

    categorias = []
    for cat, datos in sorted(buckets.items(), key=lambda kv: -kv[1]["total"]):
        categorias.append({
            "categoria": cat,
            "total": datos["total"],
            "n": datos["n"],
            "color": COLORES.get(cat, COLORES["otros"]),
        })
    _asignar_porcentajes(categorias, total)

    # Mayor gasto: el variable más grande o el fijo más caro, lo que gane.
    mg = mayor_gasto(user_id, anio, mes)
    mayor = None
    if mg:
        mayor = {"monto": mg["monto"], "categoria": mg["categoria"],
                 "descripcion": mg["descripcion"], "fecha": mg["fecha"]}
    if fijos:
        top_fijo = max(fijos, key=lambda f: f["monto"])
        if mayor is None or top_fijo["monto"] > mayor["monto"]:
            mayor = {"monto": top_fijo["monto"], "categoria": top_fijo["categoria"],
                     "descripcion": f"{top_fijo['descripcion']} (fijo mensual)", "fecha": None}

    return {
        "mes": r["mes"],
        "es_mes_actual": es_mes_actual,
        "total": total,
        "total_prev": total_prev,
        "mes_prev_con_datos": total_prev > 0,
        "variacion_pct": variacion,
        "proyeccion": proyeccion,               # None en meses históricos o primeros días
        "promedio_diario": promedio_diario,
        "gasto_hoy": gasto_hoy,
        "dias_registrados": dias_registrados,
        "dia_actual": dia_actual,
        "dias_mes": dias_mes,
        "categorias": categorias,
        "serie": serie,
        "mayor_gasto": mayor,
        "insights": _insights(buckets, prev_buckets) if total_prev > 0 else [],
        "finanzas": _finanzas(user_id, anio, mes, total, buckets, hoy, fijos),
    }
