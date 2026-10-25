"""Google sign-in. With OPEN_SIGNUP any Google account may log in, otherwise only OWNER_EMAIL and ALLOWED_EMAILS; each gets their own mailbox database."""
from dataclasses import dataclass

from fastapi import Cookie, HTTPException
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .config import settings

_BASE_SCOPES = ["openid", "https://www.googleapis.com/auth/userinfo.email"]


def scopes() -> list[str]:
    gmail = "https://mail.google.com/" if settings.allow_permanent_delete else "https://www.googleapis.com/auth/gmail.modify"
    return _BASE_SCOPES + [gmail]
COOKIE = "session"
MAX_AGE = 60 * 60 * 24 * 7


def _signer():
    return URLSafeTimedSerializer(settings.session_secret, salt="session")


OAUTH_COOKIE = "oauth"


def make_oauth_cookie(state: str, verifier: str | None) -> str:
    """Remembers the sign-in's state (CSRF check) and PKCE verifier between /auth/login and /auth/callback."""
    return URLSafeTimedSerializer(settings.session_secret, salt="oauth").dumps({"state": state, "verifier": verifier})


def read_oauth_cookie(value: str | None) -> dict:
    try:
        return URLSafeTimedSerializer(settings.session_secret, salt="oauth").loads(value or "", max_age=600)
    except BadSignature:
        raise HTTPException(400, "Sign-in expired or was not started here. Press Sign in with Google again.")


def owner_email() -> str:
    return settings.owner_email.strip().lower()


def allowed_emails() -> set[str]:
    return {e.strip().lower() for e in settings.allowed_emails.split(",") if e.strip()} | ({owner_email()} - {""})


def is_allowed(email: str) -> bool:
    """Open sign-up: any Google account (it still has to sign in through Google). Otherwise only the owner and ALLOWED_EMAILS."""
    email = email.strip().lower()
    if settings.open_signup:
        return "@" in email
    return email in allowed_emails()


def is_owner(email: str) -> bool:
    return bool(owner_email()) and email.strip().lower() == owner_email()


@dataclass(frozen=True)
class Principal:
    """Who is making this request. `data_key` picks that user's own database (and their own running-job flags)."""
    email: str
    uid: int
    data_key: str
    is_owner: bool


def make_session(email: str, uid: int = 0, data_key: str | None = None) -> str:
    dk = data_key or ("main" if is_owner(email) else f"u{uid}")
    return _signer().dumps({"email": email, "uid": uid, "dk": dk})


async def current_user(session: str | None = Cookie(default=None)) -> Principal:
    """Reads the signed cookie (only issued after a Google sign-in). In invited mode the allow-list is checked on every request, so removing an
    address from ALLOWED_EMAILS locks that person out at once. (async on purpose: it also selects this user's job flags.)"""
    try:
        data = _signer().loads(session or "", max_age=MAX_AGE)
    except BadSignature:
        raise HTTPException(401, "not signed in")
    email = data["email"]
    if not is_allowed(email):
        raise HTTPException(403, "not allowed")
    dk = data.get("dk") or ("main" if is_owner(email) else None)  # cookies from before several users existed: the owner only
    if dk is None:
        raise HTTPException(401, "please sign in again")
    from . import control
    control.use(dk)
    return Principal(email=email, uid=int(data.get("uid") or 0), data_key=dk, is_owner=is_owner(email))


def build_flow():
    from google_auth_oauthlib.flow import Flow
    cfg = {"web": {"client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
                   "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                   "token_uri": "https://oauth2.googleapis.com/token",
                   "redirect_uris": [settings.oauth_redirect_uri]}}
    return Flow.from_client_config(cfg, scopes=scopes(), redirect_uri=settings.oauth_redirect_uri)
