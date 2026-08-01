import finanzas


def test_tasa_mensual_a_anual():
    assert round(finanzas.tasa_anual(0.0286) * 100, 2) == 40.27
    assert round(finanzas.tasa_anual(0.0345) * 100, 2) == 50.23


def test_interes_del_mes():
    assert finanzas.interes_del_mes(1_000_000, 0.0286) == 28600
    assert finanzas.interes_del_mes(1_000_000, 0.0345) == 34500


def test_pago_minimo_tiene_piso_y_tope():
    assert finanzas.pago_minimo_sugerido(1_000_000) == 50000
    assert finanzas.pago_minimo_sugerido(100_000) == 20000
    assert finanzas.pago_minimo_sugerido(5_000) == 5000


def test_cuota_que_no_cubre_el_interes_no_paga_nunca():
    r = finanzas.simular_pago_fijo(1_000_000, 0.029, 20_000)
    assert r["paga"] is False
    assert r["interes_mensual"] == 29000


def test_pago_minimo_es_mucho_mas_caro_que_cuota_fija():
    minimo = finanzas.simular_pago_minimo(1_000_000, 0.029)
    fijo = finanzas.simular_pago_fijo(1_000_000, 0.029, 150_000)
    assert minimo["meses"] > 60
    assert minimo["intereses"] > 1_000_000
    assert fijo["meses"] < 12
    assert fijo["intereses"] < minimo["intereses"] / 5


def test_deuda_sin_saldo_no_cuesta_nada():
    r = finanzas.simular_pago_fijo(0, 0.03, 50_000)
    assert r == {"paga": True, "meses": 0, "total_pagado": 0, "intereses": 0}


def test_avalancha_ordena_por_tasa_no_por_saldo():
    deudas = [
        {"nombre": "falabella", "saldo": 800_000, "tasa_mensual": 0.0286},
        {"nombre": "chile", "saldo": 300_000, "tasa_mensual": 0.0345},
        {"nombre": "pagada", "saldo": 0, "tasa_mensual": 0.05},
    ]
    orden = [d["nombre"] for d in finanzas.orden_avalancha(deudas)]
    assert orden == ["chile", "falabella"]


def test_conviene_abonar_cuando_la_deuda_es_cara():
    r = finanzas.conviene_ahorrar(100_000, 0.029)
    assert r["recomendacion"] == "abonar"
    assert r["evita_abonando"] == 2900
    assert r["gana_ahorrando"] == 400
    assert r["veces_mejor"] == 7.2


def test_conviene_ahorrar_sin_deuda():
    assert finanzas.conviene_ahorrar(100_000, None)["recomendacion"] == "ahorrar"


def test_conviene_ahorrar_si_la_deuda_es_barata():
    assert finanzas.conviene_ahorrar(100_000, 0.002)["recomendacion"] == "ahorrar"


def test_plan_reparte_minimos_colchon_y_abono():
    deudas = [
        {"id": 1, "nombre": "falabella", "saldo": 800_000, "tasa_mensual": 0.0286},
        {"id": 2, "nombre": "chile", "saldo": 300_000, "tasa_mensual": 0.0345},
    ]
    p = finanzas.plan_mensual(deudas, excedente=200_000,
                              colchon_actual=0, colchon_objetivo=50_000)
    assert p["alcanza_minimos"] is True
    assert p["total_minimos"] == 40000 + 20000  # 5% de 800k y piso en la de 300k
    assert p["a_colchon"] == 50_000
    assert p["abono_extra"]["deuda"] == "chile"  # la de mayor tasa
    assert p["abono_extra"]["monto"] == 200_000 - 60_000 - 50_000
    assert p["sobra"] == 0


def test_plan_avisa_cuando_no_alcanza_para_los_minimos():
    deudas = [{"id": 1, "nombre": "chile", "saldo": 1_000_000, "tasa_mensual": 0.0345}]
    p = finanzas.plan_mensual(deudas, excedente=10_000)
    assert p["alcanza_minimos"] is False
    assert p["faltante"] == 40_000
    assert "mora" in p["advertencia"]


def test_impacto_de_un_abono_extra():
    r = finanzas.impacto_abono(1_000_000, 0.029, cuota_base=100_000, abono_extra=150_000)
    assert r["meses_menos"] > 0
    assert r["intereses_ahorrados"] > 0
    assert r["con_abono"]["meses"] < r["base"]["meses"]


