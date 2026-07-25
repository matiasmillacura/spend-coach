"""Carga datos de demo (para el usuario demo local) y ver el dashboard lleno.

Uso (base separada, recomendado para no tocar tu base real):
    DATABASE_URL="sqlite:///demo.db" python3 seed_demo.py
    DATABASE_URL="sqlite:///demo.db" python3 app.py     # y abre en modo demo

Para volver a cero:  rm demo.db
"""
from __future__ import annotations

import calendar
import random
from datetime import date

import db

# Gastos "plantilla" realistas por categoría: (categoria, descripcion, min, max)
PLANTILLAS = [
    ("comida", "almuerzo", 4000, 9000),
    ("comida", "café", 2000, 4500),
    ("comida", "delivery", 7000, 15000),
    ("transporte", "metro", 800, 1800),
    ("transporte", "bencina", 20000, 40000),
    ("transporte", "uber", 3000, 8000),
    ("supermercado", "compras", 15000, 45000),
    ("servicios", "suscripción", 4990, 12990),
    ("entretenimiento", "salida", 8000, 25000),
    ("salud", "farmacia", 3000, 12000),
    ("hogar", "aseo", 5000, 18000),
    ("ropa", "ropa", 12000, 40000),
]


def _sembrar_mes(user_id: int, anio: int, mes: int, n_gastos: int, rnd: random.Random) -> None:
    dias = calendar.monthrange(anio, mes)[1]
    for _ in range(n_gastos):
        cat, desc, lo, hi = rnd.choice(PLANTILLAS)
        monto = rnd.randint(lo, hi)
        dia = rnd.randint(1, dias)
        f = f"{anio:04d}-{mes:02d}-{dia:02d}"
        db.agregar_gasto(user_id, monto, cat, desc, f, f"demo {desc}")
    # Un gasto grande de arriendo para que el "mayor gasto" tenga gracia
    db.agregar_gasto(user_id, 350000, "hogar", "arriendo", f"{anio:04d}-{mes:02d}-03", "demo arriendo")


def main() -> None:
    rnd = random.Random(42)  # semilla fija: datos reproducibles
    db.init_db()
    user_id = db.get_or_create_demo_user()["id"]
    hoy = date.today()

    # Mes anterior (para la comparación del dashboard)
    pa, pm = (hoy.year - 1, 12) if hoy.month == 1 else (hoy.year, hoy.month - 1)
    _sembrar_mes(user_id, pa, pm, 22, rnd)

    # Mes actual: repartido por todo el mes para que los gráficos se vean llenos.
    dias_mes = calendar.monthrange(hoy.year, hoy.month)[1]
    for _ in range(20):
        cat, desc, lo, hi = rnd.choice(PLANTILLAS)
        dia = rnd.randint(1, dias_mes)
        f = f"{hoy.year:04d}-{hoy.month:02d}-{dia:02d}"
        db.agregar_gasto(user_id, rnd.randint(lo, hi), cat, desc, f, f"demo {desc}")
    db.agregar_gasto(user_id, 350000, "hogar", "arriendo", f"{hoy.year:04d}-{hoy.month:02d}-03", "demo arriendo")

    print(f"Datos demo cargados para el usuario demo en: {db.config.DATABASE_URL}")
    print("Levanta el servidor con la MISMA DATABASE_URL, p. ej.:")
    print('  DATABASE_URL="sqlite:///demo.db" python3 app.py')


if __name__ == "__main__":
    main()
