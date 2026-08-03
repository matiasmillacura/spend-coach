from datetime import date

from werkzeug.security import generate_password_hash

import dashboard
import db


def _usuario(email, rut, saldo=500_000):
    uid = db.crear_usuario_password(
        email=email, password_hash=generate_password_hash("clave12345"),
        apodo="Bal", nombre_completo="Usuario Balance", rut=rut,
        fecha_nacimiento="1990-01-01")["id"]
    db.set_onboarding(uid, completo=True)
    db.agregar_ingreso_fijo(uid, "sueldo", 800_000)
    db.set_saldo_inicial(uid, saldo)
    return uid


def _te_queda(uid):
    return dashboard.construir(uid)["finanzas"]["balance"]


def test_pagar_una_deuda_descuenta_del_saldo():
    uid = _usuario("bal-a@test.cl", "7000008-3")
    hoy = date.today().isoformat()
    t = db.crear_tarjeta(uid, "CMR Falabella")
    linea = db.registrar_deuda(uid, 700_000, 2.86, "rotativo", tarjeta_id=t["id"])

    assert _te_queda(uid) == 500_000
    db.registrar_pago_deuda(uid, linea["id"], 200_000, hoy)
    # pagar la tarjeta no es un "gasto", pero la plata salió igual
    assert _te_queda(uid) == 300_000


def test_ahorrar_descuenta_del_saldo():
    uid = _usuario("bal-b@test.cl", "7000009-1")
    db.registrar_ahorro(uid, 50_000, date.today().isoformat())
    assert _te_queda(uid) == 450_000


def test_gasto_pago_y_ahorro_se_acumulan():
    uid = _usuario("bal-c@test.cl", "7000010-5")
    hoy = date.today().isoformat()
    db.agregar_gasto(uid, 30_000, "comida", "almuerzos", hoy, "")
    t = db.crear_tarjeta(uid, "Banco de Chile")
    linea = db.registrar_deuda(uid, 300_000, 3.45, "rotativo", tarjeta_id=t["id"])
    db.registrar_pago_deuda(uid, linea["id"], 100_000, hoy)
    db.registrar_ahorro(uid, 20_000, hoy)

    assert _te_queda(uid) == 500_000 - 30_000 - 100_000 - 20_000


def test_avance_de_pago_de_la_tarjeta():
    uid = _usuario("bal-d@test.cl", "7000011-3")
    t = db.crear_tarjeta(uid, "CMR")
    linea = db.registrar_deuda(uid, 250_000, 2.86, "rotativo", tarjeta_id=t["id"],
                               total_facturado=250_000, pago_minimo=35_000)
    db.registrar_pago_deuda(uid, linea["id"], 200_000, date.today().isoformat())

    d = dashboard.construir(uid)["deudas"]["items"][0]
    assert d["total_facturado"] == 250_000
    assert d["pago_minimo"] == 35_000
    assert d["pagado"] == 200_000
    assert d["pct_pagado"] == 80


def test_desglose_de_una_categoria():
    uid = _usuario("bal-e@test.cl", "7000012-1")
    hoy = date.today()
    db.agregar_gasto(uid, 12_000, "comida", "sushi", hoy.isoformat(), "")
    db.agregar_gasto(uid, 8_000, "comida", "café", hoy.isoformat(), "")
    db.agregar_gasto(uid, 45_000, "supermercado", "jumbo", hoy.isoformat(), "")

    detalle = db.gastos_por_categoria_detalle(uid, "comida", hoy.year, hoy.month)
    assert [g["descripcion"] for g in detalle] == ["café", "sushi"]
    assert sum(g["monto"] for g in detalle) == 20_000
