import re
import threading
from pathlib import Path

from fastapi import Depends
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from . import auth
from .config import settings


class Base(DeclarativeBase):
    """The tables of ONE user's mailbox (mail, scores, groups, topics, ...). Every user has their own database holding these."""


class CentralBase(DeclarativeBase):
    """Tables shared by everyone (the list of users). They live only in the main database."""


_MEMORY = ("sqlite://", "sqlite:///:memory:")


def make_engine(url: str):
    sqlite = url.startswith("sqlite")
    kwargs = {"connect_args": {"check_same_thread": False, "timeout": 30}} if sqlite else {}
    if url in _MEMORY:
        kwargs["poolclass"] = StaticPool  # one shared in-memory database for every thread (tests, demo)
    eng = create_engine(url, **kwargs)
    if sqlite and url not in _MEMORY:
        @event.listens_for(eng, "connect")
        def _wal(conn, _):  # WAL: the dashboard can read while a long sync writes, and neither blocks the other
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()
    return eng


engine = make_engine(settings.database_url)  # the main database: the user list, and the owner's own mailbox
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

# ---------------- one database per user ----------------
_KEY = re.compile(r"^(main|u\d+)$")
_user_engines: dict[str, object] = {}
_lock = threading.Lock()


def _main_sqlite_path() -> Path | None:
    url = settings.database_url
    if not url.startswith("sqlite") or url in _MEMORY:
        return None
    return Path(url.split("///", 1)[1])


def user_dir() -> Path | None:
    if settings.user_data_dir:
        return Path(settings.user_data_dir)
    p = _main_sqlite_path()
    return p.parent / "users" if p else None


def _engine_for(key: str):
    if not _KEY.match(key):
        raise ValueError(f"bad data key {key!r}")
    if key == "main":
        return engine
    with _lock:
        eng = _user_engines.get(key)
        if eng is None:
            if not settings.database_url.startswith("sqlite"):
                raise RuntimeError("Separate user databases need DATABASE_URL to be SQLite for now (Postgres is not supported for several users yet).")
            d = user_dir()
            if d is None:
                eng = make_engine("sqlite://")
            else:
                d.mkdir(parents=True, exist_ok=True)
                eng = make_engine(f"sqlite:///{d / (key + '.db')}")
            Base.metadata.create_all(eng)
            _user_engines[key] = eng
        return eng


def open_user_session(key: str) -> Session:
    """A session on that user's own database ("main" = the owner's, in the main database)."""
    return sessionmaker(bind=_engine_for(key), expire_on_commit=False)()


def drop_user_data(key: str):
    """Erase a user's database completely (account deletion). The owner's "main" database is emptied by the caller instead."""
    if key == "main":
        return
    with _lock:
        eng = _user_engines.pop(key, None)
    if eng is not None:
        eng.dispose()
    d = user_dir()
    if d is not None:
        for suffix in ("", "-wal", "-shm"):
            (d / f"{key}.db{suffix}").unlink(missing_ok=True)


_central_ready = False


def central_session() -> Session:
    global _central_ready
    if not _central_ready:  # the user list table exists before anyone uses it, however the app was started
        from . import models  # noqa: F401  (registers the tables)
        CentralBase.metadata.create_all(engine)
        _central_ready = True
    return SessionLocal()


def get_central_db():
    db = central_session()
    try:
        yield db
    finally:
        db.close()


def get_db(user: auth.Principal = Depends(auth.current_user)):
    """The signed-in user's own database. Every route that reads or changes mail depends on this, so nothing can reach another user's mail."""
    db = open_user_session(user.data_key)
    try:
        yield db
    finally:
        db.close()
