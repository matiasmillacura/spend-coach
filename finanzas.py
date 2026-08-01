"""Motor financiero: toda la aritmética de deudas. Funciones puras, sin base de datos.

El agente nunca calcula montos; llama a estas funciones y explica el resultado.
"""
from __future__ import annotations

TOPE_MESES = 600
RENDIMIENTO_AHORRO_MENSUAL = 0.004  # depósito a plazo conservador en Chile (~4,9% anual)


def tasa_anual(tasa_mensual: float) -> float:
    """Tasa mensual a anual compuesta: 2,86% mensual -> 40,27% anual."""
    return (1 + tasa_mensual) ** 12 - 1


def interes_del_mes(saldo: int, tasa_mensual: float) -> int:
    return round(saldo * tasa_mensual)


def pago_minimo_sugerido(saldo: int, pct: float = 0.05, piso: int = 20000) -> int:
    """Mínimo típico de una tarjeta chilena: un % del saldo con piso."""
    return min(saldo, max(round(saldo * pct), piso))


def simular_pago_fijo(saldo: int, tasa_mensual: float, cuota: int) -> dict:
    """Cuántos meses y cuánto interés cuesta pagar una deuda con cuota fija."""
    if saldo <= 0:
        return {"paga": True, "meses": 0, "total_pagado": 0, "intereses": 0}
    primer_interes = interes_del_mes(saldo, tasa_mensual)
    if cuota <= primer_interes:
        return {"paga": False, "meses": None, "total_pagado": None, "intereses": None,
                "motivo": "La cuota no alcanza a cubrir ni los intereses del mes: "
                          "la deuda crecería en vez de bajar.",
                "interes_mensual": primer_interes}
    actual, total, intereses, meses = saldo, 0, 0, 0
    while actual > 0 and meses < TOPE_MESES:
        i = interes_del_mes(actual, tasa_mensual)
        pago = min(cuota, actual + i)
        actual = actual + i - pago
        total += pago
        intereses += i
        meses += 1
    return {"paga": True, "meses": meses, "total_pagado": total, "intereses": intereses}


def simular_pago_minimo(saldo: int, tasa_mensual: float, pct: float = 0.05,
                        piso: int = 20000) -> dict:
    """El escenario que hay que mostrarle a todo el mundo: pagar solo el mínimo."""
    if saldo <= 0:
        return {"paga": True, "meses": 0, "total_pagado": 0, "intereses": 0}
    actual, total, intereses, meses = saldo, 0, 0, 0
    while actual > 0 and meses < TOPE_MESES:
        i = interes_del_mes(actual, tasa_mensual)
        cuota = pago_minimo_sugerido(actual, pct, piso)
        if cuota <= i:
            return {"paga": False, "meses": None, "total_pagado": None, "intereses": None,
                    "motivo": "Con el mínimo no se cubren los intereses: la deuda crece."}
        pago = min(cuota, actual + i)
        actual = actual + i - pago
        total += pago
        intereses += i
        meses += 1
    return {"paga": True, "meses": meses, "total_pagado": total, "intereses": intereses}


def comparar_escenarios(saldo: int, tasa_mensual: float, cuotas: list[int]) -> list[dict]:
    """Mismo saldo, distintas cuotas: la tabla que hace entender el costo del mínimo."""
    salida = [{"escenario": "pago mínimo", **simular_pago_minimo(saldo, tasa_mensual)}]
    for c in sorted(set(cuotas)):
        salida.append({"escenario": f"cuota fija ${c:,}".replace(",", "."),
                       "cuota": c, **simular_pago_fijo(saldo, tasa_mensual, c)})
    return salida


def orden_avalancha(deudas: list[dict]) -> list[dict]:
    """Deudas ordenadas por tasa mensual descendente: pagar primero la más cara es
    siempre lo que minimiza el interés total."""
    return sorted([d for d in deudas if d.get("saldo", 0) > 0],
                  key=lambda d: (-float(d.get("tasa_mensual") or 0), -int(d.get("saldo", 0))))


def conviene_ahorrar(monto: int, tasa_deuda_mensual: float | None,
                     rendimiento_mensual: float = RENDIMIENTO_AHORRO_MENSUAL) -> dict:
    """¿Ahorrar este monto o abonarlo a la deuda? Compara los dos rendimientos."""
    gana_ahorrando = round(monto * rendimiento_mensual)
    if not tasa_deuda_mensual:
        return {"recomendacion": "ahorrar", "gana_ahorrando": gana_ahorrando,
                "evita_abonando": 0,
                "motivo": "No hay deuda con interés: ahorrar es la mejor opción."}
    evita_abonando = round(monto * tasa_deuda_mensual)
    if evita_abonando <= gana_ahorrando:
        return {"recomendacion": "ahorrar", "gana_ahorrando": gana_ahorrando,
                "evita_abonando": evita_abonando,
                "motivo": "La deuda es más barata que el rendimiento del ahorro."}
    veces = round(evita_abonando / gana_ahorrando, 1) if gana_ahorrando else None
    return {"recomendacion": "abonar", "gana_ahorrando": gana_ahorrando,
            "evita_abonando": evita_abonando, "veces_mejor": veces,
            "motivo": f"Abonar evita ${evita_abonando:,} de interés al mes; ahorrar rinde "
                      f"${gana_ahorrando:,}.".replace(",", ".")}


