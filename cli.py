"""Interfaz de terminal del coach de gastos (opera sobre el usuario demo local).

En la app web cada usuario entra con Google; este CLI es una utilidad de
desarrollo que trabaja siempre con el usuario demo local de la base configurada.

Uso:
    python3 cli.py                 # modo conversacional (escribe gastos y comandos)
    python3 cli.py "gasté 12 lucas en almuerzo"   # registra un gasto y sale

Comandos dentro del modo conversacional:
    resumen            resumen del mes actual con comentario del coach
    lista              últimos gastos registrados
    borrar <id>        elimina un gasto
    salir / exit       terminar
"""
from __future__ import annotations

import sys
from datetime import date

from coach import coaching_mensual, formato_clp
from db import agregar_gasto, borrar_gasto, get_or_create_demo_user, init_db, ultimos_gastos
from extractor import ExtractorError, extraer_gasto


def _registrar(user_id: int, texto: str) -> None:
    try:
        g = extraer_gasto(texto)
    except ExtractorError as e:
        print(f"⚠️  {e}")
        return
    except ValueError as e:
        print(f"🤔 {e}")
        return

    if not g.get("es_gasto"):
        print("Eso no parece un gasto. Escribe algo como 'gasté 3 lucas en café'.")
        return

    gid = agregar_gasto(
        user_id, g["monto"], g["categoria"], g["descripcion"], g["fecha"], texto
    )
    print(
        f"✅ [{gid}] {formato_clp(g['monto'])} · {g['categoria']} · "
        f"{g['descripcion']} ({g['fecha']})"
    )


def _mostrar_lista(user_id: int) -> None:
    filas = ultimos_gastos(user_id)
    if not filas:
        print("Aún no hay gastos registrados.")
        return
    print("Últimos gastos:")
    for f in filas:
        print(
            f"  [{f['id']}] {f['fecha']}  {formato_clp(f['monto']):>12}  "
            f"{f['categoria']:<14} {f['descripcion']}"
        )


def _procesar_comando(user_id: int, linea: str) -> bool:
    """Devuelve False si hay que salir del bucle."""
    cmd = linea.strip()
    bajo = cmd.lower()

    if bajo in ("salir", "exit", "quit", "q"):
        return False
    if bajo in ("resumen", "resume"):
        hoy = date.today()
        print(coaching_mensual(user_id, hoy.year, hoy.month))
    elif bajo in ("lista", "list", "ls"):
        _mostrar_lista(user_id)
    elif bajo.startswith("borrar"):
        partes = cmd.split()
        if len(partes) == 2 and partes[1].isdigit():
            ok = borrar_gasto(user_id, int(partes[1]))
            print("🗑️  Borrado." if ok else "No encontré ese id.")
        else:
            print("Uso: borrar <id>")
    elif cmd:
        _registrar(user_id, cmd)
    return True


def main() -> None:
    init_db()
    user_id = get_or_create_demo_user()["id"]

    if len(sys.argv) > 1:
        _registrar(user_id, " ".join(sys.argv[1:]))
        return

    print("💰 Coach de gastos (CLI, usuario demo). Escribe un gasto, o: resumen / lista / borrar <id> / salir")
    while True:
        try:
            linea = input("› ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not _procesar_comando(user_id, linea):
            break


if __name__ == "__main__":
    main()
