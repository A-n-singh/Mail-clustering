from app.models import SyncRun
from app.sync import sync_in_progress


def test_a_leftover_unfinished_row_is_not_a_running_sync(db):
    """A restart mid-sync leaves an unfinished row behind. It must NOT look like a sync that is still running."""
    db.add(SyncRun())
    db.commit()
    assert sync_in_progress(db) is False
    control_flag_on = __import__("app.control", fromlist=["x"]).sync_active
    control_flag_on.set()
    assert sync_in_progress(db) is True          # only a really running sync counts


def test_orphan_runs_are_closed_on_startup_and_by_stop(db):
    from app.models import TopicRun
    from app.sync import close_orphan_runs
    db.add(SyncRun()); db.add(TopicRun()); db.commit()
    assert close_orphan_runs(db) == 2
    assert all(r.ended is not None for r in db.scalars(select(SyncRun)))
    assert all(r.ended is not None for r in db.scalars(select(TopicRun)))
    assert close_orphan_runs(db) == 0


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


# ---- Gmail rate limiting: pacing, retry, resumable first sync ----
import httplib2
import pytest
from sqlalchemy import select

from app.models import Message
from googleapiclient.errors import HttpError

from app import gmail as gm
from app.sync import run_sync
from conftest import FakeGmail, mail


def http_error(status, reason=""):
    body = ('{"error": {"errors": [{"reason": "%s"}]}}' % reason).encode()
    return HttpError(httplib2.Response({"status": status}), body)


def test_retryable_errors():
    assert gm.is_retryable(http_error(403, "rateLimitExceeded"))
    assert gm.is_retryable(http_error(403, "userRateLimitExceeded"))
    assert gm.is_retryable(http_error(429)) and gm.is_retryable(http_error(503))
    assert not gm.is_retryable(http_error(403, "forbidden"))     # a real permission problem must not be retried
    assert not gm.is_retryable(http_error(404))