def colchon_sugerido(gasto_mensual: int, hay_deuda_cara: bool) -> int:
    """Colchón objetivo. Con deuda cara encima se busca uno CHICO primero (para no
    volver a la tarjeta ante un imprevisto) y recién sin deuda se apunta a un mes."""
    if gasto_mensual <= 0:
        return 100_000
    if hay_deuda_cara:
        return max(100_000, min(round(gasto_mensual * 0.25), 400_000))
    return gasto_mensual


def plan_mensual(deudas: list[dict], excedente: int, colchon_actual: int = 0,
                 colchon_objetivo: int = 0, reparto_colchon: float = 0.5) -> dict:
    """Reparte el excedente del mes: mínimos, colchón y abono extra a la deuda más cara.

    Mientras falte colchón, el sobrante se divide entre colchón y deuda (por defecto mitad
    y mitad) en vez de llenar el colchón primero: con una tarjeta al 40% anual, postergar
    todos los abonos hasta juntar un mes de gastos sale caro, pero quedarse sin colchón
    devuelve a la tarjeta al primer imprevisto.
    """
    activas = orden_avalancha(deudas)
    minimos = [{"deuda": d.get("nombre", "deuda"), "linea_id": d.get("id"),
                "monto": pago_minimo_sugerido(int(d["saldo"]),
                                              float(d.get("pct_minimo") or 0.05))}
               for d in activas]
    total_minimos = sum(m["monto"] for m in minimos)
    restante = excedente - total_minimos

    if restante < 0:
        return {"alcanza_minimos": False, "excedente": excedente,
                "minimos": minimos, "total_minimos": total_minimos,
                "faltante": -restante, "a_colchon": 0, "abono_extra": None,
                "advertencia": "El excedente no cubre los pagos mínimos. Caer en mora es "
                               "lo más caro que existe: hay que recortar gastos o "
                               "renegociar antes de que se acumulen cargos."}

    falta_colchon = max(0, colchon_objetivo - colchon_actual)
    hay_deuda = bool(activas)
    if falta_colchon and hay_deuda:
        a_colchon = min(restante, falta_colchon, round(restante * reparto_colchon))
    else:
        a_colchon = min(restante, falta_colchon)
    restante -= a_colchon

    abono_extra = None
    if restante > 0 and activas:
        objetivo = activas[0]
        abono_extra = {"deuda": objetivo.get("nombre", "deuda"),
                       "linea_id": objetivo.get("id"), "monto": restante,
                       "tasa_mensual": objetivo.get("tasa_mensual"),
                       "motivo": "Es la deuda con la tasa más alta: cada peso abonado acá "
                                 "ahorra más interés que en cualquier otra."}
        restante = 0

    return {"alcanza_minimos": True, "excedente": excedente,
            "minimos": minimos, "total_minimos": total_minimos,
            "a_colchon": a_colchon, "abono_extra": abono_extra, "sobra": restante}


def impacto_abono(saldo: int, tasa_mensual: float, cuota_base: int, abono_extra: int) -> dict:
    """Cuánto adelanta y cuánto interés ahorra un abono extra sobre el plan base."""
    base = simular_pago_fijo(saldo, tasa_mensual, cuota_base)
    con_abono = simular_pago_fijo(saldo, tasa_mensual, cuota_base + abono_extra)
    if not base["paga"] or not con_abono["paga"]:
        return {"base": base, "con_abono": con_abono}
    return {"base": base, "con_abono": con_abono,
            "meses_menos": base["meses"] - con_abono["meses"],
            "intereses_ahorrados": base["intereses"] - con_abono["intereses"]}


def evaluar_repactacion(saldo: int, tasa_actual_mensual: float, cuota_actual: int,
                        cuota_nueva: int, meses_nuevos: int) -> dict:
    """Repactar casi siempre baja la cuota y sube el costo total. Muestra ambas cosas."""
    actual = simular_pago_fijo(saldo, tasa_actual_mensual, cuota_actual)
    total_repactado = cuota_nueva * meses_nuevos
    resultado = {"actual": actual, "repactado": {"cuota": cuota_nueva,
                                                 "meses": meses_nuevos,
                                                 "total_pagado": total_repactado,
                                                 "intereses": total_repactado - saldo}}
    if actual["paga"]:
        resultado["diferencia_total"] = total_repactado - actual["total_pagado"]
        resultado["alivio_mensual"] = cuota_actual - cuota_nueva
        resultado["conviene"] = total_repactado <= actual["total_pagado"]
    else:
        resultado["conviene"] = True
        resultado["motivo"] = ("Hoy la cuota no cubre ni los intereses, así que repactar "
                               "es mejor que seguir como está.")
    return resultado
