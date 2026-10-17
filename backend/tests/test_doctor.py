from types import SimpleNamespace

from cryptography.fernet import Fernet

from app.doctor import FAIL, OK, WARN, check_config


def cfg(**kw):
    base = dict(google_client_id="123-abc.apps.googleusercontent.com", google_client_secret="GOCSPX-x", owner_email="me@gmail.com",
                token_key=Fernet.generate_key().decode(), session_secret="s" * 40,
                oauth_redirect_uri="https://inbox.duckdns.org/auth/callback", frontend_url="https://inbox.duckdns.org",
                allow_permanent_delete=False)
    base.update(kw)
    return SimpleNamespace(**base)


def levels(s):
    return {name: lvl for lvl, name, _ in check_config(s)}


def test_good_config_has_no_failures():
    assert FAIL not in levels(cfg()).values()


def test_empty_config_reports_each_problem_without_leaking_values():
    s = cfg(google_client_id="", google_client_secret="", owner_email="", token_key="", session_secret="change-me")
    lv = levels(s)
    assert [lv[k] for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "OWNER_EMAIL", "TOKEN_KEY", "SESSION_SECRET")] == [FAIL] * 5
    out = " ".join(m for _, _, m in check_config(cfg()))
    assert "GOCSPX-x" not in out and "s" * 40 not in out


def test_redirect_rules():
    assert levels(cfg(oauth_redirect_uri="http://inbox.duckdns.org/auth/callback"))["OAUTH_REDIRECT_URI"] == FAIL     # http on a public host
    assert levels(cfg(oauth_redirect_uri="https://inbox.duckdns.org/callback"))["OAUTH_REDIRECT_URI"] == FAIL         # wrong path
    assert levels(cfg(frontend_url="https://other.example.com"))["FRONTEND_URL"] == FAIL                              # cookie would be lost
    assert levels(cfg(oauth_redirect_uri="http://localhost:8000/auth/callback", frontend_url="http://localhost:5173"))["OAUTH_REDIRECT_URI"] == WARN
    assert levels(cfg(allow_permanent_delete=True))["ALLOW_PERMANENT_DELETE"] == WARN
