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


def test_groups_hologram_shapes_and_filters():
    c, _ = client()
    c.cookies.set("session", auth.make_session("me@example.com"))
    h = c.get("/groups/hologram").json()
    assert {t["label"] for t in h["topics"]} == {"corp.com", "shop.com"}
    assert len(h["points"]) == 2
    assert all({"i", "x", "y", "z", "t", "s", "f"} <= set(p) for p in h["points"])
    assert {p["t"] for p in h["points"]} == {t["topic_id"] for t in h["topics"]}
    assert c.get("/groups/hologram").json() == h                      # same picture every time
    only = c.get("/groups/hologram?sender=shop").json()
    assert [t["label"] for t in only["topics"]] == ["shop.com"] and len(only["points"]) == 1
    assert c.get("/groups/hologram?label=NOPE").json() == {"points": [], "topics": []}


def test_group_hologram_splits_by_subject_template():
    c, _ = client()
    c.cookies.set("session", auth.make_session("me@example.com"))
    cid = c.get("/groups?sender=shop").json()[0]["cluster_id"]
    h = c.get(f"/groups/{cid}/hologram").json()
    assert len(h["points"]) == 1 and h["topics"][0]["count"] == 1 and h["points"][0]["t"] == h["topics"][0]["topic_id"]
    assert c.get(f"/groups/{cid}/hologram?label=NOPE").json() == {"points": [], "topics": []}


def test_holo_layout_is_stable_and_keeps_groups_apart():
    from app.holo_layout import layout
    g = [("a", [f"a{i}" for i in range(30)]), ("b", [f"b{i}" for i in range(10)]), ("c", ["c0"])]
    p = layout(g)
    assert p == layout(list(reversed(g))) and len(p) == 41
    mid = lambda ids: [sum(p[i][k] for i in ids) / len(ids) for k in range(3)]
    ca, cb = mid(g[0][1]), mid(g[1][1])
    assert sum((x - y) ** 2 for x, y in zip(ca, cb)) ** 0.5 > 0.5
    assert layout([]) == {}
