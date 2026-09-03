from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from polybot.config import settings
from polybot.persistence.models import Base

_IS_SQLITE = settings.database_url.startswith("sqlite:///")

if _IS_SQLITE:
    db_path = Path(settings.database_url.removeprefix("sqlite:///"))
    db_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

if _IS_SQLITE:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:
        """WAL permite lectores concurrentes (dashboard, jobs de resolución, chequeos
        ad-hoc) sin bloquearse contra el escritor principal -- journal mode por defecto
        (rollback) ya causó errores "database is locked" en más de una sesión de
        diagnóstico. `busy_timeout` es la red de seguridad por conexión (WAL no elimina
        el lock durante un checkpoint, sólo lo hace mucho más raro)."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


def init_db() -> None:
    Base.metadata.create_all(engine)


def get_session() -> Session:
    return SessionLocal()
