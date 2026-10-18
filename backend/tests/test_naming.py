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
    assert subjects[0] == ("Statement ending is ready", 10) and senders == ["HDFC Bank"] and attempt == 0   # typical subjects, no numbers, with counts


@pytest.mark.parametrize("bad,why", [
    ("", "empty"), (None, "empty"),
    ("a very long heading that goes on and on forever", "too long"),
    ("Statement 4821", "has numbers"),
    ("HDFC Bank", "just a sender name"),
    ("Urgent deadline alerts", "alarm words but no urgent mail here"),
    ("Holiday travel plans", "shares no word with the subjects and is not close to them in meaning"),
    ("HDFC Bank offers", "shares no word with the subjects and is not close to them in meaning"),   # only the sender's name matches
    ("Statement ending is ready", "copies one subject of a mixed group"),
])
def test_checker_rejects(bad, why):
    facts = naming.facts_of(msgs(MIXED), set())
    assert naming.check(naming.tidy(bad), facts) == why


class StubEmbedder:
    """Maps a text to a fixed vector, so closeness is predictable: 'bank'-ish texts point one way, others another."""
    def embed(self, texts):
        import numpy as np
        return np.array([[1.0, 0.0] if any(w in t.lower() for w in ("statement", "sign-in", "payment", "bank", "account")) else [0.0, 1.0]
                         for t in texts], dtype=np.float32)


def test_close_in_meaning_passes_when_no_word_is_shared():
    facts = naming.facts_of(msgs(MIXED), set())
    close = naming._closeness(StubEmbedder(), ["Statement ending is ready", "Your credit card payment is due"])
    assert naming.check("Account notifications", facts) == "shares no word with the subjects and is not close to them in meaning"
    assert naming.check("Account notifications", facts, close) is None            # 'account' is close in meaning
    assert naming.check("Holiday travel plans", facts, close) == "shares no word with the subjects and is not close to them in meaning"


def test_closeness_is_off_without_an_embedder_and_survives_a_broken_one():
    assert naming._closeness(None, ["x"]) is None

    class Broken:
        def embed(self, t):
            raise RuntimeError("no model")
    assert naming._closeness(Broken(), ["x"]) is None


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


class BowEmbedder:
    """Bag-of-words hashing: texts that share most of their words are close. Deterministic, offline."""
    def embed(self, texts):
        import zlib

        import numpy as np
        out = np.zeros((len(texts), 512), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in t.lower().replace("?", " ").split():
                out[i, zlib.crc32(w.encode()) % 512] += 1
        return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)


@pytest.fixture
def merge_at(monkeypatch):
    monkeypatch.setattr(naming.settings, "title_merge_similarity", 0.85)      # bag-of-words scores are lower than real embeddings


SHIPPED = ([("A <a@x.com>", "Your order has shipped")] * 25 + [("A <a@x.com>", "Your order has been shipped")] * 20
           + [("A <a@x.com>", "Refund processed")] * 10)


def test_merge_counts_rewordings_as_one_type(merge_at):
    facts = naming.facts_of(msgs(SHIPPED), set(), BowEmbedder())
    assert [(g.sample, g.count) for g in facts.groups] == [("Your order has shipped", 45), ("Refund processed", 10)]
    assert len(facts.templates) == 3                       # the exact types are still known, for the checker


def test_merge_lets_the_router_name_a_topic_the_exact_count_would_send_to_the_model(merge_at):
    assert naming.name_cluster(msgs(SHIPPED), set(), None, None).source == "fallback"                 # 25 of 55 = 45%
    r = naming.name_cluster(msgs(SHIPPED), set(), Boom(), BowEmbedder())                              # 45 of 55 = 82%
    assert (r.label, r.source) == ("Your order has shipped", "template")


def test_merge_never_joins_different_themes(merge_at):
    rows = [("A <a@x.com>", "Your order has shipped")] * 20 + [("A <a@x.com>", "Delivered: order")] * 20 + [("A <a@x.com>", "Refund processed")] * 20
    facts = naming.facts_of(msgs(rows), set(), BowEmbedder())
    assert len(facts.groups) == 3 and facts.top_share < 0.7


def test_merge_is_off_without_an_embedder_or_with_a_broken_one(merge_at):
    assert len(naming.facts_of(msgs(SHIPPED), set(), None).groups) == 3

    class Broken:
        def embed(self, t):
            raise RuntimeError("no model")
    assert len(naming.facts_of(msgs(SHIPPED), set(), Broken()).groups) == 3


THEMES = [("Your order has shipped", 30), ("Your parcel is out for delivery", 25), ("Delivered: your package", 20),
          ("Rate your recent purchase", 15), ("Order confirmation received", 10), ("Refund processed", 8), ("Payment failed", 6)]


def test_pick_by_meaning_gives_a_small_theme_a_seat(merge_at):
    rows = [("A <a@x.com>", s) for s, n in THEMES for _ in range(n)]
    f = Fake("Order and refund updates")
    naming.name_cluster(msgs(rows), set(), f, BowEmbedder())
    shown = dict(f.calls[0][0])
    assert len(shown) == 5 and shown["Your order has shipped"] == 30                                  # biggest first, with its count
    assert "Refund processed" in shown and "Payment failed" in shown                                 # small, different themes are not left out


def test_without_an_embedder_the_model_sees_the_five_biggest():
    rows = [("A <a@x.com>", s) for s, n in THEMES for _ in range(n)]
    f = Fake("Order updates")
    naming.name_cluster(msgs(rows), set(), f, None)
    assert [s for s, _ in f.calls[0][0]] == [t[0] for t in THEMES[:5]]


def test_the_prompt_shows_counts():
    p = naming._prompt([("Your order has shipped", 55), ("Refund processed", 3)], ["Amazon"])
    assert "- Your order has shipped (55 mails)" in p and "- Refund processed (3 mails)" in p and p.endswith("Heading:")


def test_a_stray_one_off_does_not_take_a_seat(merge_at):
    rows = [("A <a@x.com>", s) for s, n in THEMES for _ in range(n)] + [("A <a@x.com>", "Quokka zebra xylophone")]
    f = Fake("Order updates")
    naming.name_cluster(msgs(rows), set(), f, BowEmbedder())
    assert "Quokka zebra xylophone" not in dict(f.calls[0][0])                                       # 1 of 115 mails, under 4%
