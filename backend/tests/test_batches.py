"""Pilot loading rules: one cap for the whole mailbox, refill with older mail, load in small batches, topics grow as it loads."""
from sqlalchemy import select

from app import actions as act
from app.capacity import enforce_user_cap, refill_user, refill_user_in_batches
from app.config import settings
from app.models import Message, MessageTopic, TopicRun
from app.sync import run_sync
from app.topics import after_sync, grown_since_build, progress
from conftest import FakeGmail, mail
from test_topics import FakeEmbedder, TOPICS


def live(db):
    return {m.message_id for m in db.scalars(select(Message)) if not m.in_trash}


def shop(n, **kw):
    return [mail(f"m{i}", f"Shop{i % 4} <d@shop{i % 4}.com>", f"Hello {i}", "x", day=i, **kw) for i in range(n)]


def test_user_cap_drops_least_important_oldest_and_protects_urgent(db):
    mails = shop(8) + [mail("old-urgent", "Boss <b@corp.com>", "Final notice: deadline", "x", day=-50)]
    g = FakeGmail(mails)
    run_sync(db, g)
    assert enforce_user_cap(db, 5) == 4
    kept = live(db)
    assert "old-urgent" in kept and len(kept) == 5     # protected even though it is the oldest
    assert g.mails["m0"].labels == ["INBOX", "UNREAD"]  # Gmail is untouched


def test_user_cap_can_stay_above_cap_when_everything_is_protected(db):
    g = FakeGmail([mail(f"u{i}", "Boss <b@corp.com>", f"Deadline {i}: urgent", "x", day=i) for i in range(4)])
    run_sync(db, g)
    assert enforce_user_cap(db, 2) == 0 and len(live(db)) == 4


def test_sync_applies_user_cap(db):
    run_sync(db, FakeGmail(shop(10)), user_cap=6)
    assert len(live(db)) == 6


def test_refill_pulls_only_older_mail_up_to_the_cap(db):
    g = FakeGmail(shop(10))
    run_sync(db, g, initial_fetch=6)                    # newest 6: m4..m9
    assert live(db) == {f"m{i}" for i in range(4, 10)}
    act.perform(db, g, ["m9", "m8"], "trash", confirmed=True)
    g.mails["m9"].labels = g.mails["m8"].labels = ["TRASH"]
    assert refill_user(db, g, 6) == 2
    assert live(db) == {f"m{i}" for i in range(2, 8)}   # the next older ones, never a newer one
    assert refill_user(db, g, 6) == 0                   # full again: nothing more is pulled, no churn


def test_refill_in_batches_reports_each_step(db):
    g = FakeGmail(shop(14))
    run_sync(db, g, initial_fetch=8)                    # m6..m13
    act.perform(db, g, ["m13", "m12", "m11", "m10", "m9"], "trash", confirmed=True)
    for i in (13, 12, 11, 10, 9):
        g.mails[f"m{i}"].labels = ["TRASH"]
    steps = []
    assert refill_user_in_batches(db, g, 8, 2, on_batch=lambda d, total: steps.append(total)) == 5
    assert steps == [2, 4, 5]
    assert len(live(db)) == 8


def test_sync_calls_on_batch_every_batch_size_mails(db):
    seen = []
    run_sync(db, FakeGmail(shop(23)), batch_size=5, on_batch=lambda d, n: seen.append(n))
    assert seen == [5, 10, 15, 20]


def test_a_failing_batch_hook_does_not_stop_loading(db):
    def boom(d, n):
        raise RuntimeError("model not downloaded")
    run = run_sync(db, FakeGmail(shop(12)), batch_size=5, on_batch=boom)
    assert run.error is None and len(live(db)) == 12


def test_topics_grow_while_loading(db, monkeypatch):
    monkeypatch.setattr(settings, "topic_milestones", "30,60")
    mails, i = [], 0
    for _, (senders, subjects) in TOPICS.items():
        for k in range(30):
            i += 1
            name, addr = senders[(k // 5 + k) % 3]
            mails.append(mail(f"m{i}", f"{name} <{addr}>", subjects[k % 3] + f" {k}", "x", day=i))
    e = FakeEmbedder()
    builds = []

    def hook(d, n):
        before = d.scalar(select(TopicRun.id).order_by(TopicRun.id.desc()))
        progress(d, e)
        if d.scalar(select(TopicRun.id).order_by(TopicRun.id.desc())) != before:
            builds.append(n)

    run_sync(db, FakeGmail(mails), batch_size=15, on_batch=hook)
    assert builds == [30, 60]                           # rebuilt from scratch only at the milestones
    placed = db.scalars(select(MessageTopic.message_id)).all()
    assert len(placed) == 90                            # every batch was placed as it arrived (the last one by assign_new)
    assert grown_since_build(db)                        # 90 mails vs 60 at the last build: after_sync will tidy up once


def test_after_sync_rebuilds_when_the_mailbox_grew_a_lot(db, monkeypatch):
    monkeypatch.setattr(settings, "topic_milestones", "30")
    g = FakeGmail([mail(f"m{i}", f"Shop{i % 3} <d@shop{i % 3}.com>", f"flight booking {i}", "x", day=i) for i in range(40)])
    run_sync(db, g, batch_size=10, on_batch=lambda d, n: progress(d, FakeEmbedder()))
    runs = db.scalar(select(TopicRun.id).order_by(TopicRun.id.desc()))
    g.mails.update({f"x{i}": mail(f"x{i}", f"Shop{i % 3} <d@shop{i % 3}.com>", f"flight booking x{i}", "x", day=100 + i) for i in range(40)})
    g.changes = ([f"x{i}" for i in range(40)], [])      # Gmail reports the new mail
    run_sync(db, g)
    assert grown_since_build(db)
    after_sync(db, FakeEmbedder())
    assert db.scalar(select(TopicRun.id).order_by(TopicRun.id.desc())) > runs
