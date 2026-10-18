import zlib

import numpy as np
from sqlalchemy import func, select

from app import actions as act
from app.config import settings
from app.embeddings import Embedder, ensure_embeddings, message_text
from app.models import Embedding, Message, MessageTopic, Topic
from app.sync import run_sync
from app.topics import after_sync, assign_new, recluster, recluster_due
from conftest import FakeGmail, mail


class FakeEmbedder:
    """Bag-of-words hashing: mails that share words land close together. Fast, deterministic, offline."""
    name = "fake-hash"

    def embed(self, texts):
        out = np.zeros((len(texts), 1024), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in t.lower().replace(".", " ").split():
                out[i, zlib.crc32(w.encode()) % 1024] += 1
        return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)


TOPICS = {
    "travel": ([("Indigo", "bookings@indigo.in"), ("MakeMyTrip", "trips@mmt.com"), ("Airline", "noreply@air.in")],
               ["Flight booking confirmed", "Your trip itinerary flight", "Boarding pass flight booking"]),
    "food": ([("Swiggy", "orders@swiggy.in"), ("Zomato", "hello@zomato.com"), ("Dominos", "hi@dominos.in")],
             ["Your food order delivered", "Restaurant food offer tonight", "Rate your food delivery"]),
    "bank": ([("HDFC", "alerts@hdfc.net"), ("ICICI", "alerts@icici.com"), ("Axis", "alerts@axis.in")],
             ["Account statement ready", "Debit transaction alert account", "Credit card statement account"]),
}


