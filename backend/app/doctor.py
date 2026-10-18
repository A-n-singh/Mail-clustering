"""Plain-language setup check:  python -m app.doctor [--model] [--live]
Never prints secrets, only whether they are set and well-formed.
  --model  also load the embedding model and embed one sentence
  --live   also call Gmail with the saved token (needs network and a completed sign-in)"""
import os
import shutil
import sys
import time
from urllib.parse import urlparse

OK, WARN, FAIL = "ok", "warn", "fail"
ICON = {OK: "✔", WARN: "!", FAIL: "✘"}


def _local(url: str) -> bool:
    return (urlparse(url).hostname or "") in ("localhost", "127.0.0.1")


def check_config(s) -> list[tuple[str, str, str]]:
    r = []
    add = lambda lvl, name, msg: r.append((lvl, name, msg))
    cid = s.google_client_id
    if not cid:
        add(FAIL, "GOOGLE_CLIENT_ID", "empty. Copy the Client ID from Google Cloud > Google Auth Platform > Clients.")
    elif not cid.endswith(".apps.googleusercontent.com"):
        add(WARN, "GOOGLE_CLIENT_ID", "does not end in .apps.googleusercontent.com. Is it the Client ID (not the secret)?")
    else:
        add(OK, "GOOGLE_CLIENT_ID", "set")
    add(OK if s.google_client_secret else FAIL, "GOOGLE_CLIENT_SECRET",
        "set" if s.google_client_secret else "empty. Open the client in Google Cloud and add/copy a secret.")
    owner = s.owner_email
    add(OK if "@" in owner else FAIL, "OWNER_EMAIL", "set" if "@" in owner else "empty or not an email. Only this Google account may sign in.")
    try:
        from cryptography.fernet import Fernet
        Fernet(s.token_key.encode())
        add(OK, "TOKEN_KEY", "valid")
    except Exception:
        add(FAIL, "TOKEN_KEY", "missing or not a valid key. Run deploy/init-env.sh to generate one.")
    sec = s.session_secret
    add(FAIL if sec in ("", "change-me") else WARN if len(sec) < 32 else OK, "SESSION_SECRET",
        "still the default. Run deploy/init-env.sh." if sec in ("", "change-me") else "shorter than 32 characters" if len(sec) < 32 else "set")
    red, front = s.oauth_redirect_uri, s.frontend_url
    if _local(red):
        add(WARN, "OAUTH_REDIRECT_URI", "points at localhost: fine on your own computer, will not work from a phone or tablet.")
    elif not red.startswith("https://"):
        add(FAIL, "OAUTH_REDIRECT_URI", "must start with https:// for a public address (Google rejects plain http).")
    elif not red.endswith("/auth/callback"):
        add(FAIL, "OAUTH_REDIRECT_URI", "must end with /auth/callback.")
    else:
        add(OK, "OAUTH_REDIRECT_URI", f"{red} (add exactly this under Authorized redirect URIs in Google Cloud)")
    ro, fo = urlparse(red), urlparse(front)
    if not _local(red) and (ro.scheme, ro.netloc) != (fo.scheme, fo.netloc):
        add(FAIL, "FRONTEND_URL", f"must be the same site as the redirect address ({ro.scheme}://{ro.netloc}); otherwise the login cookie is lost.")
    else:
        add(OK, "FRONTEND_URL", front)
    if s.allow_permanent_delete:
        add(WARN, "ALLOW_PERMANENT_DELETE", "ON: sign-in asks for full mailbox access and Trash can be erased forever.")
    else:
        add(OK, "ALLOW_PERMANENT_DELETE", "off (safe default)")
    return r


def check_machine(data_dir: str) -> list[tuple[str, str, str]]:
    r = []
    try:
        kb = int(next(l for l in open("/proc/meminfo") if l.startswith("MemTotal")).split()[1])
        gb = kb / 1024 / 1024
        lvl = OK if gb >= 3 else WARN if gb >= 1.8 else FAIL
        r.append((lvl, "Memory", f"{gb:.1f} GB" + ("" if lvl == OK else ". Topic building needs about 3 GB or more; it may crash or be killed.")))
    except Exception:
        r.append((WARN, "Memory", "could not read"))
    free = shutil.disk_usage(data_dir if os.path.isdir(data_dir) else ".").free / 1e9
    r.append((OK if free >= 5 else WARN, "Disk", f"{free:.1f} GB free"))
    return r


