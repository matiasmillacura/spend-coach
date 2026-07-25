"""Capa de datos multiusuario con SQLAlchemy.

Portable entre SQLite (desarrollo local) y PostgreSQL (producción): se evita
cualquier función de fecha específica del motor (nada de strftime/to_char).
Los rangos de mes se calculan en Python y se filtran con comparaciones de fecha,
que funcionan igual en ambos.

TODA consulta de gastos está acotada por user_id: un usuario solo ve y borra
lo suyo. Esa es la garantía central del modo multiusuario.
"""
from __future__ import annotations

import calendar
from contextlib import contextmanager
from datetime import date, datetime
from typing import Iterator

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    func,
    inspect as sqla_inspect,
    select,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from config import config

# Categorías permitidas. El extractor debe mapear cada gasto a una de estas.
CATEGORIAS = [
    "comida",          # almuerzos, café, delivery, restaurantes
    "supermercado",    # compras de despensa
    "transporte",      # bencina, micro, metro, uber, estacionamiento
    "servicios",       # luz, agua, gas, internet, suscripciones
    "salud",           # farmacia, médico, gimnasio
    "entretenimiento", # cine, salidas, juegos, streaming
    "hogar",           # arriendo, muebles, aseo, reparaciones
    "ropa",            # vestuario y calzado
    "educacion",       # cursos, libros, colegiatura
    "otros",           # lo que no calce en lo anterior
]

# Clasificación de cada categoría para la regla 50/30/20.
# "necesidades" = gasto difícil de evitar; "deseos" = gasto discrecional.
# (El ahorro no es una categoría de gasto: se mide aparte.)
GRUPO_CATEGORIA = {
    "supermercado": "necesidades",
    "transporte": "necesidades",
    "servicios": "necesidades",
    "salud": "necesidades",
    "hogar": "necesidades",
    "educacion": "necesidades",
    "comida": "deseos",          # restaurantes/café/delivery (la despensa va en 'supermercado')
    "entretenimiento": "deseos",
    "ropa": "deseos",
    "otros": "deseos",
}

# Pasos del onboarding conversacional, en orden.
ONBOARDING_PASOS = ["nombre", "nacimiento", "ingresos", "meta", "completo"]

# Regla de presupuesto por defecto (necesidades / deseos / ahorro).
REGLA_DEFAULT = {"pct_necesidades": 50, "pct_deseos": 30, "pct_ahorro": 20}

