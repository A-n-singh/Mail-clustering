import os
os.environ.update(DATABASE_URL="sqlite://", OWNER_EMAIL="me@example.com", SESSION_SECRET="s")
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.gmail import RawMail


class FakeGmail:
    def __init__(self, mails):
        self.mails = {m.message_id: m for m in mails}
        self.history = "100"
        self.changes = ([], [])
        self.calls = []

    def list_message_ids(self, days): return list(self.mails)
    def get_message(self, mid): return self.mails[mid]
    def profile_history_id(self): return self.history
    def history_changes(self, start): return self.changes
    def modify_labels(self, mid, add, remove): self.calls.append(("modify", mid, add, remove))
    def trash(self, mid): self.calls.append(("trash", mid))
    def untrash(self, mid): self.calls.append(("untrash", mid))
    def ensure_label(self, name): return "Label_" + name


def mail(i, sender="Bob <bob@acme.com>", subject="hi", body="hello", unsub=None, labels=("INBOX", "UNREAD")):
    return RawMail(i, "t" + i, sender, subject, datetime(2026, 1, 1, tzinfo=timezone.utc),
                   list(labels), body[:20], body, False, unsub, {})


@pytest.fixture
def db():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(e)
    s = sessionmaker(bind=e, expire_on_commit=False)()
    yield s
    s.close()
