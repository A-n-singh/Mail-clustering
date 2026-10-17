import pytest
from sqlalchemy import select

from app import actions as act, auth
from app.config import settings
from app.models import Action, Cluster, Message
from app.purge import PurgeError, purge_trash, trash_summary
from app.sync import run_sync
from conftest import FakeGmail, mail


def trashed_setup(db):
    g = FakeGmail([mail(str(i), "Shop <d@shop.com>", f"Sale {i}", "x", unsub="<u>", size=1000 * (i + 1)) for i in range(4)])
    run_sync(db, g)
    act.perform(db, g, ["0", "1", "2"], "trash", confirmed=True)
    for i in "012":
        g.mails[i].labels = ["TRASH"]       # what Gmail reports after trashing
    return g


def test_purge_deletes_only_trashed_mail_and_logs(db):
    g = trashed_setup(db)
    assert trash_summary(db) == {"count": 3, "bytes": 1000 + 2000 + 3000}
    r = purge_trash(db, g)
    assert r == {"deleted": 3, "bytes_freed": 6000, "skipped_restored": 0}
    assert sorted(g.deleted) == ["0", "1", "2"]
    assert [m.message_id for m in db.scalars(select(Message))] == ["3"]        # the live mail is untouched
    assert "3" in g.mails
    assert db.scalar(select(Cluster)).size == 1
    assert {a.action for a in db.scalars(select(Action)) if a.action == "purge"} == {"purge"}


def test_purge_skips_mail_restored_in_gmail(db):
    g = trashed_setup(db)
    g.mails["1"].labels = ["INBOX"]     # restored since we last synced
    r = purge_trash(db, g)
    assert r["deleted"] == 2 and r["skipped_restored"] == 1
    assert "1" not in (g.deleted or []) and "1" in g.mails


def test_purge_refuses_mail_not_in_trash(db):
    g = trashed_setup(db)
    with pytest.raises(PurgeError):
        purge_trash(db, g, ["3"])       # mail 3 is live
    assert g.deleted is None


def test_purge_specific_ids(db):
    g = trashed_setup(db)
    assert purge_trash(db, g, ["0"])["deleted"] == 1
    assert g.deleted == ["0"]


def test_api_guards(monkeypatch):
    from test_api import client
    c, g = client()
    c.cookies.set("session", auth.make_session("me@example.com"))
    assert c.post("/trash/purge", json={"confirm": "DELETE FOREVER"}).status_code == 403       # off by default
    assert c.get("/trash").json()["enabled"] is False
    monkeypatch.setattr(settings, "allow_permanent_delete", True)
    assert c.post("/trash/purge", json={"confirm": "yes"}).status_code == 400                  # wrong phrase
    assert g.deleted is None
    b = c.post("/actions", json={"message_ids": ["2"], "action": "trash"}).json()
    g.mails["2"].labels = ["TRASH"]
    assert c.get("/trash").json()["count"] == 1
    assert c.post("/trash/purge", json={"confirm": "DELETE FOREVER"}).json()["deleted"] == 1
    assert g.deleted == ["2"]


def test_scope_depends_on_setting(monkeypatch):
    assert "https://mail.google.com/" not in auth.scopes()
    monkeypatch.setattr(settings, "allow_permanent_delete", True)
    assert "https://mail.google.com/" in auth.scopes()
    assert not any("gmail.modify" in s for s in auth.scopes())
