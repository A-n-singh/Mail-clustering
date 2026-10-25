"""The list of users (central `users` table). Who may sign in is decided by auth.is_allowed(); this module records them and
gives each one a database of their own."""
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import auth
from .config import settings
from .models import User


class PilotFull(Exception):
    pass


def data_key_for(email: str, uid: int) -> str:
    """The owner keeps the main database (an existing install needs no move); everyone else gets their own file."""
    return "main" if auth.is_owner(email) else f"u{uid}"


def get_or_create(central: Session, email: str, login: bool = True) -> User:
    key = email.strip().lower()
    u = central.scalar(select(User).where(User.email == key))
    if u is None:
        if not auth.is_owner(key) and (central.scalar(select(func.count()).select_from(User)) or 0) >= settings.max_users:
            raise PilotFull(f"The pilot is full ({settings.max_users} users).")
        u = User(email=key, data_key="", role="owner" if auth.is_owner(key) else "member")
        central.add(u)
        central.flush()  # gets the id
        u.data_key = data_key_for(key, u.id)
    if login:
        u.last_login_at = datetime.now(timezone.utc)
    central.commit()
    return u


def ensure_owner(central: Session):
    """So the owner's mail is synced from the first start, even before anyone signs in again."""
    if auth.owner_email():
        get_or_create(central, auth.owner_email(), login=False)


def all_users(central: Session) -> list[User]:
    return list(central.scalars(select(User).order_by(User.id)))


def forget(central: Session, email: str):
    u = central.scalar(select(User).where(User.email == email.strip().lower()))
    if u:
        central.delete(u)
        central.commit()
