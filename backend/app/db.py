from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str):
    sqlite = url.startswith("sqlite")
    kwargs = {"connect_args": {"check_same_thread": False, "timeout": 30}} if sqlite else {}
    eng = create_engine(url, **kwargs)
    if sqlite and url not in ("sqlite://", "sqlite:///:memory:"):
        @event.listens_for(eng, "connect")
        def _wal(conn, _):  # WAL: the dashboard can read while a long sync writes, and neither blocks the other
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()
    return eng


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
