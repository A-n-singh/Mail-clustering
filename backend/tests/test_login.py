from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import auth
from app.config import settings
from app import db as dbmod
from app.db import Base, CentralBase, get_central_db, get_db
from app.main import app
from app.models import Owner


class FakeFlow:
    """Stands in for Google's flow; records whether the PKCE verifier was restored on the way back."""
    last = None

    def __init__(self):
        self.code_verifier = "verifier-123"
        self.fetched = None
        self.credentials = SimpleNamespace(id_token="tok", refresh_token="refresh-abc")
        FakeFlow.last = self

    def authorization_url(self, **kw):
        return "https://accounts.google.com/o/oauth2/auth?x=1", "state-xyz"

    def fetch_token(self, code):
        self.fetched = (code, self.code_verifier)


def setup(monkeypatch, email="me@example.com"):
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(e)
    S = sessionmaker(bind=e, expire_on_commit=False)
    app.dependency_overrides[get_db] = lambda: S()
    ec = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    CentralBase.metadata.create_all(ec)
    SC = sessionmaker(bind=ec, expire_on_commit=False)
    app.dependency_overrides[get_central_db] = lambda: SC()      # the user list
    monkeypatch.setattr(dbmod, "open_user_session", lambda key: S())  # that user's own database
    monkeypatch.setattr(settings, "token_key", Fernet.generate_key().decode())
    monkeypatch.setattr(auth, "build_flow", FakeFlow)
    import google.oauth2.id_token as idt
    monkeypatch.setattr(idt, "verify_oauth2_token", lambda *a, **k: {"email": email})
    return TestClient(app, follow_redirects=False), S


def test_login_then_callback_restores_pkce_and_signs_in(monkeypatch):
    c, S = setup(monkeypatch)
    r = c.get("/auth/login")
    assert r.status_code == 307 and r.headers["location"].startswith("https://accounts.google.com/")
    assert "oauth" in r.cookies
    cb = c.get("/auth/callback?code=CODE&state=state-xyz")
    assert cb.status_code == 307
    assert FakeFlow.last.fetched == ("CODE", "verifier-123")          # same PKCE verifier as at login
    with S() as db:
        assert db.scalar(select(Owner)).email == "me@example.com"       # token stored (encrypted)
        assert "refresh-abc" not in db.scalar(select(Owner)).refresh_token_enc
    assert c.get("/me").json() == {"email": "me@example.com"}           # session cookie works


def test_callback_rejects_wrong_state_and_missing_cookie(monkeypatch):
    c, _ = setup(monkeypatch)
    assert c.get("/auth/callback?code=C&state=s").status_code == 400    # never started here
    c.get("/auth/login")
    assert c.get("/auth/callback?code=C&state=FORGED").status_code == 400
    assert c.get("/me").status_code == 401


def test_callback_rejects_other_google_accounts(monkeypatch):
    c, S = setup(monkeypatch, email="stranger@example.com")
    c.get("/auth/login")
    assert c.get("/auth/callback?code=C&state=state-xyz").status_code == 403
    with S() as db:
        assert db.scalar(select(Owner)) is None
