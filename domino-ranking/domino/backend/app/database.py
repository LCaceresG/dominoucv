"""Conexión a la base de datos.

Por defecto usa SQLite (archivo local `domino.db`) para desarrollar.
En producción cambia DATABASE_URL a PostgreSQL, por ejemplo:
    postgresql+psycopg://usuario:clave@host:5432/domino
"""
import os

from sqlalchemy import Boolean, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./domino.db")

# SQLite: espera hasta 30 s si otra petición está escribiendo (la web hace varias a la vez).
connect_args = {"check_same_thread": False, "timeout": 30} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False)


class Base(DeclarativeBase):
    pass


def ensure_schema() -> list[str]:
    """Crea las tablas que falten y agrega las columnas nuevas a las tablas existentes.

    Así se puede actualizar el programa sin borrar la base de datos ni perder datos.
    Devuelve la lista de columnas agregadas (vacía si todo estaba al día).
    Es un sustituto simple de las migraciones; con datos reales conviene pasar a Alembic.
    """
    from . import models  # noqa: F401  (registra las tablas)

    Base.metadata.create_all(engine)
    added: list[str] = []
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl = f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(dialect=engine.dialect)}'
                scalar = col.default.arg if col.default is not None and col.default.is_scalar else None
                if isinstance(col.type, Boolean):
                    ddl += f" DEFAULT {'TRUE' if scalar else 'FALSE'}"
                elif isinstance(scalar, (int, float)):
                    ddl += f" DEFAULT {scalar}"  # filas existentes arrancan con el valor por defecto (p. ej. 0.0)
                conn.execute(text(ddl))
                added.append(f"{table.name}.{col.name}")
    return added


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
