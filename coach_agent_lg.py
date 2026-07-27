"""Versión LangGraph del coach — compárala lado a lado con coach_agent.py.

La diferencia central está en el bucle del agente:

  coach_agent.py (a mano)                   | coach_agent_lg.py (LangGraph)
  -------------------------------------------|--------------------------------------
  for _ in range(MAX_TOOL_ROUNDS):           | create_react_agent(...) construye un
      resp = client.messages.create(...)     | GRAFO:  agent → ¿pidió tools?
      if resp.stop_reason == "tool_use":     |            ├─ sí → nodo tools → agent
          ejecutar cada tool                 |            └─ no → fin
          reinyectar tool_result             | y agent.invoke() lo recorre solo.
      texto final; break                     |
  -------------------------------------------|--------------------------------------
  _ejecutar_tool() con un if por nombre      | ToolNode enruta por nombre de tool
  TOOLS: lista de dicts con schema a mano    | StructuredTool (aquí, un puente que
                                             | REUTILIZA esos mismos dicts)
  db.historial_mensajes() carga las últimas  | CHECKPOINTER: LangGraph persiste el
  20 líneas y se reconstruye la conversación | estado completo por thread_id y solo
  en cada turno                              | le pasamos el mensaje NUEVO
  MAX_HISTORIAL al leer de la BD             | pre_model_hook + trim_messages recorta
                                             | lo que ve el modelo en cada turno

MEMORIA — quién guarda qué:
  - Checkpointer (checkpoints.db): la memoria DEL AGENTE. Incluye lo que la BD
    nunca guardó: las llamadas a tools y sus resultados. Un "thread" por usuario.
  - Nuestra BD (tabla mensajes): el historial VISIBLE en la UI. Se sigue
    escribiendo con db.agregar_intercambio, la web no cambia.
  Nota: al estrenar el checkpointer, el agente parte con memoria fresca (las
  conversaciones antiguas viven en la BD/UI pero no en su estado).

OBSERVABILIDAD — LangSmith:
  No requiere código: langchain-core emite trazas solo si el entorno lo pide.
  En .env:  LANGSMITH_TRACING=true + LANGSMITH_API_KEY + LANGSMITH_PROJECT.
  Cada responder() aparece como un árbol: agente → llamadas al modelo → tools,
  con tokens, latencia y errores por paso. Cuenta gratis: smith.langchain.com

Lo que NO cambia (lógica de negocio, siempre tuya): el system prompt con el
snapshot financiero real, qué hace cada herramienta contra la BD, y el manejo
de errores con mensajes amables (no un 500 de Flask).

Para activarlo en la app:  COACH_ENGINE=langgraph python app.py
"""
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
# Mismo dominio del original (tools, system prompt, límites); solo cambia quién
# ejecuta el bucle y quién recuerda.
from coach_agent import MAX_HISTORIAL, MAX_TOOL_ROUNDS, TOOLS, _ejecutar_tool, _system_prompt
from config import config
from extractor import ExtractorError


# --- modelo ------------------------------------------------------------------

_llm = None


def get_llm() -> ChatAnthropic:
    """Chat model de LangChain sobre Claude (mismo modelo que el coach original)."""
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


# --- memoria: checkpointer ----------------------------------------------------

_checkpointer = None


def get_checkpointer():
    """Persistencia de estado de LangGraph, elegida según DATABASE_URL.

    Guarda por `thread_id` todo el estado del grafo (mensajes, tool calls y
    results); por eso ya no se carga historial a mano.

    - Desarrollo (SQLite): archivo aparte de la BD (COACH_CHECKPOINT_DB).
      check_same_thread=False porque Flask atiende cada request en un hilo
      distinto; SqliteSaver serializa el acceso con su propio lock.
    - Producción (Postgres): mismas tablas de la BD principal, vía pool de
      conexiones (los hilos de gunicorn no pueden compartir una conexión).
      setup() crea las tablas del checkpointer si no existen (idempotente).
    """
    global _checkpointer
    if _checkpointer is None:
        if config.DATABASE_URL.startswith("sqlite"):
            conn = sqlite3.connect(config.CHECKPOINT_DB, check_same_thread=False)
            _checkpointer = SqliteSaver(conn)
        else:
            from langgraph.checkpoint.postgres import PostgresSaver
            from psycopg_pool import ConnectionPool

            # PostgresSaver espera una URI libpq, sin el "+psycopg" de SQLAlchemy.
            url = config.DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)
            pool = ConnectionPool(url, min_size=1, max_size=4,
                                  kwargs={"autocommit": True, "prepare_threshold": 0})
            _checkpointer = PostgresSaver(pool)
            _checkpointer.setup()
    return _checkpointer


