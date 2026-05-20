from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from food_registry_bot.config import get_settings


def create_engine_from_settings(database_url: str | None = None) -> Engine:
    settings = get_settings()
    return create_engine(database_url or settings.sqlalchemy_database_url, future=True)


def create_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    engine = create_engine_from_settings(database_url=database_url)
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
