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
# Reutilizamos el "dominio" del agente original: mismas tools, mismo system
# prompt, mismos límites. Solo cambia QUIÉN ejecuta el bucle y QUIÉN recuerda.
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

_checkpointer: SqliteSaver | None = None


def get_checkpointer() -> SqliteSaver:
    """Persistencia de estado de LangGraph en SQLite (un archivo aparte de la BD).

    El checkpointer guarda, por `thread_id`, TODO el estado del grafo tras cada
    paso: mensajes, tool calls y tool results. Por eso ya no cargamos historial
    a mano — al invocar con el mismo thread_id, LangGraph "recuerda" solo.

    check_same_thread=False: Flask atiende cada request en un hilo distinto y
    SqliteSaver serializa el acceso con su propio lock.
    En producción (Postgres) el equivalente es PostgresSaver sobre DATABASE_URL.
    """
    global _checkpointer
    if _checkpointer is None:
        conn = sqlite3.connect(config.CHECKPOINT_DB, check_same_thread=False)
        _checkpointer = SqliteSaver(conn)
    return _checkpointer


def _recortar_historial(state):
    """pre_model_hook: se ejecuta ANTES de cada llamada al modelo.

    El checkpointer acumula la conversación completa (es su gracia), pero
    enviarla entera a Claude en cada turno sería cada vez más caro. Este hook
    recorta LO QUE VE EL MODELO sin tocar lo persistido:

    - token_counter=len → contamos mensajes (paridad con MAX_HISTORIAL=20 del
      original). En producción se contaría tokens reales con el modelo.
    - strategy="last" → conserva los más recientes.
    - start_on="human" → nunca deja la ventana empezando en un tool_result
      huérfano (la API de Claude lo rechazaría). Es el equivalente del
      `while messages[0].role != "user": pop(0)` del original.
    - Devolver "llm_input_messages" (y no "messages") = solo cambia la ENTRADA
      del modelo; el estado guardado queda intacto.
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
    """Convierte los TOOLS del agente original en tools de LangChain.

    Lección clave: una "tool" en cualquier framework es solo
    (nombre + descripción + schema de argumentos + función que la ejecuta).
    Ya teníamos las cuatro cosas; esto solo las re-empaqueta.

    Nota el closure: cada tool queda ligada al user_id del request, así el
    modelo jamás decide sobre qué usuario opera (igual que en el original).
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

    # Turno actual: texto simple, o imagen+texto (bloques en formato nativo de
    # la API de Claude; langchain-anthropic los pasa tal cual). Le fijamos un id
    # para poder editar este mensaje en el estado después (ver más abajo).
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

    # AQUÍ desaparece el bucle manual: este grafo ES el `for` de coach_agent.py.
    agent = create_react_agent(
        model=llm,
        tools=_tools_para(user_id),
        prompt=_system_prompt(user_id),      # el system con el snapshot real
        pre_model_hook=_recortar_historial,  # ventana de contexto acotada
        checkpointer=get_checkpointer(),     # memoria persistente por thread
    )

    # thread_id = usuario: cada usuario es una conversación continua. Fíjate en
    # que SOLO enviamos el mensaje nuevo — el resto lo aporta el checkpointer.
    config_run = {
        "configurable": {"thread_id": str(user_id)},
        # Equivalente a MAX_TOOL_ROUNDS: cada vuelta consume 2 pasos del grafo
        # (agent + tools), +1 por la respuesta final.
        "recursion_limit": 2 * MAX_TOOL_ROUNDS + 1,
    }

    try:
        resultado = agent.invoke({"messages": [turno]}, config_run)
        texto_resp = _texto_final(resultado["messages"][-1].content)
    except GraphRecursionError:
        # Se agotaron las vueltas (el `else` del for original): no invitar a
        # repetir — ya se ejecutaron herramientas y repetir duplicaría registros.
        texto_resp = "Avancé con lo que me pediste. Revisa el dashboard para confirmar 🙂"
    # LangChain usa el SDK anthropic por debajo: mismas excepciones, mismo trato.
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

    # Fotos: igual que el original, la imagen NO se re-envía en turnos futuros.
    # Como el checkpointer la dejó guardada en el estado, la reemplazamos por su
    # marcador de texto. update_state usa el reducer de mensajes: mismo id =
    # sustituir, no agregar. (Editar el pasado del thread — esto es lo que un
    # checkpointer permite y una lista de mensajes a mano no.)
    if imagen_b64:
        agent.update_state(config_run, {"messages": [HumanMessage(content=texto_persistir, id=msg_id)]})

    # La BD sigue siendo el historial VISIBLE (la UI lee de aquí; no cambia).
    db.agregar_intercambio(user_id, texto_persistir, texto_resp)
    return texto_resp


def _texto_final(content) -> str:
    """El .content de un AIMessage puede ser un string o una lista de bloques
    (equivale al `"".join(b.text for b in resp.content if b.type=="text")` original)."""
    if isinstance(content, str):
        return content.strip()
    partes = [b.get("text", "") for b in content
              if isinstance(b, dict) and b.get("type") == "text"]
    return "".join(partes).strip()