def test_repactacion_baja_la_cuota_pero_puede_subir_el_total():
    r = finanzas.evaluar_repactacion(1_000_000, 0.029, cuota_actual=150_000,
                                     cuota_nueva=90_000, meses_nuevos=18)
    assert r["alivio_mensual"] == 60_000
    assert r["repactado"]["total_pagado"] == 1_620_000
    assert r["diferencia_total"] > 0
    assert r["conviene"] is False


def test_colchon_es_chico_mientras_haya_deuda_cara():
    assert finanzas.colchon_sugerido(400_000, hay_deuda_cara=True) == 100_000
    assert finanzas.colchon_sugerido(400_000, hay_deuda_cara=False) == 400_000
    assert finanzas.colchon_sugerido(2_000_000, hay_deuda_cara=True) == 400_000


def test_con_deuda_el_excedente_se_reparte_entre_colchon_y_abono():
    deudas = [{"id": 1, "nombre": "chile", "saldo": 300_000, "tasa_mensual": 0.0345}]
    p = finanzas.plan_mensual(deudas, excedente=220_000,
                              colchon_actual=0, colchon_objetivo=400_000)
    # sin el reparto, los 200.000 restantes se habrían ido enteros al colchón
    assert p["a_colchon"] == 100_000
    assert p["abono_extra"]["monto"] == 100_000


def test_sin_deuda_el_colchon_se_llena_completo():
    p = finanzas.plan_mensual([], excedente=200_000, colchon_actual=0,
                              colchon_objetivo=500_000)
    assert p["a_colchon"] == 200_000
    assert p["abono_extra"] is None


def test_proximo_pago_salta_al_mes_siguiente_si_ya_paso():
    from datetime import date
    assert finanzas.proximo_pago(30, date(2026, 8, 1)) == date(2026, 8, 30)
    assert finanzas.proximo_pago(1, date(2026, 8, 1)) == date(2026, 9, 1)
    # febrero no tiene 30: cae en el último día del mes
    assert finanzas.proximo_pago(30, date(2026, 2, 1)) == date(2026, 2, 28)
    assert finanzas.proximo_pago(None, date(2026, 8, 1)) is None


def test_flujo_descuenta_compromisos_y_colchon():
    from datetime import date
    hoy = date(2026, 8, 1)
    compromisos = [
        {"concepto": "arriendo", "monto": 300_000, "fecha": None},
        {"concepto": "cumpleaños", "monto": 40_000, "fecha": "2026-08-05"},
        {"concepto": "fuera de rango", "monto": 999_000, "fecha": "2026-12-01"},
    ]
    f = finanzas.flujo_hasta_proximo_ingreso(500_000, compromisos, hoy,
                                             date(2026, 8, 30), colchon=100_000)
    assert f["total_comprometido"] == 340_000   # el de diciembre no cuenta
    assert f["disponible"] == 60_000
    assert f["en_rojo"] is False


def test_flujo_marca_rojo_cuando_no_alcanza():
    from datetime import date
    f = finanzas.flujo_hasta_proximo_ingreso(
        100_000, [{"concepto": "arriendo", "monto": 300_000, "fecha": None}],
        date(2026, 8, 1), date(2026, 8, 30), colchon=0)
    assert f["en_rojo"] is True


def test_puedo_gastar_sugiere_tope_y_avisa_cuando_apreta():
    holgado = finanzas.puedo_gastar(30_000, disponible=200_000)
    assert holgado["puede"] is True and holgado["tope_recomendado"] == 100_000
    justo = finanzas.puedo_gastar(150_000, disponible=200_000)
    assert justo["puede"] is True and justo.get("ajustado") is True
    excede = finanzas.puedo_gastar(300_000, disponible=200_000)
    assert excede["puede"] is False and excede["excede_por"] == 100_000
    sin_plata = finanzas.puedo_gastar(10_000, disponible=-5_000)
    assert sin_plata["puede"] is False


def test_perfil_cambia_el_colchon():
    conservador = finanzas.colchon_sugerido(500_000, False, "conservador")
    gustito = finanzas.colchon_sugerido(500_000, False, "gustito")
    assert conservador > gustito
    assert finanzas.colchon_sugerido(500_000, False, "no-existe") == 500_000


def test_alertas_de_corte_y_vencimiento():
    from datetime import date
    hoy = date(2026, 8, 1)
    avisos = finanzas.alertas_tarjeta(dia_corte=4, dia_vencimiento=20, hoy=hoy)
    assert [a["tipo"] for a in avisos] == ["corte"]
    assert avisos[0]["dias"] == 3
    assert finanzas.alertas_tarjeta(None, None, hoy) == []


def test_formato_de_pesos():
    assert finanzas.clp(1_234_567) == "$1.234.567"
    assert finanzas.clp(0) == "$0"
