from app.models import SyncRun
from app.sync import sync_in_progress


def test_scheduler_job_skips_when_sync_running(db):
    db.add(SyncRun())
    db.commit()
    assert sync_in_progress(db) is True


def test_delete_account_erases_everything():
    from fastapi.testclient import TestClient
    from app import auth
    from app.main import app
    from test_api import client
    c, _ = client()
    c.cookies.set("session", auth.make_session("me@example.com"))
    assert c.get("/summary").json()["total"] == 2
    assert c.delete("/account").json() == {"ok": True}
    assert c.get("/summary").json() == {"total": 0, "grouped": 0, "needs_review": 0}
    assert c.get("/groups").json() == []
