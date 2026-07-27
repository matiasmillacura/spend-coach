"""Versión LangChain/LangGraph del coach; equivalente a coach_agent.py."""
from __future__ import annotations

import json
import sqlite3
import uuid

import anthropic
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, trim_messages
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent

import db
from coach_agent import MAX_HISTORIAL, MAX_TOOL_ROUNDS, TOOLS, _ejecutar_tool, _system_prompt
from config import config
from extractor import ExtractorError

_llm = None


def get_llm() -> ChatAnthropic:
    """Chat model de LangChain sobre Claude."""
    global _llm
    if not config.ANTHROPIC_API_KEY:
        raise ExtractorError(
            "Falta la clave de la API de Claude. Define ANTHROPIC_API_KEY en tu .env."
        )
    if _llm is None:
        _llm = ChatAnthropic(
            model=config.CLAUDE_MODEL_CHAT,
            max_tokens=2048,
            api_key=config.ANTHROPIC_API_KEY,
        )
    return _llm


_checkpointer = None


def get_checkpointer():
    """Persistencia de estado de LangGraph por thread_id (SQLite o Postgres según DATABASE_URL)."""
    global _checkpointer
    if _checkpointer is None:
        if config.DATABASE_URL.startswith("sqlite"):
            conn = sqlite3.connect(config.CHECKPOINT_DB, check_same_thread=False)
            _checkpointer = SqliteSaver(conn)
        else:
            from langgraph.checkpoint.postgres import PostgresSaver
            from psycopg_pool import ConnectionPool

            url = config.DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)
            pool = ConnectionPool(url, min_size=1, max_size=4,
                                  kwargs={"autocommit": True, "prepare_threshold": 0})
            _checkpointer = PostgresSaver(pool)
            _checkpointer.setup()
    return _checkpointer


def _recortar_historial(state):
    """pre_model_hook: recorta lo que ve el modelo sin tocar lo persistido."""
    recortados = trim_messages(
        state["messages"],
        strategy="last",
        token_counter=len,
        max_tokens=MAX_HISTORIAL,
        start_on="human",
        include_system=False,
        allow_partial=False,
    )
    return {"llm_input_messages": recortados}


def _tools_para(user_id: int) -> list[StructuredTool]:
    """Re-empaqueta los TOOLS del agente original como StructuredTool ligados al user_id."""
    def _hacer_func(nombre: str):
        def _run(**kwargs) -> str:
            out = _ejecutar_tool(user_id, nombre, kwargs)
            return json.dumps(out, ensure_ascii=False)
        return _run

    return [
        StructuredTool(
            name=spec["name"],
            description=spec["description"],
            args_schema=spec["input_schema"],
            func=_hacer_func(spec["name"]),
        )
        for spec in TOOLS
    ]


def responder(user_id: int, texto_usuario: str,
              imagen_b64: str | None = None, imagen_tipo: str | None = None) -> str:
    """Misma firma y contrato que coach_agent.responder."""
    texto_usuario = (texto_usuario or "").strip()
    if not texto_usuario and not imagen_b64:
        return "Cuéntame algo 🙂"

    llm = get_llm()

    msg_id = str(uuid.uuid4())
    if imagen_b64:
        contenido = [
            {"type": "image",
             "source": {"type": "base64",
                        "media_type": imagen_tipo or "image/jpeg",
                        "data": imagen_b64}},
            {"type": "text",
             "text": texto_usuario or "Registra lo que aparece en esta foto (boleta/comprobante)."},
        ]
        texto_persistir = f"📸 {texto_usuario}".strip() if texto_usuario else "📸 (foto de boleta)"
    else:
        contenido = texto_usuario
        texto_persistir = texto_usuario
    turno = HumanMessage(content=contenido, id=msg_id)

    agent = create_react_agent(
        model=llm,
        tools=_tools_para(user_id),
        prompt=_system_prompt(user_id),
        pre_model_hook=_recortar_historial,
        checkpointer=get_checkpointer(),
    )

    config_run = {
        "configurable": {"thread_id": str(user_id)},
        "recursion_limit": 2 * MAX_TOOL_ROUNDS + 1,
    }

    try:
        resultado = agent.invoke({"messages": [turno]}, config_run)
        texto_resp = _texto_final(resultado["messages"][-1].content)
    except GraphRecursionError:
        texto_resp = "Avancé con lo que me pediste. Revisa el dashboard para confirmar 🙂"
    except anthropic.AuthenticationError as e:
        raise ExtractorError("La clave de la API de Claude es inválida o fue revocada.") from e
    except anthropic.RateLimitError as e:
        raise ExtractorError("La API está saturada. Intenta de nuevo en unos segundos.") from e
    except (anthropic.APIConnectionError, anthropic.APITimeoutError) as e:
        raise ExtractorError("No hay conexión con la API de Claude.") from e
    except anthropic.APIError as e:
        raise ExtractorError("Hubo un problema con la API de Claude. Intenta de nuevo en un momento.") from e

    if not texto_resp:
        texto_resp = "Listo 👍"

    if imagen_b64:
        agent.update_state(config_run, {"messages": [HumanMessage(content=texto_persistir, id=msg_id)]})

    db.agregar_intercambio(user_id, texto_persistir, texto_resp)
    return texto_resp


def _texto_final(content) -> str:
    """El .content de un AIMessage puede ser un string o una lista de bloques."""
    if isinstance(content, str):
        return content.strip()
    partes = [b.get("text", "") for b in content
              if isinstance(b, dict) and b.get("type") == "text"]
    return "".join(partes).strip()
