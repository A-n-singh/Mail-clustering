"""Google sign-in for the single owner. Only OWNER_EMAIL may log in."""
from fastapi import Cookie, Depends, HTTPException
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


def make_session(email: str) -> str:
    return _signer().dumps({"email": email})


def current_user(session: str | None = Cookie(default=None)) -> str:
    try:
        data = _signer().loads(session or "", max_age=MAX_AGE)
    except BadSignature:
        raise HTTPException(401, "not signed in")
    if data["email"].lower() != settings.owner_email.lower():
        raise HTTPException(403, "not allowed")
    return data["email"]


def is_allowed(email: str) -> bool:
    return bool(settings.owner_email) and email.lower() == settings.owner_email.lower()


def build_flow():
    from google_auth_oauthlib.flow import Flow
    cfg = {"web": {"client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
                   "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                   "token_uri": "https://oauth2.googleapis.com/token",
                   "redirect_uris": [settings.oauth_redirect_uri]}}
    return Flow.from_client_config(cfg, scopes=scopes(), redirect_uri=settings.oauth_redirect_uri)
