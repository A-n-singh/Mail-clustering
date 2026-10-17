from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import auth
from app.db import Base, get_db
from app.main import app, get_gmail
from app.sync import run_sync
from conftest import FakeGmail, mail


def client():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(e)
    S = sessionmaker(bind=e, expire_on_commit=False)
    g = FakeGmail([mail("1", "Boss <boss@corp.com>", "Urgent: report", "deadline today"),
                   mail("2", "Shop <d@shop.com>", "sale", "50% off", unsub="<x>")])
    with S() as s:
        run_sync(s, g)
    app.dependency_overrides[get_db] = lambda: S()
    app.dependency_overrides[get_gmail] = lambda: g
    return TestClient(app), g


def test_requires_login():
    c, _ = client()
    assert c.get("/summary").status_code == 401


def test_only_owner_allowed():
    c, _ = client()
    c.cookies.set("session", auth.make_session("evil@example.com"))
    assert c.get("/summary").status_code == 403


def test_dashboard_flow():
    c, g = client()
    c.cookies.set("session", auth.make_session("me@example.com"))
    assert c.get("/summary").json() == {"total": 2, "grouped": 2, "needs_review": 1}
    assert c.get("/needs-action").json()[0]["message_id"] == "1"
    assert {x["label"] for x in c.get("/groups").json()} == {"corp.com", "shop.com"}
    r = c.post("/actions", json={"message_ids": ["2"], "action": "trash"})
    assert c.post(f"/actions/{r.json()['batch_id']}/undo").json() == {"undone": 1}
    assert c.post("/actions", json={"message_ids": ["1", "2"], "action": "trash", "confirmed": True}).status_code == 400


def test_filters_and_status():
    c, _ = client()
    c.cookies.set("session", auth.make_session("me@example.com"))
    assert [g["label"] for g in c.get("/groups?min_score=80").json()] == ["corp.com"]
    assert [g["label"] for g in c.get("/groups?sender=shop").json()] == ["shop.com"]
    assert len(c.get("/groups?label=UNREAD").json()) == 2
    assert c.get("/groups?label=NOPE").json() == []
    assert c.get("/groups?date_from=2030-01-01").json() == []
    assert c.get("/sync/status").json()["running"] is False
    cid = c.get("/groups?sender=shop").json()[0]["cluster_id"]
    assert len(c.get(f"/groups/{cid}/messages").json()) == 1
