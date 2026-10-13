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


# ---- capped clusters, refill, subject-first grouping ----
from app.capacity import enforce_cap, refill
from app.grouping import group_key, subject_key


def test_subject_key_templates():
    assert subject_key("Re: Your order #12345 has shipped") == subject_key("Your order #99 has shipped")
    assert subject_key("Invoice INV-2024-001 due 5 May") == "invoice # due # may"


def test_free_mail_groups_by_address_company_by_domain():
    assert group_key("a@gmail.com", "gmail.com", "A")[0] == "a@gmail.com"
    assert group_key("x@shop.com", "shop.com", "X") == ("shop.com", "from:shop.com")


def test_subject_beats_body_in_scoring():
    from app.scoring import score_message
    # scary body, boring promo subject from a bulk sender -> still junk
    assert score_message("Shop", "Big sale today", "urgent deadline asap", True, [])[0] == "junk"
    assert score_message("Ann", "Action required: contract", "hi", False, [])[0] == "urgent"


def test_cap_evicts_least_important_oldest_and_protects_urgent(db):
    mails = [mail(f"n{i}", "Shop <d@shop.com>", f"Newsletter {i}", "x", unsub="<u>", day=i) for i in range(5)]
    mails.append(mail("old-urgent", "Shop <d@shop.com>", "Final notice: deadline", "x", day=-50))
    g = FakeGmail(mails)
    run_sync(db, g)
    enforce_cap(db, 3)
    kept = {m.message_id for m in db.scalars(select(Message))}
    assert "old-urgent" in kept            # protected even though oldest
    assert "n0" not in kept and "n1" not in kept and "n4" in kept   # oldest low-score go first
    assert len(kept) == 3
    assert g.mails["n0"].labels == ["INBOX", "UNREAD"]            # Gmail untouched


def test_trash_then_refill_pulls_older_mail(db):
    mails = [mail(f"m{i}", "Shop <d@shop.com>", f"Hello {i}", "x", day=i) for i in range(6)]
    g = FakeGmail(mails)
    run_sync(db, g, cap=4)
    cid = db.scalar(select(Cluster.cluster_id))
    assert {m.message_id for m in db.scalars(select(Message))} == {"m2", "m3", "m4", "m5"}
    act.perform(db, g, ["m5", "m4"], "trash", confirmed=True)
    g.mails["m5"].labels = g.mails["m4"].labels = ["TRASH"]   # what Gmail would now report
    assert refill(db, g, cid, 4) == 2
    live = {m.message_id for m in db.scalars(select(Message)) if not m.in_trash}
    assert live == {"m0", "m1", "m2", "m3"}
    assert db.get(Cluster, cid).size == 4
