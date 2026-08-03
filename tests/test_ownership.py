from werkzeug.security import generate_password_hash

import db


def _usuario(email, rut):
    return db.crear_usuario_password(
        email=email, password_hash=generate_password_hash("clave12345"),
        apodo=email.split("@")[0], nombre_completo="Usuario Prueba",
        rut=rut, fecha_nacimiento="1990-01-01",
    )["id"]


def test_gastos_no_se_filtran_entre_usuarios():
    a = _usuario("owner-a@test.cl", "14.221.976-K")
    b = _usuario("owner-b@test.cl", "16.334.615-K")

    db.agregar_gasto(a, 5000, "comida", "almuerzo", "2026-07-01", "")

    de_a = db.ultimos_gastos(a, 10)
    de_b = db.ultimos_gastos(b, 10)
    assert any(g["descripcion"] == "almuerzo" for g in de_a)
    assert de_b == []


def test_rut_duplicado_rechazado():
    _usuario("rut-1@test.cl", "18.972.631-7")
    try:
        _usuario("rut-2@test.cl", "18.972.631-7")
        assert False, "debió lanzar ValueError"
    except ValueError:
        pass


def test_borrar_usuario_no_toca_a_los_demas():
    victima = _usuario("borrar-a@test.cl", "7000006-7")
    testigo = _usuario("borrar-b@test.cl", "7000007-5")
    for uid in (victima, testigo):
        db.agregar_gasto(uid, 5000, "comida", "almuerzo", "2026-08-01", "")
        db.agregar_ingreso_fijo(uid, "sueldo", 800_000)
        db.agregar_mensaje(uid, "user", "hola")

    borrado = db.borrar_usuario(victima)

    assert borrado["users"] == 1
    assert borrado["gastos"] == 1
    assert db.buscar_usuario_por_email("borrar-a@test.cl") is None
    assert db.ultimos_gastos(victima, 10) == []
    # el otro usuario queda intacto
    assert db.buscar_usuario_por_email("borrar-b@test.cl") is not None
    assert len(db.ultimos_gastos(testigo, 10)) == 1
    assert len(db.listar_ingresos_fijos(testigo)) == 1
