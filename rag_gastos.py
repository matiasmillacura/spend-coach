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


def _sincronizar(user_id: int) -> int:
    """Indexa los gastos del usuario que aún no están en el store (upsert por id)."""
    gastos = db.ultimos_gastos(user_id, MAX_INDEXAR)
    ya = _indexados.setdefault(user_id, set())
    nuevos = [g for g in gastos if g["id"] not in ya]
    if nuevos:
        store = get_store()
        store.add_documents(
            [_documento(user_id, g) for g in nuevos],
            ids=[f"u{user_id}-g{g['id']}" for g in nuevos],
        )
        ya.update(g["id"] for g in nuevos)
    return len(nuevos)


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
    return {"consulta": consulta, "encontrados": len(gastos),
            "total": sum(g["monto"] for g in gastos), "gastos": gastos}
