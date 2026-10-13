import json

from sqlalchemy import func, select

from app import llm
from app.config import settings
from app.models import LlmUsage, Message, Owner, Score
from app.sync import run_sync, sync_in_progress
from app.models import SyncRun
from conftest import FakeGmail, mail


def test_llm_off_by_default(db):
    assert llm.classify(db, "a", "b", "c") is None


def test_llm_refines_undecided_mail_and_respects_daily_cap(db, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "k")
    monkeypatch.setattr(settings, "llm_daily_cap", 1)
    sent = []
    def fake(prompt):
        sent.append(prompt)
        return json.dumps({"class": "needs_reply", "reason": "Asks about the contract"})
    assert llm.classify(db, "Ann", "Contract", "x" * 5000, call=fake) == ("needs_reply", 70, "Asks about the contract")
    assert "x" * 1500 in sent[0] and "x" * 1501 not in sent[0]   # body trimmed to 1,500 chars
    assert llm.classify(db, "Ann", "Again", "y", call=fake) is None   # cap of 1 reached
    assert db.get(LlmUsage, __import__("datetime").date.today().isoformat()).calls == 1


def test_llm_bad_reply_falls_back(db, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "k")
    assert llm.classify(db, "a", "b", "c", call=lambda p: "not json") is None
    assert llm.classify(db, "a", "b", "c", call=lambda p: '{"class": "weird"}') is None


def test_llm_used_in_sync_only_when_rules_undecided(db, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "k")
    monkeypatch.setattr(llm, "_call_model", lambda p: '{"class": "needs_reply", "reason": "LLM says so"}')
    g = FakeGmail([mail("1", "Ann <ann@corp.com>", "Quarterly numbers", "see attached figures"),
                   mail("2", "Shop <d@shop.com>", "50% off sale", "x", unsub="<u>")])
    run_sync(db, g)
    s = {x.message_id: x for x in db.scalars(select(Score))}
    assert s["1"].cls == "needs_reply" and s["1"].reason == "LLM says so"
    assert s["2"].cls == "junk"                      # decided by rules, LLM never consulted


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