def check_database() -> list[tuple[str, str, str]]:
    try:
        from sqlalchemy import select, text
        from .db import Base, SessionLocal, engine
        from .models import Message, Owner
        Base.metadata.create_all(engine)
        with SessionLocal() as db:
            db.execute(text("select 1"))
            owner = db.scalar(select(Owner))
            n = len(db.scalars(select(Message.message_id)).all())
        return [(OK, "Database", f"works; {n} mails stored; " + ("signed in" if owner else "nobody signed in yet"))]
    except Exception as e:
        return [(FAIL, "Database", f"{type(e).__name__}: {e}")]


def check_model() -> list[tuple[str, str, str]]:
    try:
        t = time.time()
        from .config import settings
        from .embeddings import FastEmbedder
        v = FastEmbedder(settings.embed_model).embed(["Your order has shipped"])
        return [(OK, "Embedding model", f"{settings.embed_model} loaded, {v.shape[1]} dimensions, {time.time() - t:.1f}s")]
    except Exception as e:
        return [(FAIL, "Embedding model", f"{type(e).__name__}: {e}. The server needs internet on first use to download it.")]


def check_title_model() -> list[tuple[str, str, str]]:
    """Optional heading model. Not set is fine: mixed topics are then named by their most typical real subject."""
    from .config import settings
    path = settings.title_model_path
    if not path:
        return [(OK, "Heading model", "not set (optional). Mixed topics use their most typical subject as the heading.")]
    if not os.path.isfile(path):
        return [(WARN, "Heading model", f"TITLE_MODEL_PATH points at {path}, which is not a file. Headings fall back to real subjects.")]
    try:
        import llama_cpp  # noqa: F401
    except Exception:
        return [(WARN, "Heading model", "the file exists but llama-cpp-python is not installed (pip install -r requirements-naming.txt).")]
    return [(OK, "Heading model", f"{os.path.basename(path)} ({os.path.getsize(path) // 1_000_000} MB)")]


def check_gmail() -> list[tuple[str, str, str]]:
    try:
        from sqlalchemy import select
        from .db import SessionLocal
        from .main import build_gmail
        from .models import Owner
        with SessionLocal() as db:
            if not db.scalar(select(Owner)):
                return [(WARN, "Gmail", "not signed in yet. Open the site and press Sign in with Google, then run this again.")]
            g = build_gmail(db)
        h = g.profile_history_id()
        return [(OK, "Gmail", f"token works, Gmail API reachable (history id {h})")]
    except Exception as e:
        msg = str(e)
        if "accessNotConfigured" in msg or "has not been used" in msg or "disabled" in msg:
            hint = "The Gmail API is not enabled. Google Cloud > APIs & Services > Library > Gmail API > Enable."
        elif "invalid_grant" in msg:
            hint = "The saved sign-in expired or was revoked. In Testing mode Google expires it after 7 days; sign in again."
        elif "insufficient" in msg.lower() or "scope" in msg.lower():
            hint = "Missing permission. Check Data Access has the gmail.modify scope, then sign in again."
        else:
            hint = "See the message above."
        return [(FAIL, "Gmail", f"{type(e).__name__}: {msg[:160]}. {hint}")]


def main(argv: list[str]) -> int:
    from .config import settings
    rows = check_config(settings) + check_machine(os.path.dirname(settings.database_url.split("///")[-1]) or ".") + check_database()
    rows += check_title_model()
    if "--model" in argv:
        rows += check_model()
    if "--live" in argv:
        rows += check_gmail()
    for lvl, name, msg in rows:
        print(f" {ICON[lvl]} {name:<24} {msg}")
    bad = sum(l == FAIL for l, *_ in rows)
    warn = sum(l == WARN for l, *_ in rows)
    print(f"\n{'All good.' if not bad else f'{bad} problem(s) to fix.'} {warn} warning(s)." + ("" if "--live" in argv else "  (add --live to test Gmail, --model to test the embedding model)"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
