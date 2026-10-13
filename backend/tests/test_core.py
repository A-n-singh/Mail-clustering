import pytest
from sqlalchemy import func, select

from app import actions as act
from app.cleaning import clean_body
from app.models import Action, Cluster, Message, Score
from app.sync import run_sync
from conftest import FakeGmail, mail


def seeded(db):
    g = FakeGmail([
        mail("1", "Boss <boss@corp.com>", "Report due tomorrow", "Please send the report. Deadline is tomorrow."),
        mail("2", "Shop <deals@shop.com>", "50% off sale", "Big sale", unsub="<mailto:u@shop.com>"),
        mail("3", "Shop <deals@shop.com>", "Newsletter", "News", unsub="<mailto:u@shop.com>"),
        mail("4", "Ann <ann@corp.com>", "Lunch?", "Can you make it on Friday?"),
    ])
    run_sync(db, g)
    return g


def test_clean_body_strips_quotes_and_signature():
    t = "Thanks!\n> old text\nOn Mon, Bob wrote:\nstuff\n"
    assert clean_body(t) == "Thanks!"
    assert clean_body("<p>Hi</p><blockquote>old</blockquote>", is_html=True) == "Hi"


def test_sync_groups_and_scores(db):
    seeded(db)
    labels = {c.label: c.size for c in db.scalars(select(Cluster))}
    assert labels == {"corp.com": 2, "shop.com": 2}
    cls = {s.message_id: s.cls for s in db.scalars(select(Score))}
    assert cls == {"1": "urgent", "2": "junk", "3": "junk", "4": "needs_reply"}


def test_sync_is_idempotent_and_incremental(db):
    g = seeded(db)
    run_sync(db, g)  # incremental, no changes
    assert db.scalar(select(func.count()).select_from(Message)) == 4
    g.changes = ([], ["3"])
    run_sync(db, g)
    assert db.get(Message, "3") is None
    assert {c.label: c.size for c in db.scalars(select(Cluster))}["shop.com"] == 1


def test_expired_history_falls_back_to_full_sync(db):
    g = seeded(db)
    g.changes = None
    assert run_sync(db, g).fetched == 4


def test_single_action_and_undo(db):
    g = seeded(db)
    b = act.perform(db, g, ["2"], "archive")
    assert "INBOX" not in db.get(Message, "2").labels
    assert db.scalar(select(Action)).previous_labels == ["INBOX", "UNREAD"]
    act.undo(db, g, b)
    assert db.get(Message, "2").labels == ["INBOX", "UNREAD"]


def test_trash_and_undo_restores(db):
    g = seeded(db)
    b = act.perform(db, g, ["2"], "trash")
    assert ("trash", "2") in g.calls and "TRASH" in db.get(Message, "2").labels
    act.undo(db, g, b)
    assert ("untrash", "2") in g.calls
    assert db.get(Message, "2").labels == ["INBOX", "UNREAD"]


def test_bulk_needs_confirm_and_blocks_protected(db):
    g = seeded(db)
    with pytest.raises(act.ActionError):
        act.perform(db, g, ["2", "3"], "trash")
    act.perform(db, g, ["2", "3"], "trash", confirmed=True)
    with pytest.raises(act.ActionError):
        act.perform(db, g, ["1", "4"], "trash", confirmed=True)
    assert act.bulk_preview(db, ["1", "2"])["blocked"] is True


def test_custom_label(db):
    g = seeded(db)
    act.perform(db, g, ["1"], "label", "Follow up")
    assert "Label_Follow up" in db.get(Message, "1").labels
