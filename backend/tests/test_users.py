"""Several users: the user list, one database per user, nobody sees anybody else's mail, per-user job flags."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import auth, control, db as dbmod, users
from app import main as main_mod
from app.config import settings
from app.main import app
from app.models import Message, User
from app.sync import run_sync
from conftest import FakeGmail, mail


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "owner_email", "me@example.com")
    monkeypatch.setattr(settings, "allowed_emails", "ann@example.com, Bob@Example.com")
    monkeypatch.setattr(settings, "max_users", 3)
    monkeypatch.setattr(settings, "user_data_dir", str(tmp_path / "users"))
    app.dependency_overrides.clear()
    dbmod._user_engines.clear()
    with dbmod.central_session() as c:
        c.execute(delete(User))
        c.commit()
    dbmod.Base.metadata.drop_all(dbmod.engine)      # the owner's mailbox is the main database: start it empty
    dbmod.Base.metadata.create_all(dbmod.engine)
    yield
    for e in dbmod._user_engines.values():
        e.dispose()
    dbmod._user_engines.clear()
    app.dependency_overrides.clear()


def join(email, mails=()):
    """Sign a user up (as the Google callback does) and load some mail into THEIR database. Returns (user, client)."""
    with dbmod.central_session() as c:
        u = users.get_or_create(c, email)
    if mails:
        with dbmod.open_user_session(u.data_key) as s:
            run_sync(s, FakeGmail(list(mails)))
    cl = TestClient(app)
    cl.cookies.set("session", auth.make_session(email, u.id, u.data_key))
    return u, cl


def test_owner_keeps_the_main_database_others_get_their_own():
    with dbmod.central_session() as c:
        owner = users.get_or_create(c, "ME@example.com")
        ann = users.get_or_create(c, "ann@example.com")
        again = users.get_or_create(c, "Ann@Example.com")
        assert owner.data_key == "main" and owner.role == "owner"
        assert ann.data_key == f"u{ann.id}" and ann.role == "member"
        assert again.id == ann.id and again.last_login_at is not None        # same person, however the e-mail is typed
        assert [u.email for u in users.all_users(c)] == ["me@example.com", "ann@example.com"]


def test_pilot_is_limited_but_the_owner_always_gets_in(monkeypatch):
    monkeypatch.setattr(settings, "max_users", 2)
    monkeypatch.setattr(settings, "allowed_emails", "ann@example.com,bob@example.com,carol@example.com")
    with dbmod.central_session() as c:
        users.get_or_create(c, "ann@example.com")
        users.get_or_create(c, "bob@example.com")
        with pytest.raises(users.PilotFull):
            users.get_or_create(c, "carol@example.com")
        assert users.get_or_create(c, "me@example.com").role == "owner"        # exempt
        assert users.get_or_create(c, "ann@example.com")                       # people already in can always come back


def test_the_allow_list_is_checked_on_every_request():
    _, ann = join("ann@example.com")
    assert ann.get("/me").json() == {"email": "ann@example.com"}
    settings.allowed_emails = "bob@example.com"                                 # ann is taken off the list
    assert ann.get("/me").status_code == 403
    stranger = TestClient(app)
    assert stranger.get("/me").status_code == 401                               # no cookie
    stranger.cookies.set("session", auth.make_session("evil@example.com"))
    assert stranger.get("/me").status_code == 403


def test_nobody_sees_anybody_elses_mail():
    # both mailboxes even contain a mail with the SAME id ("1") but different content
    _, ann = join("ann@example.com", [mail("1", "Boss <boss@corp.com>", "Ann's secret plan", "x"),
                                      mail("2", "Shop <d@shop.com>", "Only Ann has this", "x")])
    _, bob = join("bob@example.com", [mail("1", "Pal <pal@home.org>", "Bob's holiday", "x")])
    _, owner = join("me@example.com")
    assert ann.get("/summary").json()["total"] == 2
    assert bob.get("/summary").json()["total"] == 1
    assert owner.get("/summary").json()["total"] == 0
    assert ann.get("/messages/1").json()["subject"] == "Ann's secret plan"
    assert bob.get("/messages/1").json()["subject"] == "Bob's holiday"
    assert bob.get("/messages/2").status_code == 404                            # Ann's mail does not exist for Bob
    assert {g["label"] for g in bob.get("/groups").json()} == {"home.org"}
    assert bob.post("/actions", json={"message_ids": ["2"], "action": "star"}).status_code in (400, 404, 409)


def test_mail_links_open_in_the_signed_in_users_own_account():
    _, ann = join("ann@example.com", [mail("1", "Boss <boss@corp.com>", "Urgent: report", "deadline today")])
    link = ann.get("/needs-action").json()[0]["gmail_link"]
    assert "authuser=ann%40example.com" in link


def test_job_flags_belong_to_one_user():
    a, ann = join("ann@example.com")
    _, bob = join("bob@example.com")
    control.flags(a.data_key).sync_active.set()                                 # Ann has a sync running
    assert ann.get("/sync/status").json()["running"] is True
    assert bob.get("/sync/status").json()["running"] is False                   # Bob's screen is not affected
    assert ann.post("/sync/stop").json() == {"stopping": True}
    assert control.flags(a.data_key).cancel_sync.is_set()
    assert not control.flags("main").cancel_sync.is_set()


def test_delete_account_removes_only_that_users_data_and_file(tmp_path):
    a, ann = join("ann@example.com", [mail("1", "Boss <boss@corp.com>", "Ann's mail", "x")])
    b, bob = join("bob@example.com", [mail("1", "Pal <pal@home.org>", "Bob's mail", "x")])
    f = tmp_path / "users" / f"{a.data_key}.db"
    assert f.exists()
    assert ann.delete("/account").json() == {"ok": True}
    assert not f.exists()
    with dbmod.central_session() as c:
        assert [u.email for u in users.all_users(c)] == ["bob@example.com"]
    assert bob.get("/summary").json()["total"] == 1


def test_a_cookie_from_before_several_users_still_works_for_the_owner_only():
    signer = auth._signer()
    old = TestClient(app)
    old.cookies.set("session", signer.dumps({"email": "me@example.com"}))
    assert old.get("/me").json() == {"email": "me@example.com"}
    old.cookies.set("session", signer.dumps({"email": "ann@example.com"}))
    assert old.get("/me").status_code == 401                                    # must sign in again, so she gets her own database


def test_the_scheduled_sync_visits_every_user_in_turn(monkeypatch):
    seen = []
    monkeypatch.setattr(main_mod, "_sync_job", lambda key, already_marked=False: seen.append(key))
    a, _ = join("ann@example.com")
    with dbmod.central_session() as c:
        users.ensure_owner(c)
    main_mod._sync_all()
    assert seen == [a.data_key, "main"] or sorted(seen) == sorted([a.data_key, "main"])


def test_bad_data_keys_are_refused():
    for key in ("../evil", "u1/../../x", "", "mainx"):
        with pytest.raises(ValueError):
            dbmod.open_user_session(key)


def _login_as(monkeypatch, email, verified=True):
    """Runs the Google callback for `email` with a faked Google, and returns (client, response)."""
    from cryptography.fernet import Fernet
    from test_login import FakeFlow
    from sqlalchemy.orm import sessionmaker
    import google.oauth2.id_token as idt
    monkeypatch.setattr(settings, "token_key", Fernet.generate_key().decode())
    monkeypatch.setattr(auth, "build_flow", FakeFlow)
    monkeypatch.setattr(idt, "verify_oauth2_token", lambda *a, **k: {"email": email, "email_verified": verified})
    c = TestClient(app, follow_redirects=False)
    c.get("/auth/login")
    return c, c.get("/auth/callback?code=C&state=state-xyz")


def test_open_signup_lets_any_google_account_in(monkeypatch):
    monkeypatch.setattr(settings, "open_signup", True)
    monkeypatch.setattr(settings, "allowed_emails", "")
    c, r = _login_as(monkeypatch, "stranger@gmail.com")             # on nobody's list
    assert r.status_code == 307
    assert c.get("/me").json() == {"email": "stranger@gmail.com"}
    with dbmod.central_session() as cs:
        u = users.get_or_create(cs, "stranger@gmail.com", login=False)
        assert u.role == "member" and u.data_key == f"u{u.id}"
    with dbmod.open_user_session(u.data_key) as db:                  # their token sits in THEIR database
        from app.models import Owner
        assert db.scalar(select(Owner)).email == "stranger@gmail.com"


def test_invited_mode_still_turns_strangers_away(monkeypatch):
    monkeypatch.setattr(settings, "open_signup", False)
    _, r = _login_as(monkeypatch, "stranger@gmail.com")
    assert r.status_code == 403


def test_open_signup_refuses_unverified_addresses_and_a_full_pilot(monkeypatch):
    monkeypatch.setattr(settings, "open_signup", True)
    _, r = _login_as(monkeypatch, "fake@gmail.com", verified=False)
    assert r.status_code == 403
    monkeypatch.setattr(settings, "max_users", 1)
    assert _login_as(monkeypatch, "first@gmail.com")[1].status_code == 307
    _, full = _login_as(monkeypatch, "second@gmail.com")
    assert full.status_code == 403 and "full" in full.json()["detail"]
    assert _login_as(monkeypatch, "first@gmail.com")[1].status_code == 307   # the first one can always come back


def test_deleting_the_account_signs_the_user_out(monkeypatch):
    monkeypatch.setattr(settings, "open_signup", True)
    c, _ = _login_as(monkeypatch, "leaver@gmail.com")
    r = c.delete("/account")
    assert r.json() == {"ok": True} and "session=" in r.headers.get("set-cookie", "") and "Max-Age=0" in r.headers["set-cookie"]