def test_paced_call_waits_and_retries_then_succeeds(monkeypatch):
    sleeps, calls = [], []
    monkeypatch.setattr(gm, "_sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(gm, "_pacer", gm._Pacer())
    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise http_error(403, "rateLimitExceeded")
        return "ok"
    assert gm.paced_call(flaky, rate=1000) == "ok"
    assert len(calls) == 3 and 5 in sleeps and 10 in sleeps      # waited 5s then 10s between tries


def test_paced_call_gives_up_and_does_not_retry_real_errors(monkeypatch):
    monkeypatch.setattr(gm, "_sleep", lambda s: None)
    monkeypatch.setattr(gm, "_pacer", gm._Pacer())
    with pytest.raises(HttpError):
        gm.paced_call(lambda: (_ for _ in ()).throw(http_error(403, "rateLimitExceeded")), rate=1000)
    n = []
    def denied():
        n.append(1); raise http_error(403, "forbidden")
    with pytest.raises(HttpError):
        gm.paced_call(denied, rate=1000)
    assert len(n) == 1


def test_pacer_spaces_calls(monkeypatch):
    t = [0.0]
    monkeypatch.setattr(gm, "_clock", lambda: t[0])
    waits = []
    monkeypatch.setattr(gm, "_sleep", lambda s: (waits.append(round(s, 3)), t.__setitem__(0, t[0] + s)))
    p = gm._Pacer()
    for _ in range(4):
        p.wait(rate=10)                    # 10 per second -> 0.1s apart
    assert waits == [0.1, 0.1, 0.1]


def test_first_sync_resumes_after_a_rate_limit_failure(db):
    g = FakeGmail([mail(f"m{i}", f"S{i} <s{i}@co{i}.com>", f"hello {i}") for i in range(10)])
    real, seen = g.get_message, []
    def failing(mid):
        if len(seen) == 6:
            raise http_error(403, "rateLimitExceeded")
        seen.append(mid); return real(mid)
    g.get_message = failing
    with pytest.raises(HttpError):
        run_sync(db, g)
    assert len(list(db.scalars(select(Message)))) == 6            # progress is kept, not rolled back
    assert db.get(Message, "m0") is not None
    from app.models import Cluster
    assert len(list(db.scalars(select(Cluster)))) == 6            # and the dashboard can already show it
    g.get_message, again = real, []
    def counting(mid):
        again.append(mid); return real(mid)
    g.get_message = counting
    assert run_sync(db, g).fetched == 4                           # second run reads only the 4 missing mails
    assert len(again) == 4 and len(list(db.scalars(select(Message)))) == 10


def test_real_google_client_routes_every_call_through_pacing(monkeypatch):
    """Builds the real googleapiclient service (offline) and checks execute() goes via paced_call."""
    from google.auth.credentials import AnonymousCredentials
    from app.gmail import GoogleGmailClient
    seen = []
    monkeypatch.setattr(gm, "paced_call", lambda fn, rate=None: seen.append("paced") or "RESULT")
    g = GoogleGmailClient(AnonymousCredentials())
    req = g.svc.users().messages().get(userId="me", id="abc")
    assert type(req).__name__ == "PacedRequest"
    assert req.execute() == "RESULT" and seen == ["paced"]
    assert g.svc.users().getProfile(userId="me").execute() == "RESULT"      # a different call type is paced too


def test_header_total_matches_the_groups_below_even_after_a_failed_sync(db):
    """The header counts every stored mail; the groups/bubbles must add up to exactly the same number."""
    from app.models import Cluster, MessageCluster
    g = FakeGmail([mail(f"m{i}", f"S{i} <s{i}@co{i % 3}.com>", f"hello {i}") for i in range(10)])
    real, n = g.get_message, []
    def failing(mid):
        if len(n) == 7:
            raise http_error(403, "rateLimitExceeded")
        n.append(mid); return real(mid)
    g.get_message = failing
    with pytest.raises(HttpError):
        run_sync(db, g)
    stored = len(list(db.scalars(select(Message))))
    clustered = len(list(db.scalars(select(MessageCluster))))
    assert stored == 7 and clustered == stored                         # nothing stored but left ungrouped
    assert sum(c.size for c in db.scalars(select(Cluster))) == stored  # and the group sizes add up


def test_mails_are_grouped_while_a_long_sync_is_still_running(db):
    """Grouping used to happen only at the very end, so 'Grouped' stayed 0 for the whole first sync."""
    from app.models import MessageCluster
    g = FakeGmail([mail(f"m{i}", f"S <s@co{i % 5}.com>", f"hello {i}") for i in range(620)])
    real, mid_sync = g.get_message, {}
    def watching(mid):
        if mid == "m560":   # we are past the 500th mail, but the sync is not finished
            mid_sync["grouped"] = len(list(db.scalars(select(MessageCluster))))
        return real(mid)
    g.get_message = watching
    run_sync(db, g, initial_fetch=1000)
    assert mid_sync["grouped"] >= 500                                  # grouping happened mid-sync
    assert len(list(db.scalars(select(MessageCluster)))) == 620        # and everything is grouped at the end


# ---- Stop sync ----
from app import control


def test_stop_keeps_progress_groups_it_and_next_sync_resumes(db):
    g = FakeGmail([mail(f"m{i}", f"S <s@co{i % 3}.com>", f"hello {i}") for i in range(12)])
    real, calls = g.get_message, []
    def stopping(mid):
        calls.append(mid)
        if len(calls) == 5:
            control.cancel_sync.set()              # the owner presses Stop while mail #5 is being read
        return real(mid)
    g.get_message = stopping
    run = run_sync(db, g)
    assert run.error == control.STOPPED and run.ended is not None     # ended cleanly, not as a failure
    assert len(list(db.scalars(select(Message)))) == 5                 # everything already read is kept
    from app.models import MessageCluster, SyncState
    assert len(list(db.scalars(select(MessageCluster)))) == 5          # and already grouped
    assert db.get(SyncState, 1) is None                                # history not advanced, so Sync resumes
    g.get_message, later = real, []
    g.get_message = lambda mid: (later.append(mid), real(mid))[1]
    assert run_sync(db, g).fetched == 7 and len(later) == 7            # only the 7 unread mails; Stop did not stick
    assert len(list(db.scalars(select(Message)))) == 12


def test_stop_interrupts_a_gmail_quota_wait_immediately():
    control.cancel_sync.set()
    with pytest.raises(control.SyncCancelled):
        control.cancellable_sleep(60)                                  # would otherwise block for a minute
    control.cancel_sync.clear()


def test_stop_endpoint_and_status():
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app import auth
    from app.db import Base, get_db
    from app.main import app
    from app.models import SyncRun
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(e)
    S = sessionmaker(bind=e, expire_on_commit=False)
    app.dependency_overrides[get_db] = lambda: S()
    c = TestClient(app)
    assert c.post("/sync/stop").status_code == 401
    c.cookies.set("session", auth.make_session("me@example.com"))
    control.cancel_sync.clear()
    with S() as s:
        s.add(SyncRun()); s.commit()                                    # a leftover row from a restart, nothing is running
    assert c.get("/sync/status").json()["running"] is False             # the screen is not stuck on "Syncing…"
    assert c.post("/sync/stop").json()["stopping"] is False             # nothing to stop...
    assert not control.cancel_sync.is_set()
    with S() as s:
        assert s.query(SyncRun).first().ended is not None               # ...and Stop cleaned the leftover row
    control.sync_active.set()                                           # now a sync REALLY runs
    assert c.post("/sync/stop").json() == {"stopping": True}
    assert control.cancel_sync.is_set()
    control.sync_active.clear(); control.cancel_sync.clear()
    with S() as s:
        r = s.query(SyncRun).first(); r.ended, r.error, r.fetched = __import__("datetime").datetime.now(), control.STOPPED, 40; s.commit()
    st = c.get("/sync/status").json()
    assert st["stopped"] is True and st["error"] is None and st["running"] is False and st["fetched"] == 40