def _recortar_historial(state):
    """pre_model_hook: recorta lo que VE el modelo sin tocar lo persistido.

    token_counter=len cuenta mensajes (paridad con MAX_HISTORIAL del original);
    start_on="human" evita que la ventana empiece en un tool_result huérfano
    (la API de Claude lo rechaza); devolver "llm_input_messages" en vez de
    "messages" deja intacto el estado guardado.
    """
    recortados = trim_messages(
        state["messages"],
        strategy="last",
        token_counter=len,
        max_tokens=MAX_HISTORIAL,
        start_on="human",
        include_system=False,   # el system va aparte (prompt=)
        allow_partial=False,
    )
    return {"llm_input_messages": recortados}


# --- puente de herramientas ---------------------------------------------------

def _tools_para(user_id: int) -> list[StructuredTool]:
    """Re-empaqueta los TOOLS del agente original como StructuredTool.

    El closure liga cada tool al user_id del request: el modelo jamás decide
    sobre qué usuario opera (igual que en el original).
    """
    def _hacer_func(nombre: str):
        def _run(**kwargs) -> str:
            out = _ejecutar_tool(user_id, nombre, kwargs)  # nunca lanza: {'error': ...}
            return json.dumps(out, ensure_ascii=False)     # el tool_result que ve el modelo
        return _run

    return [
        StructuredTool(
            name=spec["name"],
            description=spec["description"],
            args_schema=spec["input_schema"],  # el mismo dict JSON Schema de siempre
            func=_hacer_func(spec["name"]),
        )
        for spec in TOOLS
    ]


# --- el agente ----------------------------------------------------------------

def responder(user_id: int, texto_usuario: str,
              imagen_b64: str | None = None, imagen_tipo: str | None = None) -> str:
    """Misma firma y contrato que coach_agent.responder — la app no nota la diferencia."""
    texto_usuario = (texto_usuario or "").strip()
    if not texto_usuario and not imagen_b64:
        return "Cuéntame algo 🙂"

    llm = get_llm()  # lanza ExtractorError si no hay clave

    # Turno actual: texto, o imagen+texto en bloques nativos de la API de Claude.
    # El id fijo permite editar este mensaje en el estado después (ver abajo).
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

    # El grafo reemplaza el bucle manual de tool-use de coach_agent.py.
    agent = create_react_agent(
        model=llm,
        tools=_tools_para(user_id),
        prompt=_system_prompt(user_id),      # el system con el snapshot real
        pre_model_hook=_recortar_historial,  # ventana de contexto acotada
        checkpointer=get_checkpointer(),     # memoria persistente por thread
    )

    # thread_id = usuario: solo se envía el mensaje nuevo, el resto lo aporta el checkpointer.
    config_run = {
        "configurable": {"thread_id": str(user_id)},
        # Equivalente a MAX_TOOL_ROUNDS: 2 pasos por vuelta (agent + tools) +1 final.
        "recursion_limit": 2 * MAX_TOOL_ROUNDS + 1,
    }

    try:
        resultado = agent.invoke({"messages": [turno]}, config_run)
        texto_resp = _texto_final(resultado["messages"][-1].content)
    except GraphRecursionError:
        # Vueltas agotadas: no invitar a repetir — ya corrieron tools y repetir duplicaría registros.
        texto_resp = "Avancé con lo que me pediste. Revisa el dashboard para confirmar 🙂"
    # LangChain usa el SDK anthropic por debajo: mismas excepciones.
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

    # La imagen NO se re-envía en turnos futuros: se sustituye en el estado por
    # su marcador de texto (update_state con el mismo id = reemplazar, no agregar).
    if imagen_b64:
        agent.update_state(config_run, {"messages": [HumanMessage(content=texto_persistir, id=msg_id)]})

    # La BD sigue siendo el historial VISIBLE (la UI lee de aquí; no cambia).
    db.agregar_intercambio(user_id, texto_persistir, texto_resp)
    return texto_resp


def _texto_final(content) -> str:
    """El .content de un AIMessage puede ser un string o una lista de bloques."""
    if isinstance(content, str):
        return content.strip()
    partes = [b.get("text", "") for b in content
              if isinstance(b, dict) and b.get("type") == "text"]
    return "".join(partes).strip()
