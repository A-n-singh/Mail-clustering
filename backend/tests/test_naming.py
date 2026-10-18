from datetime import datetime, timezone

import pytest

from app import naming
from app.grouping import subject_key
from app.models import Message


def msgs(rows):
    return [Message(message_id=f"m{i}", thread_id="t", sender=s, subject=subj, subject_key=subject_key(subj),
                    date=datetime(2026, 1, 1, tzinfo=timezone.utc)) for i, (s, subj) in enumerate(rows)]


class Fake:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def name(self, subjects, senders, attempt):
        self.calls.append((subjects, senders, attempt))
        a = self.answers.pop(0) if self.answers else None
        if isinstance(a, Exception):
            raise a
        return a


class Boom:
    def name(self, *a):
        raise AssertionError("the model must not be called for this cluster")


AMAZON = [("Amazon <o@amazon.com>", f"Your order #{1000 + i} has shipped") for i in range(29)]
MIXED = [("HDFC Bank <a@hdfc.net>", s) for s in ["Statement ending 4821 is ready"] * 10 + ["New sign-in to your netbanking"] * 8
         + ["Your credit card payment is due"] * 8]


def test_clean_heading_drops_order_numbers_and_prefixes():
    assert naming.clean_heading("Re: Your order #33532 has shipped") == "Your order has shipped"
    assert naming.clean_heading("Invoice INV-2024-77 attached") == "Invoice attached"
    assert naming.clean_heading("12345") == "12345"          # never returns nothing


def test_one_dominant_subject_type_is_the_heading_and_skips_the_model():
    r = naming.name_cluster(msgs(AMAZON), set(), Boom())
    assert (r.label, r.source) == ("Your order has shipped", "template")


def test_router_threshold_is_70_percent():
    rows = [("A <a@x.com>", "Lunch on Friday?")] * 7 + [("A <a@x.com>", "Report due tomorrow")] * 3     # 70%
    assert naming.name_cluster(msgs(rows), set(), Boom()).source == "template"
    rows = [("A <a@x.com>", "Lunch on Friday?")] * 6 + [("A <a@x.com>", "Report due tomorrow")] * 3     # 67%
    assert naming.name_cluster(msgs(rows), set(), None).source == "fallback"


def test_mixed_cluster_uses_the_model_when_the_answer_passes():
    f = Fake("Credit card and statement alerts")
    r = naming.name_cluster(msgs(MIXED), set(), f)
    assert (r.label, r.source) == ("Credit card and statement alerts", "model") and len(f.calls) == 1
    subjects, senders, attempt = f.calls[0]
    assert subjects[0] == "Statement ending is ready" and senders == ["HDFC Bank"] and attempt == 0   # typical subjects, no numbers


@pytest.mark.parametrize("bad,why", [
    ("", "empty"), (None, "empty"),
    ("a very long heading that goes on and on forever", "too long"),
    ("Statement 4821", "has numbers"),
    ("HDFC Bank", "just a sender name"),
    ("Urgent deadline alerts", "alarm words but no urgent mail here"),
    ("Holiday travel plans", "shares no word with the subjects"),
    ("HDFC Bank offers", "shares no word with the subjects"),        # only the sender's name matches
    ("Statement ending is ready", "copies one subject of a mixed group"),
])
def test_checker_rejects(bad, why):
    facts = naming.facts_of(msgs(MIXED), set())
    assert naming.check(naming.tidy(bad), facts) == why


def test_checker_accepts_alarm_words_only_when_urgent_mail_exists():
    m = msgs(MIXED)
    assert naming.check("Payment deadline alerts", naming.facts_of(m, set())) == "alarm words but no urgent mail here"
    assert naming.check("Payment deadline alerts", naming.facts_of(m, {"m0"})) is None


def test_a_rejected_answer_gets_one_retry_with_another_prompt():
    f = Fake("Holiday travel plans", "Statement and payment alerts")
    r = naming.name_cluster(msgs(MIXED), set(), f)
    assert (r.label, r.source) == ("Statement and payment alerts", "model") and [c[2] for c in f.calls] == [0, 1]


def test_two_rejections_fall_back_to_the_most_typical_real_subject():
    f = Fake("Holiday travel plans", "Holiday travel plans")
    r = naming.name_cluster(msgs(MIXED), set(), f)
    assert (r.label, r.source) == ("Statement ending is ready", "fallback") and len(f.calls) == 2


def test_a_crashing_model_never_breaks_naming():
    r = naming.name_cluster(msgs(MIXED), set(), Fake(RuntimeError("boom"), RuntimeError("boom")))
    assert (r.label, r.source) == ("Statement ending is ready", "fallback")


def test_no_model_means_fallback_for_mixed_clusters():
    assert naming.name_cluster(msgs(MIXED), set(), None).source == "fallback"


def test_tidy_takes_the_first_line_and_strips_quotes():
    assert naming.tidy('"Bank alerts."\nBecause these are…') == "Bank alerts"
    assert naming.tidy("   ") is None and naming.tidy(None) is None


def test_rename_keeps_summaries_and_old_label_when_nothing_found(db):
    ms = msgs(AMAZON)
    for m in ms:
        db.add(m)
    db.commit()
    out = naming.rename(db, {1: [m.message_id for m in ms], 2: []}, {m.message_id: m for m in ms},
                        {1: ("order · amazon", "29 mails"), 2: ("x", "0 mails")}, namer=None)
    assert out[1] == ("Your order has shipped", "29 mails") and out[2] == ("x", "0 mails")
