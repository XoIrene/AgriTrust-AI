"""Database engine, session factory and declarative base (MariaDB / MySQL)."""
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import QueuePool

from app.config import settings

if settings.is_sqlite:  # pragma: no cover - local fallback only
    connect_args = {"check_same_thread": False}
    engine_kwargs: dict = {"connect_args": connect_args, "echo": False}
else:
    engine_kwargs = {
        "pool_pre_ping": True,
        "pool_recycle": 1800,
        "pool_size": 10,
        "max_overflow": 20,
        "poolclass": QueuePool,
        "echo": False,
    }

engine = create_engine(settings.database_url, **engine_kwargs)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Declarative base for every AgriTrust AI model."""


if settings.is_mysql:

    @event.listens_for(engine, "connect")
    def _set_mysql_session(dbapi_connection, connection_record):  # pragma: no cover - driver glue
        """Make MariaDB sessions strict + UTF-8 so data round-trips cleanly."""
        with dbapi_connection.cursor() as cursor:
            cursor.execute("SET NAMES utf8mb4")
            cursor.execute("SET SESSION sql_mode='STRICT_TRANS_TABLES'")
            cursor.execute("SET SESSION time_zone='+00:00'")


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_test_db() -> Generator[Session, None, None]:
    """Session bound to the test database (used by pytest)."""
    test_engine = create_engine(
        settings.test_database_url,
        pool_pre_ping=True,
        echo=False,
    )
    if settings.is_sqlite:  # pragma: no cover
        TestingSession = sessionmaker(
            bind=test_engine, autocommit=False, autoflush=False, connect_args={"check_same_thread": False}
        )
    else:
        TestingSession = sessionmaker(bind=test_engine, autocommit=False, autoflush=False)
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()
