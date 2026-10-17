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