def build(db, extra=()):
    mails, i = [], 0
    for _, (senders, subjects) in TOPICS.items():
        for k in range(30):
            i += 1
            name, addr = senders[(k // 5 + k) % 3]   # senders and subjects are mixed, like a real inbox
            mails.append(mail(f"m{i}", f"{name} <{addr}>", subjects[k % 3] + f" {k}", "x", day=i))
    words = "quokka zebra xylophone pottery glaze kiln origami sundial trebuchet lighthouse marmalade cartography".split()
    for j in range(5):  # unrelated one-offs, each with its own vocabulary
        mails.append(mail(f"odd{j}", f"P{j} <p{j}@x{j}.io>", f"{words[j]} {words[j + 4]} {words[(j * 3) % 12]}", "x", day=50 + j))
    g = FakeGmail(list(mails) + list(extra))
    run_sync(db, g)
    return g


def topic_of(db, mid):
    return db.get(MessageTopic, mid).topic_id


def test_message_text_puts_sender_and_subject_first(db):
    build(db)
    t = message_text(db.get(Message, "m1"))
    assert t.startswith("Indigo. Flight booking confirmed")


def test_embeddings_are_stored_once(db):
    build(db)
    e = FakeEmbedder()
    assert ensure_embeddings(db, e) == 95
    assert ensure_embeddings(db, e) == 0
    assert db.scalar(select(func.count()).select_from(Embedding)) == 95


def test_recluster_groups_by_topic_across_senders(db):
    build(db)
    run = recluster(db, FakeEmbedder())
    assert run.error is None and run.topics >= 3
    true = {"travel": range(1, 31), "food": range(31, 61), "bank": range(61, 91)}
    cluster_of = {m: topic_of(db, m) for r in true.values() for m in map(lambda i: f"m{i}", r)}
    # purity: no cluster mixes two real topics
    for tid in {t for t in cluster_of.values() if t is not None}:
        kinds = {name for name, r in true.items() for i in r if cluster_of[f"m{i}"] == tid}
        assert len(kinds) == 1
    # coverage: most mail of each real topic is clustered, not thrown into the review bucket
    for name, r in true.items():
        assert sum(cluster_of[f"m{i}"] is not None for i in r) >= 24, name
    odd = [db.get(MessageTopic, f"odd{j}") for j in range(5)]
    assert sum(o.is_noise for o in odd) >= 3                    # unrelated one-offs go to the review bucket
    t = db.get(Topic, topic_of(db, "m1") or topic_of(db, "m2"))
    assert "flight" in t.label or "booking" in t.label or "trip" in t.label
    assert t.size >= 5 and "mails" in t.summary
    assert all(m.x is not None and m.z is not None for m in db.scalars(select(MessageTopic)))   # 3D coordinates stored (FR-25)


def test_topic_ids_stay_stable_across_reclustering(db):
    build(db)
    recluster(db, FakeEmbedder())
    before = {m: topic_of(db, m) for m in ("m1", "m31", "m61")}
    recluster(db, FakeEmbedder())
    assert {m: topic_of(db, m) for m in ("m1", "m31", "m61")} == before


def test_new_mail_is_assigned_to_nearest_topic(db):
    g = build(db)
    recluster(db, FakeEmbedder())
    g.mails["new1"] = mail("new1", "Vistara <x@vistara.in>", "Flight booking confirmed 99", "x", day=60)
    g.mails["new2"] = mail("new2", "Lone <l@l.io>", "Giraffe hammock", "x", day=61)
    from app.sync import store_raw
    store_raw(db, g.mails["new1"]); store_raw(db, g.mails["new2"]); db.commit()
    assert assign_new(db, FakeEmbedder()) == 2
    travel = {topic_of(db, f"m{i}") for i in range(1, 31)} - {None}
    assert topic_of(db, "new1") in travel                       # joined a travel topic
    assert db.get(MessageTopic, "new2").topic_id is None        # nothing similar -> review bucket


def test_small_mailbox_is_not_clustered(db):
    g = FakeGmail([mail(str(i), "A <a@a.com>", f"hi {i}", "x") for i in range(5)])
    run_sync(db, g)
    run = recluster(db, FakeEmbedder())
    assert run.topics == 0 and db.scalar(select(func.count()).select_from(Topic)) == 0


def test_trashed_mail_is_excluded(db):
    g = build(db)
    act.perform(db, g, ["m1"], "trash")
    recluster(db, FakeEmbedder())
    assert db.get(MessageTopic, "m1") is None


def test_weekly_schedule(db):
    build(db)
    assert recluster_due(db) is True            # never run
    after_sync(db, FakeEmbedder())
    assert recluster_due(db) is False           # just ran -> only assign new mail until next week
    n = db.scalar(select(func.count()).select_from(Topic))
    after_sync(db, FakeEmbedder())              # second call is the cheap daily path
    assert db.scalar(select(func.count()).select_from(Topic)) == n


def test_deleting_mail_cleans_topic_rows(db):
    g = build(db)
    recluster(db, FakeEmbedder())
    from app.cleanup import delete_messages
    delete_messages(db, ["m1"])
    db.commit()
    assert db.get(MessageTopic, "m1") is None and db.get(Embedding, "m1") is None


def test_topic_api():
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app import auth
    from app.db import Base, get_db
    from app.main import app, get_gmail
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(e)
    S = sessionmaker(bind=e, expire_on_commit=False)
    with S() as s:
        g = build(s)
        recluster(s, FakeEmbedder())
    app.dependency_overrides[get_db] = lambda: S()
    app.dependency_overrides[get_gmail] = lambda: g
    c = TestClient(app)
    assert c.get("/topics").status_code == 401
    c.cookies.set("session", auth.make_session("me@example.com"))
    t = c.get("/topics").json()
    assert len(t["topics"]) >= 3 and sum(x["count"] for x in t["topics"]) + t["noise"] == 95
    pts = c.get("/map").json()["points"]
    assert len(pts) == 95 and {"i", "x", "y", "z", "t", "s", "f"} <= set(pts[0])
    tid = t["topics"][0]["topic_id"]
    assert all(r["message_id"] for r in c.get(f"/topics/{tid}/messages").json())
    noise_rows = c.get("/topics/0/messages").json()
    assert [r["score"] for r in noise_rows] == sorted((r["score"] for r in noise_rows), reverse=True)
    assert "preview" in c.get("/messages/m1").json()
    st = c.get("/topics/status").json()
    assert st["running"] is False and st["topics"] >= 3
    c.post("/actions", json={"message_ids": ["m1"], "action": "trash"})
    assert len(c.get("/map").json()["points"]) == 94           # trashed mail leaves the map


def test_message_endpoint_returns_the_full_cleaned_text_for_the_reader(db):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app import auth
    from app.db import Base, get_db
    from app.main import app
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(e)
    S = sessionmaker(bind=e, expire_on_commit=False)
    long_body = "Line one of the job alert.\n" + ("More text about architect jobs. " * 60)
    with S() as s:
        run_sync(s, FakeGmail([mail("r1", "Indeed <alert@jobalert.indeed.com>", "5 more architect jobs", long_body, size=4321)]))
    app.dependency_overrides[get_db] = lambda: S()
    c = TestClient(app)
    c.cookies.set("session", auth.make_session("me@example.com"))
    m = c.get("/messages/r1").json()
    assert m["body"].startswith("Line one of the job alert.") and len(m["body"]) > 600       # whole cleaned text, not just 600 chars
    assert len(m["preview"]) == 600 and m["size_bytes"] == 4321 and m["sender_email"] == "alert@jobalert.indeed.com"
    assert m["date"] and m["gmail_link"] and "labels" in m
    assert c.get("/messages/nope").status_code == 404


# ---------- hologram inside hologram ----------
from app import hier  # noqa: E402


def test_drill_into_topic_splits_again_and_lists_leaves(db):
    from app.embeddings import set_embedder
    set_embedder(FakeEmbedder())
    build(db)
    recluster(db, FakeEmbedder())
    hier._cache.clear()
    tid = max(db.scalars(select(Topic)).all(), key=lambda t: t.size).topic_id
    v = hier.view(db, f"t{tid}")
    assert v["path"] == f"t{tid}"
    assert v["leaf"] or (len(v["topics"]) >= 2 and v["points"])
    if not v["leaf"]:
        assert all(p["t"] is None or p["t"] in {t["topic_id"] for t in v["topics"]} for p in v["points"])
        first = v["topics"][0]["topic_id"]
        rows = list(db.execute(hier.messages_query(db, f"t{tid}", first)))
        assert len(rows) == v["topics"][0]["count"]
        child = hier.view(db, f"t{tid}.{first}")   # one level deeper never crashes, small groups become a list
        assert child["leaf"] or child["topics"]
    else:
        assert v["reason"]


def test_kinds_group_across_senders_and_trashed_mail_drops_out(db):
    from app.embeddings import set_embedder
    set_embedder(FakeEmbedder())
    build(db)
    hier._cache.clear(); hier._subject_vecs.clear()
    v = hier.view(db, "k")
    assert not v["leaf"] and len(v["topics"]) >= 2
    assert v["count"] == len(v["points"])
    victim = v["points"][0]["i"]
    m = db.get(Message, victim)
    m.in_trash = True
    db.commit()
    again = hier.view(db, "k")
    assert victim not in {p["i"] for p in again["points"]}


def test_tiny_or_unknown_nodes(db):
    import pytest
    build(db)
    with pytest.raises(KeyError):
        hier.view(db, "zzz")
    with pytest.raises(KeyError):
        hier.view(db, "k.999")