# check_same_thread solo aplica a SQLite (servidor multi-hilo).
_engine_kwargs = {"pool_pre_ping": True}
if config.DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(config.DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # 'sub' de Google (identificador estable de la cuenta). Único cuando existe.
    google_sub: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    nombre: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    foto_url: Mapped[str | None] = mapped_column(String(1000))
    # Perfil financiero (se completa en el onboarding conversacional).
    fecha_nacimiento: Mapped[date | None] = mapped_column(Date)
    onboarding_paso: Mapped[str] = mapped_column(String(20), nullable=False, default="nombre")
    onboarding_completo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Última semana ISO (YYYY-Www) en que se generó el resumen semanal proactivo.
    ultima_semana_resumen: Mapped[str | None] = mapped_column(String(10))
    # Punto de partida para quien llega a MITAD de mes: cuánta plata tenía
    # disponible ese día. Evita obligar a reconstruir todo el mes hacia atrás.
    saldo_inicial: Mapped[int | None] = mapped_column(Integer)
    saldo_inicial_fecha: Mapped[date | None] = mapped_column(Date)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    gastos: Mapped[list["Gasto"]] = relationship(back_populates="user")


class Gasto(Base):
    __tablename__ = "gastos"
    __table_args__ = (
        CheckConstraint("monto > 0", name="ck_monto_positivo"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    monto: Mapped[int] = mapped_column(Integer, nullable=False)          # pesos chilenos (CLP)
    categoria: Mapped[str] = mapped_column(String(20), nullable=False)
    descripcion: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    texto_orig: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="gastos")


class IngresoFijo(Base):
    """Ingreso recurrente (ej. sueldo): cuenta automáticamente cada mes."""
    __tablename__ = "ingresos_fijos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    descripcion: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    monto: Mapped[int] = mapped_column(Integer, nullable=False)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class GastoFijo(Base):
    """Gasto recurrente mensual (suscripciones, arriendo, plan del celular):
    cuenta automáticamente cada mes, igual que los ingresos fijos."""
    __tablename__ = "gastos_fijos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    descripcion: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    monto: Mapped[int] = mapped_column(Integer, nullable=False)
    categoria: Mapped[str] = mapped_column(String(20), nullable=False, default="servicios")
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PresupuestoCategoria(Base):
    """Tope mensual por categoría de gasto (para alertas de presupuesto)."""
    __tablename__ = "presupuestos_categoria"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    categoria: Mapped[str] = mapped_column(String(20), primary_key=True)
    monto: Mapped[int] = mapped_column(Integer, nullable=False)
    actualizado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Ingreso(Base):
    """Ingreso variable/eventual (freelance, ventas, bono): se anota por fecha."""
    __tablename__ = "ingresos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    monto: Mapped[int] = mapped_column(Integer, nullable=False)
    descripcion: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    texto_orig: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MetaAhorro(Base):
    """Meta de ahorro con propósito. tipo ∈ monto_fecha | monto_mensual | porcentaje."""
    __tablename__ = "metas_ahorro"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    nombre: Mapped[str] = mapped_column(String(120), nullable=False)        # propósito: "vacaciones", "fondo emergencia"
    tipo: Mapped[str] = mapped_column(String(20), nullable=False, default="monto_fecha")
    monto_objetivo: Mapped[int | None] = mapped_column(Integer)             # tipo monto_fecha
    fecha_objetivo: Mapped[date | None] = mapped_column(Date)               # tipo monto_fecha
    monto_mensual: Mapped[int | None] = mapped_column(Integer)              # tipo monto_mensual
    porcentaje: Mapped[int | None] = mapped_column(Integer)                 # tipo porcentaje (del ingreso)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Ahorro(Base):
    """Aporte de ahorro, opcionalmente ligado a una meta (su propósito)."""
    __tablename__ = "ahorros"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    monto: Mapped[int] = mapped_column(Integer, nullable=False)
    fecha: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    meta_id: Mapped[int | None] = mapped_column(ForeignKey("metas_ahorro.id", ondelete="SET NULL"))
    texto_orig: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ReglaPresupuesto(Base):
    """Regla 50/30/20 configurable por usuario (necesidades/deseos/ahorro). Una por usuario."""
    __tablename__ = "regla_presupuesto"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    pct_necesidades: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    pct_deseos: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    pct_ahorro: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    actualizado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Mensaje(Base):
    """Historial del chat de coaching (memoria de la conversación)."""
    __tablename__ = "mensajes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    rol: Mapped[str] = mapped_column(String(12), nullable=False)   # 'user' | 'assistant'
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def _migrar_legacy_si_aplica() -> None:
    """Migra bases de la versión anterior (tabla 'gastos' SIN user_id) preservando
    los datos: crea el usuario demo local y le asigna los gastos existentes.
    Solo aplica a SQLite antiguo; en una base nueva o en Postgres no hace nada."""
    insp = sqla_inspect(engine)
    if not insp.has_table("gastos"):
        return
    cols = [c["name"] for c in insp.get_columns("gastos")]
    if "user_id" in cols:
        return  # ya está en el esquema nuevo
    Base.metadata.create_all(engine)          # asegura la tabla 'users'
    owner = get_or_create_demo_user()
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE gastos ADD COLUMN user_id INTEGER"))
        n = conn.execute(
            text("UPDATE gastos SET user_id = :uid WHERE user_id IS NULL"),
            {"uid": owner["id"]},
        ).rowcount
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_gastos_user_id ON gastos (user_id)"))
    print(f"↻ Migración: {n} gasto(s) de la versión anterior asignados al usuario demo local.")


def init_db() -> None:
    """Crea las tablas si no existen (migrando bases antiguas). Idempotente y
    tolerante a carreras entre workers en el primer arranque (gunicorn)."""
    _migrar_legacy_si_aplica()
    try:
        Base.metadata.create_all(engine)
    except Exception:
        # Otro worker pudo crear las tablas al mismo tiempo. Si ya existen, seguimos;
        # si no, el error es real y se propaga.
        if not sqla_inspect(engine).has_table("users"):
            raise
    # Mini-migración: columnas agregadas a tablas ya existentes (create_all no las añade).
    insp = sqla_inspect(engine)
    if insp.has_table("users"):
        cols = [c["name"] for c in insp.get_columns("users")]
        faltantes = {
            "ultima_semana_resumen": "VARCHAR(10)",
            "saldo_inicial": "INTEGER",
            "saldo_inicial_fecha": "DATE",
        }
        with engine.begin() as conn:
            for col, tipo in faltantes.items():
                if col not in cols:
                    conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {tipo}"))


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transacción por bloque: commit al salir, rollback si hay excepción."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


# --- utilidades internas -----------------------------------------------------

def _rango_mes(anio: int, mes: int) -> tuple[date, date]:
    """Primer y último día del mes (para filtrar por rango, sin funciones de motor)."""
    ultimo = calendar.monthrange(anio, mes)[1]
    return date(anio, mes, 1), date(anio, mes, ultimo)


def _a_fecha(fecha) -> date:
    if isinstance(fecha, date):
        return fecha
    return date.fromisoformat(str(fecha))


# --- usuarios ----------------------------------------------------------------

def get_or_create_user(
    google_sub: str, email: str, nombre: str = "", foto_url: str | None = None
) -> dict:
    """Busca al usuario por su 'sub' de Google; lo crea si es la primera vez.
    Devuelve un dict con los datos del usuario (id incluido).

    Tolera la carrera de dos primeros logins concurrentes del mismo usuario: si el
    INSERT choca con la restricción única de google_sub, reintenta el SELECT."""
    for intento in range(2):
        try:
            with session_scope() as s:
                u = s.scalar(select(User).where(User.google_sub == google_sub))
                if u is None:
                    u = User(google_sub=google_sub, email=email, nombre=nombre, foto_url=foto_url)
                    s.add(u)
                    s.flush()  # asigna el id sin cerrar la transacción
                else:
                    # Email/foto se refrescan desde Google, pero el nombre NO pisa
                    # el apodo elegido en el onboarding (solo se usa si está vacío).
                    u.email, u.foto_url = email, foto_url
                    if not u.nombre:
                        u.nombre = nombre
                return {"id": u.id, "email": u.email, "nombre": u.nombre, "foto_url": u.foto_url}
        except IntegrityError:
            if intento == 0:
                continue  # otro request lo creó entre el SELECT y el INSERT: reintenta
            raise


def get_user(user_id: int) -> dict | None:
    """Datos básicos del usuario para la UI. None si no existe."""
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is None:
            return None
        return {"id": u.id, "email": u.email, "nombre": u.nombre, "foto_url": u.foto_url}


def get_or_create_demo_user() -> dict:
    """Usuario único para el 'modo demo' local (sin OAuth configurado)."""
    with session_scope() as s:
        u = s.scalar(select(User).where(User.email == "demo@local"))
        if u is None:
            # nombre vacío a propósito: así el onboarding conversacional te pide el nombre.
            u = User(google_sub=None, email="demo@local", nombre="")
            s.add(u)
            s.flush()
        return {"id": u.id, "email": u.email, "nombre": u.nombre, "foto_url": u.foto_url}


# --- gastos (todo acotado por user_id) --------------------------------------

def agregar_gasto(
    user_id: int, monto: int, categoria: str, descripcion: str, fecha, texto_orig: str
) -> int:
    """Inserta un gasto del usuario y devuelve su id."""
    monto = int(monto)
    if monto <= 0:
        raise ValueError("El monto debe ser mayor que 0.")
    if categoria not in CATEGORIAS:
        categoria = "otros"
    with session_scope() as s:
        g = Gasto(
            user_id=user_id,
            monto=monto,
            categoria=categoria,
            descripcion=(descripcion or "")[:200],
            fecha=_a_fecha(fecha),
            texto_orig=(texto_orig or "")[:500],
        )
        s.add(g)
        s.flush()
        return g.id


def ultimos_gastos(user_id: int, limite: int = 50) -> list[dict]:
    with session_scope() as s:
        filas = s.scalars(
            select(Gasto)
            .where(Gasto.user_id == user_id)
            .order_by(Gasto.fecha.desc(), Gasto.id.desc())
            .limit(limite)
        ).all()
        return [_gasto_dict(g) for g in filas]


def borrar_gasto(user_id: int, gasto_id: int) -> bool:
    """Borra un gasto por id SOLO si pertenece al usuario. True si existía."""
    with session_scope() as s:
        g = s.get(Gasto, gasto_id)
        if g is None or g.user_id != user_id:
            return False
        s.delete(g)
        return True


def resumen_mes(user_id: int, anio: int, mes: int) -> dict:
    """Totales del mes del usuario: total general y desglose por categoría."""
    ini, fin = _rango_mes(anio, mes)
    with session_scope() as s:
        filas = s.execute(
            select(Gasto.categoria, func.sum(Gasto.monto), func.count())
            .where(Gasto.user_id == user_id, Gasto.fecha >= ini, Gasto.fecha <= fin)
            .group_by(Gasto.categoria)
            .order_by(func.sum(Gasto.monto).desc())
        ).all()
    por_categoria = {cat: {"total": int(total), "n": int(n)} for cat, total, n in filas}
    total = sum(c["total"] for c in por_categoria.values())
    return {"mes": f"{anio:04d}-{mes:02d}", "total": total, "por_categoria": por_categoria}


def gastos_del_mes(user_id: int, anio: int, mes: int) -> list[dict]:
    ini, fin = _rango_mes(anio, mes)
    with session_scope() as s:
        filas = s.scalars(
            select(Gasto)
            .where(Gasto.user_id == user_id, Gasto.fecha >= ini, Gasto.fecha <= fin)
            .order_by(Gasto.fecha)
        ).all()
        return [_gasto_dict(g) for g in filas]


def serie_diaria(user_id: int, anio: int, mes: int) -> dict[str, int]:
    """Total gastado por día del mes: {'YYYY-MM-DD': monto}."""
    ini, fin = _rango_mes(anio, mes)
    with session_scope() as s:
        filas = s.execute(
            select(Gasto.fecha, func.sum(Gasto.monto))
            .where(Gasto.user_id == user_id, Gasto.fecha >= ini, Gasto.fecha <= fin)
            .group_by(Gasto.fecha)
            .order_by(Gasto.fecha)
        ).all()
    return {f.isoformat(): int(total) for f, total in filas}


def mayor_gasto(user_id: int, anio: int, mes: int) -> dict | None:
    """El gasto individual más grande del mes del usuario."""
    ini, fin = _rango_mes(anio, mes)
    with session_scope() as s:
        g = s.scalars(
            select(Gasto)
            .where(Gasto.user_id == user_id, Gasto.fecha >= ini, Gasto.fecha <= fin)
            .order_by(Gasto.monto.desc())
            .limit(1)
        ).first()
        return _gasto_dict(g) if g else None


def _gasto_dict(g: Gasto) -> dict:
    return {
        "id": g.id,
        "monto": g.monto,
        "categoria": g.categoria,
        "descripcion": g.descripcion,
        "fecha": g.fecha.isoformat(),
        "texto_orig": g.texto_orig,
    }


# --- perfil y onboarding -----------------------------------------------------

def _edad(nac: date | None, hoy: date | None = None) -> int | None:
    if nac is None:
        return None
    hoy = hoy or date.today()
    return hoy.year - nac.year - ((hoy.month, hoy.day) < (nac.month, nac.day))


def guardar_perfil(user_id: int, nombre: str | None = None, fecha_nacimiento=None) -> dict:
    """Actualiza nombre y/o fecha de nacimiento del usuario."""
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is None:
            raise ValueError("Usuario no encontrado.")
        if nombre:
            u.nombre = str(nombre).strip()[:200]
        if fecha_nacimiento is not None:
            u.fecha_nacimiento = _a_fecha(fecha_nacimiento)
        return _perfil_dict(u)


def set_onboarding(user_id: int, paso: str | None = None, completo: bool | None = None) -> None:
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is None:
            return
        if paso is not None:
            u.onboarding_paso = paso
        if completo is not None:
            u.onboarding_completo = completo
            if completo:
                u.onboarding_paso = "completo"


def get_perfil(user_id: int) -> dict | None:
    with session_scope() as s:
        u = s.get(User, user_id)
        return _perfil_dict(u) if u else None


def set_saldo_inicial(user_id: int, monto: int, fecha=None) -> None:
    """Guarda el punto de partida de quien llega a mitad de mes: cuánta plata
    tenía disponible ese día (el dashboard arranca desde ahí, sin mes hacia atrás)."""
    monto = int(monto)
    if monto < 0:
        raise ValueError("El saldo no puede ser negativo.")
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is None:
            raise ValueError("Usuario no encontrado.")
        u.saldo_inicial = monto
        u.saldo_inicial_fecha = _a_fecha(fecha) if fecha else date.today()


def _perfil_dict(u: User) -> dict:
    return {
        "id": u.id,
        "nombre": u.nombre,
        "email": u.email,
        "foto_url": u.foto_url,
        "fecha_nacimiento": u.fecha_nacimiento.isoformat() if u.fecha_nacimiento else None,
        "edad": _edad(u.fecha_nacimiento),
        "onboarding_paso": u.onboarding_paso,
        "onboarding_completo": u.onboarding_completo,
        "saldo_inicial": u.saldo_inicial,
        "saldo_inicial_fecha": u.saldo_inicial_fecha.isoformat() if u.saldo_inicial_fecha else None,
    }


# --- ingresos fijos (recurrentes) -------------------------------------------

def agregar_ingreso_fijo(user_id: int, descripcion: str, monto: int) -> int:
    monto = int(monto)
    if monto <= 0:
        raise ValueError("El monto del ingreso debe ser mayor que 0.")
    with session_scope() as s:
        i = IngresoFijo(user_id=user_id, descripcion=(descripcion or "ingreso")[:200], monto=monto)
        s.add(i)
        s.flush()
        return i.id


def listar_ingresos_fijos(user_id: int, solo_activos: bool = True) -> list[dict]:
    with session_scope() as s:
        q = select(IngresoFijo).where(IngresoFijo.user_id == user_id)
        if solo_activos:
            q = q.where(IngresoFijo.activo.is_(True))
        filas = s.scalars(q.order_by(IngresoFijo.monto.desc())).all()
        return [{"id": i.id, "descripcion": i.descripcion, "monto": i.monto, "activo": i.activo} for i in filas]


def borrar_ingreso_fijo(user_id: int, ingreso_id: int) -> bool:
    with session_scope() as s:
        i = s.get(IngresoFijo, ingreso_id)
        if i is None or i.user_id != user_id:
            return False
        s.delete(i)
        return True


def total_ingreso_fijo(user_id: int) -> int:
    with session_scope() as s:
        t = s.scalar(
            select(func.coalesce(func.sum(IngresoFijo.monto), 0))
            .where(IngresoFijo.user_id == user_id, IngresoFijo.activo.is_(True))
        )
        return int(t or 0)


# --- gastos fijos / suscripciones (recurrentes) ------------------------------

def agregar_gasto_fijo(user_id: int, descripcion: str, monto: int, categoria: str = "servicios") -> int:
    monto = int(monto)
    if monto <= 0:
        raise ValueError("El monto debe ser mayor que 0.")
    if categoria not in CATEGORIAS:
        categoria = "servicios"
    with session_scope() as s:
        g = GastoFijo(user_id=user_id, descripcion=(descripcion or "gasto fijo")[:200],
                      monto=monto, categoria=categoria)
        s.add(g)
        s.flush()
        return g.id


def listar_gastos_fijos(user_id: int, solo_activos: bool = True) -> list[dict]:
    with session_scope() as s:
        q = select(GastoFijo).where(GastoFijo.user_id == user_id)
        if solo_activos:
            q = q.where(GastoFijo.activo.is_(True))
        filas = s.scalars(q.order_by(GastoFijo.monto.desc())).all()
        return [{"id": g.id, "descripcion": g.descripcion, "monto": g.monto,
                 "categoria": g.categoria, "activo": g.activo} for g in filas]


def borrar_gasto_fijo(user_id: int, gasto_id: int) -> bool:
    with session_scope() as s:
        g = s.get(GastoFijo, gasto_id)
        if g is None or g.user_id != user_id:
            return False
        s.delete(g)
        return True


def editar_gasto_fijo(user_id: int, gasto_id: int, monto=None, descripcion=None,
                      categoria=None, activo=None) -> bool:
    with session_scope() as s:
        g = s.get(GastoFijo, gasto_id)
        if g is None or g.user_id != user_id:
            return False
        if monto is not None:
            m = int(monto)
            if m <= 0:
                raise ValueError("El monto debe ser mayor que 0.")
            g.monto = m
        if descripcion:
            g.descripcion = str(descripcion)[:200]
        if categoria:
            g.categoria = categoria if categoria in CATEGORIAS else g.categoria
        if activo is not None:
            g.activo = bool(activo)
        return True


def total_gasto_fijo(user_id: int) -> int:
    with session_scope() as s:
        t = s.scalar(
            select(func.coalesce(func.sum(GastoFijo.monto), 0))
            .where(GastoFijo.user_id == user_id, GastoFijo.activo.is_(True))
        )
        return int(t or 0)


# --- ingresos variables (por evento) ----------------------------------------

def registrar_ingreso(user_id: int, monto: int, descripcion: str, fecha, texto_orig: str = "") -> int:
    monto = int(monto)
    if monto <= 0:
        raise ValueError("El monto del ingreso debe ser mayor que 0.")
    with session_scope() as s:
        i = Ingreso(
            user_id=user_id, monto=monto,
            descripcion=(descripcion or "ingreso")[:200],
            fecha=_a_fecha(fecha), texto_orig=(texto_orig or "")[:500],
        )
        s.add(i)
        s.flush()
        return i.id


def ingresos_variables_del_mes(user_id: int, anio: int, mes: int) -> list[dict]:
    ini, fin = _rango_mes(anio, mes)
    with session_scope() as s:
        filas = s.scalars(
            select(Ingreso)
            .where(Ingreso.user_id == user_id, Ingreso.fecha >= ini, Ingreso.fecha <= fin)
            .order_by(Ingreso.fecha.desc())
        ).all()
        return [{"id": i.id, "monto": i.monto, "descripcion": i.descripcion, "fecha": i.fecha.isoformat()} for i in filas]


def total_ingresos_variables_mes(user_id: int, anio: int, mes: int) -> int:
    ini, fin = _rango_mes(anio, mes)
    with session_scope() as s:
        t = s.scalar(
            select(func.coalesce(func.sum(Ingreso.monto), 0))
            .where(Ingreso.user_id == user_id, Ingreso.fecha >= ini, Ingreso.fecha <= fin)
        )
        return int(t or 0)


def ingreso_mensual_total(user_id: int, anio: int, mes: int) -> int:
    """Ingreso del mes = fijos activos + variables anotados en el mes."""
    return total_ingreso_fijo(user_id) + total_ingresos_variables_mes(user_id, anio, mes)


# --- metas de ahorro ---------------------------------------------------------

def crear_meta(user_id: int, nombre: str, tipo: str = "monto_fecha", monto_objetivo=None,
               fecha_objetivo=None, monto_mensual=None, porcentaje=None) -> int:
    if tipo not in ("monto_fecha", "monto_mensual", "porcentaje"):
        tipo = "monto_fecha"
    with session_scope() as s:
        m = MetaAhorro(
            user_id=user_id, nombre=(nombre or "ahorro")[:120], tipo=tipo,
            monto_objetivo=int(monto_objetivo) if monto_objetivo else None,
            fecha_objetivo=_a_fecha(fecha_objetivo) if fecha_objetivo else None,
            monto_mensual=int(monto_mensual) if monto_mensual else None,
            porcentaje=int(porcentaje) if porcentaje else None,
        )
        s.add(m)
        s.flush()
        return m.id


def actualizar_meta(user_id: int, meta_id: int, **campos) -> bool:
    permitidos = {"nombre", "tipo", "monto_objetivo", "fecha_objetivo", "monto_mensual", "porcentaje", "activo"}
    with session_scope() as s:
        m = s.get(MetaAhorro, meta_id)
        if m is None or m.user_id != user_id:
            return False
        for k, v in campos.items():
            if k not in permitidos:
                continue
            if k == "fecha_objetivo" and v is not None:
                v = _a_fecha(v)
            setattr(m, k, v)
        return True


def buscar_meta_por_nombre(user_id: int, nombre: str) -> dict | None:
    """Busca una meta activa cuyo nombre coincida (case-insensitive, contiene)."""
    n = (nombre or "").strip().lower()
    if not n:
        return None
    with session_scope() as s:
        filas = s.scalars(
            select(MetaAhorro).where(MetaAhorro.user_id == user_id, MetaAhorro.activo.is_(True))
        ).all()
        for m in filas:
            if n in m.nombre.lower() or m.nombre.lower() in n:
                return _meta_dict(m)
    return None


def listar_metas(user_id: int, solo_activas: bool = True) -> list[dict]:
    with session_scope() as s:
        q = select(MetaAhorro).where(MetaAhorro.user_id == user_id)
        if solo_activas:
            q = q.where(MetaAhorro.activo.is_(True))
        filas = s.scalars(q.order_by(MetaAhorro.creado_en)).all()
        return [_meta_dict(m) for m in filas]


def _meta_dict(m: MetaAhorro) -> dict:
    return {
        "id": m.id, "nombre": m.nombre, "tipo": m.tipo,
        "monto_objetivo": m.monto_objetivo,
        "fecha_objetivo": m.fecha_objetivo.isoformat() if m.fecha_objetivo else None,
        "monto_mensual": m.monto_mensual, "porcentaje": m.porcentaje, "activo": m.activo,
    }


# --- ahorros -----------------------------------------------------------------

def registrar_ahorro(user_id: int, monto: int, fecha, meta_id: int | None = None, texto_orig: str = "") -> int:
    monto = int(monto)
    if monto <= 0:
        raise ValueError("El monto del ahorro debe ser mayor que 0.")
    with session_scope() as s:
        # Valida que la meta (si viene) sea del usuario.
        if meta_id is not None:
            m = s.get(MetaAhorro, meta_id)
            if m is None or m.user_id != user_id:
                meta_id = None
        a = Ahorro(user_id=user_id, monto=monto, fecha=_a_fecha(fecha), meta_id=meta_id,
                   texto_orig=(texto_orig or "")[:500])
        s.add(a)
        s.flush()
        return a.id


def total_ahorrado_meta(user_id: int, meta_id: int) -> int:
    with session_scope() as s:
        t = s.scalar(
            select(func.coalesce(func.sum(Ahorro.monto), 0))
            .where(Ahorro.user_id == user_id, Ahorro.meta_id == meta_id)
        )
        return int(t or 0)


def total_ahorrado_meta_hasta(user_id: int, meta_id: int, hasta: date) -> int:
    """Total aportado a una meta hasta cierta fecha (para proyecciones)."""
    with session_scope() as s:
        t = s.scalar(
            select(func.coalesce(func.sum(Ahorro.monto), 0))
            .where(Ahorro.user_id == user_id, Ahorro.meta_id == meta_id, Ahorro.fecha <= hasta)
        )
        return int(t or 0)


def total_ahorrado_meta_mes(user_id: int, meta_id: int, anio: int, mes: int) -> int:
    """Aportado a una meta SOLO en el mes indicado (para progreso de metas mensuales)."""
    ini, fin = _rango_mes(anio, mes)
    with session_scope() as s:
        t = s.scalar(
            select(func.coalesce(func.sum(Ahorro.monto), 0))
            .where(Ahorro.user_id == user_id, Ahorro.meta_id == meta_id,
                   Ahorro.fecha >= ini, Ahorro.fecha <= fin)
        )
        return int(t or 0)


def total_ahorro_mes(user_id: int, anio: int, mes: int) -> int:
    ini, fin = _rango_mes(anio, mes)
    with session_scope() as s:
        t = s.scalar(
            select(func.coalesce(func.sum(Ahorro.monto), 0))
            .where(Ahorro.user_id == user_id, Ahorro.fecha >= ini, Ahorro.fecha <= fin)
        )
        return int(t or 0)


# --- corrección de movimientos (listar / editar / eliminar) ------------------
# Para que el coach pueda revertir duplicados o corregir montos mal anotados.
# TODO verifica ownership: solo se toca lo que pertenece al user_id.

def ultimos_ingresos(user_id: int, limite: int = 10) -> list[dict]:
    with session_scope() as s:
        filas = s.scalars(
            select(Ingreso).where(Ingreso.user_id == user_id)
            .order_by(Ingreso.fecha.desc(), Ingreso.id.desc()).limit(limite)
        ).all()
        return [{"id": i.id, "monto": i.monto, "descripcion": i.descripcion,
                 "fecha": i.fecha.isoformat()} for i in filas]


def ultimos_ahorros(user_id: int, limite: int = 10) -> list[dict]:
    with session_scope() as s:
        filas = s.scalars(
            select(Ahorro).where(Ahorro.user_id == user_id)
            .order_by(Ahorro.fecha.desc(), Ahorro.id.desc()).limit(limite)
        ).all()
        out = []
        for a in filas:
            meta = s.get(MetaAhorro, a.meta_id) if a.meta_id else None
            out.append({"id": a.id, "monto": a.monto, "fecha": a.fecha.isoformat(),
                        "meta": meta.nombre if meta else None})
        return out


def editar_gasto(user_id: int, gasto_id: int, **campos) -> bool:
    """Edita monto/categoria/descripcion/fecha de un gasto propio."""
    with session_scope() as s:
        g = s.get(Gasto, gasto_id)
        if g is None or g.user_id != user_id:
            return False
        if campos.get("monto") is not None:
            m = int(campos["monto"])
            if m <= 0:
                raise ValueError("El monto debe ser mayor que 0.")
            g.monto = m
        if campos.get("categoria"):
            g.categoria = campos["categoria"] if campos["categoria"] in CATEGORIAS else "otros"
        if campos.get("descripcion"):
            g.descripcion = str(campos["descripcion"])[:200]
        if campos.get("fecha"):
            g.fecha = _a_fecha(campos["fecha"])
        return True


def borrar_ingreso(user_id: int, ingreso_id: int) -> bool:
    with session_scope() as s:
        i = s.get(Ingreso, ingreso_id)
        if i is None or i.user_id != user_id:
            return False
        s.delete(i)
        return True


def editar_ingreso(user_id: int, ingreso_id: int, **campos) -> bool:
    """Edita monto/descripcion/fecha de un ingreso variable propio."""
    with session_scope() as s:
        i = s.get(Ingreso, ingreso_id)
        if i is None or i.user_id != user_id:
            return False
        if campos.get("monto") is not None:
            m = int(campos["monto"])
            if m <= 0:
                raise ValueError("El monto debe ser mayor que 0.")
            i.monto = m
        if campos.get("descripcion"):
            i.descripcion = str(campos["descripcion"])[:200]
        if campos.get("fecha"):
            i.fecha = _a_fecha(campos["fecha"])
        return True


def borrar_ahorro(user_id: int, ahorro_id: int) -> bool:
    with session_scope() as s:
        a = s.get(Ahorro, ahorro_id)
        if a is None or a.user_id != user_id:
            return False
        s.delete(a)
        return True


def editar_ahorro(user_id: int, ahorro_id: int, monto=None, fecha=None, meta_id=None) -> bool:
    """Edita monto/fecha/meta de un aporte de ahorro propio."""
    with session_scope() as s:
        a = s.get(Ahorro, ahorro_id)
        if a is None or a.user_id != user_id:
            return False
        if monto is not None:
            m = int(monto)
            if m <= 0:
                raise ValueError("El monto debe ser mayor que 0.")
            a.monto = m
        if fecha:
            a.fecha = _a_fecha(fecha)
        if meta_id is not None:
            meta = s.get(MetaAhorro, meta_id)
            a.meta_id = meta.id if (meta and meta.user_id == user_id) else None
        return True


def editar_ingreso_fijo(user_id: int, ingreso_id: int, monto=None, descripcion=None, activo=None) -> bool:
    """Edita un ingreso fijo propio (p. ej. cambió el sueldo, o desactivarlo)."""
    with session_scope() as s:
        i = s.get(IngresoFijo, ingreso_id)
        if i is None or i.user_id != user_id:
            return False
        if monto is not None:
            m = int(monto)
            if m <= 0:
                raise ValueError("El monto debe ser mayor que 0.")
            i.monto = m
        if descripcion:
            i.descripcion = str(descripcion)[:200]
        if activo is not None:
            i.activo = bool(activo)
        return True


# --- regla de presupuesto 50/30/20 ------------------------------------------

def get_regla(user_id: int) -> dict:
    """Devuelve la regla del usuario o el default 50/30/20 si no la ha configurado."""
    with session_scope() as s:
        r = s.get(ReglaPresupuesto, user_id)
        if r is None:
            return dict(REGLA_DEFAULT)
        return {"pct_necesidades": r.pct_necesidades, "pct_deseos": r.pct_deseos, "pct_ahorro": r.pct_ahorro}


def set_regla(user_id: int, pct_necesidades: int, pct_deseos: int, pct_ahorro: int) -> dict:
    """Guarda la regla. No valida viabilidad (eso lo hace/aconseja la IA)."""
    n, d, a = int(pct_necesidades), int(pct_deseos), int(pct_ahorro)
    if min(n, d, a) < 0:
        raise ValueError("Los porcentajes no pueden ser negativos.")
    with session_scope() as s:
        r = s.get(ReglaPresupuesto, user_id)
        if r is None:
            r = ReglaPresupuesto(user_id=user_id, pct_necesidades=n, pct_deseos=d, pct_ahorro=a)
            s.add(r)
        else:
            r.pct_necesidades, r.pct_deseos, r.pct_ahorro = n, d, a
            r.actualizado_en = datetime.utcnow()
        return {"pct_necesidades": n, "pct_deseos": d, "pct_ahorro": a}


# --- presupuestos por categoría ----------------------------------------------

def set_presupuesto(user_id: int, categoria: str, monto: int) -> dict:
    """Define el tope mensual de una categoría. monto <= 0 lo elimina."""
    if categoria not in CATEGORIAS:
        raise ValueError(f"Categoría inválida: {categoria}")
    monto = int(monto)
    with session_scope() as s:
        p = s.get(PresupuestoCategoria, (user_id, categoria))
        if monto <= 0:
            if p is not None:
                s.delete(p)
            return {"categoria": categoria, "monto": 0, "eliminado": True}
        if p is None:
            s.add(PresupuestoCategoria(user_id=user_id, categoria=categoria, monto=monto))
        else:
            p.monto = monto
            p.actualizado_en = datetime.utcnow()
        return {"categoria": categoria, "monto": monto}


def listar_presupuestos(user_id: int) -> list[dict]:
    with session_scope() as s:
        filas = s.scalars(
            select(PresupuestoCategoria).where(PresupuestoCategoria.user_id == user_id)
        ).all()
        return [{"categoria": p.categoria, "monto": p.monto} for p in filas]


# --- rangos de fechas (para el resumen semanal) -------------------------------

def gastos_rango(user_id: int, ini: date, fin: date) -> dict:
    """Total y desglose por categoría de los gastos variables entre dos fechas."""
    with session_scope() as s:
        filas = s.execute(
            select(Gasto.categoria, func.sum(Gasto.monto))
            .where(Gasto.user_id == user_id, Gasto.fecha >= ini, Gasto.fecha <= fin)
            .group_by(Gasto.categoria)
            .order_by(func.sum(Gasto.monto).desc())
        ).all()
    por_categoria = {cat: int(total) for cat, total in filas}
    return {"total": sum(por_categoria.values()), "por_categoria": por_categoria}


def total_ingresos_variables_rango(user_id: int, ini: date, fin: date) -> int:
    with session_scope() as s:
        t = s.scalar(
            select(func.coalesce(func.sum(Ingreso.monto), 0))
            .where(Ingreso.user_id == user_id, Ingreso.fecha >= ini, Ingreso.fecha <= fin)
        )
        return int(t or 0)


def total_ahorro_rango(user_id: int, ini: date, fin: date) -> int:
    with session_scope() as s:
        t = s.scalar(
            select(func.coalesce(func.sum(Ahorro.monto), 0))
            .where(Ahorro.user_id == user_id, Ahorro.fecha >= ini, Ahorro.fecha <= fin)
        )
        return int(t or 0)


def get_ultima_semana_resumen(user_id: int) -> str | None:
    with session_scope() as s:
        u = s.get(User, user_id)
        return u.ultima_semana_resumen if u else None


def set_ultima_semana_resumen(user_id: int, semana: str) -> None:
    with session_scope() as s:
        u = s.get(User, user_id)
        if u is not None:
            u.ultima_semana_resumen = semana


# --- mensajes (memoria del chat) --------------------------------------------

def agregar_mensaje(user_id: int, rol: str, texto: str) -> None:
    with session_scope() as s:
        s.add(Mensaje(user_id=user_id, rol=rol, texto=texto or ""))


def agregar_intercambio(user_id: int, user_texto: str, assistant_texto: str) -> None:
    """Inserta el par (user, assistant) en una sola transacción: nunca queda un
    mensaje huérfano que rompa la alternancia de roles del historial."""
    with session_scope() as s:
        s.add(Mensaje(user_id=user_id, rol="user", texto=user_texto or ""))
        s.add(Mensaje(user_id=user_id, rol="assistant", texto=assistant_texto or ""))


def historial_mensajes(user_id: int, limite: int = 20) -> list[dict]:
    """Últimos mensajes en orden cronológico (viejo → nuevo)."""
    with session_scope() as s:
        filas = s.scalars(
            select(Mensaje).where(Mensaje.user_id == user_id)
            .order_by(Mensaje.id.desc()).limit(limite)
        ).all()
        return [{"rol": m.rol, "texto": m.texto} for m in reversed(filas)]
