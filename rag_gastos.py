"""Búsqueda semántica sobre los gastos del usuario (RAG con embeddings)."""
from __future__ import annotations

import logging

from langchain_core.documents import Document

import db
from config import config

log = logging.getLogger(__name__)

MAX_INDEXAR = 500
_store = None
_indexados: dict[int, set[int]] = {}


def _texto(g: dict) -> str:
    return f"{g['descripcion']} · categoría {g['categoria']} · ${g['monto']:,} · {g['fecha']}".replace(",", ".")


def _documento(user_id: int, g: dict) -> Document:
    return Document(
        page_content=_texto(g),
        metadata={"user_id": user_id, "gasto_id": g["id"], "fecha": g["fecha"],
                  "monto": g["monto"], "categoria": g["categoria"], "descripcion": g["descripcion"]},
    )


def _embeddings():
    from langchain_voyageai import VoyageAIEmbeddings
    return VoyageAIEmbeddings(model=config.VOYAGE_MODEL, api_key=config.VOYAGE_API_KEY,
                              batch_size=32)


def get_store(embeddings=None):
    """Vector store: pgvector sobre la BD en producción, en memoria con SQLite."""
    global _store
    if _store is None:
        emb = embeddings or _embeddings()
        if config.DATABASE_URL.startswith("sqlite"):
            from langchain_core.vectorstores import InMemoryVectorStore
            _store = InMemoryVectorStore(emb)
        else:
            from langchain_postgres import PGVector
            _store = PGVector(
                embeddings=emb,
                collection_name="gastos_semantico",
                connection=config.DATABASE_URL,
                use_jsonb=True,
            )
    return _store


def reset_store() -> None:
    global _store
    _store = None
    _indexados.clear()


def _documentos_contexto(user_id: int) -> list[tuple[str, Document]]:
    """Metas y eventos futuros también se indexan: así el asesor recuerda PARA QUÉ
    era cada cosa cuando decide si un gasto vale la pena."""
    docs = []
    for m in db.listar_metas(user_id):
        texto = f"meta de ahorro: {m['nombre']}"
        if m.get("monto_objetivo"):
            texto += f" · objetivo ${m['monto_objetivo']:,}".replace(",", ".")
        if m.get("fecha_objetivo"):
            texto += f" · para {m['fecha_objetivo']}"
        docs.append((f"u{user_id}-m{m['id']}",
                     Document(page_content=texto,
                              metadata={"user_id": user_id, "tipo": "meta",
                                        "descripcion": m["nombre"], "fecha": m.get("fecha_objetivo"),
                                        "monto": m.get("monto_objetivo") or 0,
                                        "categoria": "meta", "gasto_id": None})))
    for e in db.listar_eventos_futuros(user_id):
        texto = f"gasto que se viene: {e['descripcion']} el {e['fecha']}"
        if e["monto_estimado"]:
            texto += f" · estimado ${e['monto_estimado']:,}".replace(",", ".")
        docs.append((f"u{user_id}-e{e['id']}",
                     Document(page_content=texto,
                              metadata={"user_id": user_id, "tipo": "evento",
                                        "descripcion": e["descripcion"], "fecha": e["fecha"],
                                        "monto": e["monto_estimado"], "categoria": "evento",
                                        "gasto_id": None})))
    return docs


def _sincronizar(user_id: int) -> int:
    """Indexa lo que aún no está en el store (upsert por id estable)."""
    ya = _indexados.setdefault(user_id, set())
    pendientes: list[tuple[str, Document]] = [
        (f"u{user_id}-g{g['id']}", _documento(user_id, g))
        for g in db.ultimos_gastos(user_id, MAX_INDEXAR) if f"g{g['id']}" not in ya
    ]
    pendientes += [(i, d) for i, d in _documentos_contexto(user_id)
                   if i.split("-", 1)[1] not in ya]
    if pendientes:
        get_store().add_documents([d for _, d in pendientes], ids=[i for i, _ in pendientes])
        ya.update(i.split("-", 1)[1] for i, _ in pendientes)
    return len(pendientes)


def _filtro(store, user_id: int):
    """El filtro por usuario se aplica en el store, no en el prompt: un usuario
    nunca puede recuperar documentos de otro."""
    if store.__class__.__name__ == "InMemoryVectorStore":
        return lambda doc: doc.metadata.get("user_id") == user_id
    return {"user_id": {"$eq": user_id}}


def buscar(user_id: int, consulta: str, k: int = 6) -> list[dict]:
    """Gastos más parecidos a la consulta, del usuario indicado."""
    consulta = (consulta or "").strip()
    if not consulta:
        return []
    _sincronizar(user_id)
    store = get_store()
    encontrados = store.similarity_search_with_score(consulta, k=k, filter=_filtro(store, user_id))
    salida = []
    for doc, score in encontrados:
        if doc.metadata.get("user_id") != user_id:
            continue
        salida.append({
            "id": doc.metadata.get("gasto_id"),
            "tipo": doc.metadata.get("tipo", "gasto"),
            "fecha": doc.metadata.get("fecha"),
            "monto": doc.metadata.get("monto"),
            "categoria": doc.metadata.get("categoria"),
            "descripcion": doc.metadata.get("descripcion"),
            "similitud": round(float(score), 4),
        })
    return salida


def buscar_resumido(user_id: int, consulta: str, k: int = 6) -> dict:
    """Resultado listo para devolver como tool_result: gastos + total."""
    try:
        gastos = buscar(user_id, consulta, k)
    except Exception as e:
        log.exception("Búsqueda semántica falló")
        return {"error": f"No pude buscar en el historial ({type(e).__name__})."}
    solo_gastos = [g for g in gastos if g.get("tipo", "gasto") == "gasto"]
    return {"consulta": consulta, "encontrados": len(gastos),
            "total_gastado": sum(g["monto"] or 0 for g in solo_gastos),
            "resultados": gastos}
