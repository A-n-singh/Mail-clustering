"""Run the API with a fake in-memory Gmail so the dashboard can be tried without Google credentials.
    cd backend && python demo.py     (then `npm run dev` in frontend/ and open http://localhost:5173)
Not used in production."""
import os, random
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("OWNER_EMAIL", "demo@example.com")
os.environ.setdefault("SESSION_SECRET", "demo")
os.environ.setdefault("SYNC_INTERVAL_MINUTES", "0")
from datetime import datetime, timedelta, timezone

import uvicorn
from fastapi import Response
from sqlalchemy.pool import StaticPool

from app import auth, db as dbmod, main as m
from app.gmail import RawMail
from app.models import Owner


class DemoGmail:
    """Just enough of GmailClient to behave like a mailbox that lives in memory."""
    def __init__(self):
        random.seed(1)
        now = datetime.now(timezone.utc)
        senders = [("Amazon", "orders@amazon.com", ["Your order #{n} has shipped", "Delivered: order #{n}"], True, 6000),
                   ("Swiggy", "offers@swiggy.in", ["{n}% off your next order", "Weekend deals just for you"], True, 40000),
                   ("LinkedIn", "news@linkedin.com", ["You appeared in {n} searches", "Jobs you may like"], True, 25000),
                   ("HDFC Bank", "alerts@hdfcbank.net", ["Statement for account ending {n}", "Action required: verify your account"], True, 90000),
                   ("Priya Sharma", "priya@corp.com", ["Can you review the deck?", "Lunch Friday?", "Report due tomorrow"], False, 3000),
                   ("Rahul", "rahul@gmail.com", ["Photos from the trip", "Are you coming?"], False, 2_500_000)]
        self.mails, i = {}, 0
        for name, addr, subjects, bulk, size in senders:
            for k in range(random.randint(30, 140) if bulk else random.randint(4, 9)):
                i += 1
                subj = random.choice(subjects).format(n=random.randint(100, 99999) if "{n}" in "".join(subjects) else 0)
                self.mails[f"m{i}"] = RawMail(f"m{i}", f"t{i}", f"{name} <{addr}>", subj, now - timedelta(hours=i * 3),
                                              ["INBOX", "UNREAD"], subj, subj + " ...", False,
                                              "<https://example.com/unsub>" if bulk else None, {}, size)
        self.history = "1"
    def search(self, query, limit):
        import re
        w = re.search(r"from:(\S+)", query)
        ids = [x.message_id for x in sorted(self.mails.values(), key=lambda x: x.date, reverse=True)
               if "TRASH" not in x.labels and (not w or w.group(1) in x.sender)]
        return ids[:limit], len(ids)
    def get_message(self, mid): return self.mails[mid]
    def profile_history_id(self): return self.history
    def history_changes(self, start): return [], []
    def modify_labels(self, mid, add, rem):
        l = self.mails[mid].labels
        self.mails[mid].labels = [x for x in l if x not in rem] + [x for x in add if x not in l]
    def trash(self, mid): self.mails[mid].labels = ["TRASH"]
    def untrash(self, mid): self.mails[mid].labels = ["INBOX"]
    def ensure_label(self, name): return "Label_" + name
    def delete_permanently(self, ids):
        for i in ids: self.mails.pop(i, None)


gmail = DemoGmail()
engine = dbmod.make_engine("sqlite://")
engine.dispose()
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
dbmod.Base.metadata.create_all(eng)
S = sessionmaker(bind=eng, expire_on_commit=False)
with S() as s:
    s.add(Owner(id=1, email="demo@example.com", refresh_token_enc="x")); s.commit()
m.SessionLocal = S
m.app.dependency_overrides[dbmod.get_db] = lambda: (yield from _gen())
def _gen():
    s = S()
    try: yield s
    finally: s.close()
m.app.dependency_overrides[dbmod.get_db] = _gen
m.app.dependency_overrides[m.get_gmail] = lambda: gmail
m.build_gmail = lambda db: gmail

m.app.router.routes[:] = [r for r in m.app.router.routes if getattr(r, "path", "") != "/auth/login"]

@m.app.get("/auth/login")
def demo_login():
    from fastapi.responses import RedirectResponse
    r = RedirectResponse("http://localhost:5173"); r.set_cookie("session", auth.make_session("demo@example.com")); return r

if __name__ == "__main__":
    uvicorn.run(m.app, port=8000)
