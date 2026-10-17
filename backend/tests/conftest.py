import os
os.environ.update(DATABASE_URL="sqlite://", OWNER_EMAIL="me@example.com", SESSION_SECRET="s")
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.gmail import RawMail


class FakeGmail:
    deleted = None
    def __init__(self, mails):
        self.mails = {m.message_id: m for m in mails}
        self.history = "100"
        self.changes = ([], [])
        self.calls = []

    def search(self, query, limit):
        import re
        want = re.search(r"from:(\S+)", query)
        ids = [m.message_id for m in sorted(self.mails.values(), key=lambda m: m.date, reverse=True)
               if "TRASH" not in m.labels and (not want or want.group(1) in m.sender)]
        return ids[:limit], len(ids)
    def get_message(self, mid): return self.mails[mid]
    def profile_history_id(self): return self.history
    def history_changes(self, start): return self.changes
    def modify_labels(self, mid, add, remove): self.calls.append(("modify", mid, add, remove))
    def trash(self, mid): self.calls.append(("trash", mid))
    def untrash(self, mid): self.calls.append(("untrash", mid))
    def ensure_label(self, name): return "Label_" + name
    def delete_permanently(self, ids):
        self.deleted = (self.deleted or []) + list(ids)
        for i in ids:
            self.mails.pop(i, None)


def mail(i, sender="Bob <bob@acme.com>", subject="hi", body="hello", unsub=None, labels=("INBOX", "UNREAD"), day=1, size=1000):
    return RawMail(i, "t" + i, sender, subject, datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=day),
                   list(labels), body[:20], body, False, unsub, {}, size)


@pytest.fixture
def db():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(e)
    s = sessionmaker(bind=e, expire_on_commit=False)()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _reset_stop_flag():
    from app import control
    control.cancel_sync.clear()
    yield
    control.cancel_sync.clear()
