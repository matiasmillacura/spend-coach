import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding
from werkzeug.security import generate_password_hash

import db
import rag_gastos


@pytest.fixture()
def store_falso():
    rag_gastos.reset_store()
    rag_gastos.get_store(DeterministicFakeEmbedding(size=64))
    yield
    rag_gastos.reset_store()


def _usuario(email, rut):
    return db.crear_usuario_password(
        email=email, password_hash=generate_password_hash("clave12345"),
        apodo="RAG", nombre_completo="Usuario RAG", rut=rut, fecha_nacimiento="1990-01-01",
    )["id"]


def test_busqueda_no_cruza_usuarios(store_falso):
    a = _usuario("rag-a@test.cl", "7000000-8")
    b = _usuario("rag-b@test.cl", "7000001-6")

    db.agregar_gasto(a, 25000, "salud", "veterinario del perro", "2026-07-10", "")
    db.agregar_gasto(b, 30000, "salud", "veterinario del gato", "2026-07-11", "")

    de_a = rag_gastos.buscar(a, "mascota", k=5)
    assert de_a, "el usuario A debería recuperar su propio gasto"
    assert all(g["descripcion"] == "veterinario del perro" for g in de_a)

    ids_b = {g["id"] for g in rag_gastos.buscar(b, "mascota", k=5)}
    ids_a = {g["id"] for g in de_a}
    assert ids_a.isdisjoint(ids_b)


def test_indexa_solo_los_gastos_nuevos(store_falso):
    u = _usuario("rag-c@test.cl", "7000002-4")
    db.agregar_gasto(u, 5000, "comida", "café", "2026-07-01", "")

    assert rag_gastos._sincronizar(u) == 1
    assert rag_gastos._sincronizar(u) == 0

    db.agregar_gasto(u, 7000, "comida", "almuerzo", "2026-07-02", "")
    assert rag_gastos._sincronizar(u) == 1


def test_consulta_vacia_no_rompe(store_falso):
    u = _usuario("rag-d@test.cl", "7000003-2")
    assert rag_gastos.buscar(u, "  ") == []


def test_resumen_incluye_total(store_falso):
    u = _usuario("rag-e@test.cl", "7000004-0")
    db.agregar_gasto(u, 12000, "transporte", "bencina", "2026-07-05", "")

    r = rag_gastos.buscar_resumido(u, "auto", k=3)
    assert r["encontrados"] >= 1
    assert r["total_gastado"] == sum(
        g["monto"] for g in r["resultados"] if g["tipo"] == "gasto")


def test_indexa_metas_y_eventos_como_contexto(store_falso):
    u = _usuario("rag-f@test.cl", "7000005-9")
    db.crear_meta(u, "viaje al sur", tipo="monto_fecha", monto_objetivo=1_500_000)
    db.crear_evento_futuro(u, "cumpleaños de mi papá", "2026-09-10", 40_000)

    tipos = {r["tipo"] for r in rag_gastos.buscar(u, "regalo viaje familia", k=10)}
    assert "meta" in tipos
    assert "evento" in tipos
