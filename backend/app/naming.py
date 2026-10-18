"""Topic headings, made by a small fixed pipeline (an orchestrator around ONE job: naming a cluster).

    facts -> merge near-copies -> router -> pick subjects -> namer (local model) -> checker -> retry once -> fallback

  * facts    count the subject types in the cluster (order numbers collapsed: 'Your order #123 has shipped' and '#456' are one type)
  * merge    with the local embedding model, subject types that mean the same ('Your order has shipped' / 'Your order has been
             shipped') are counted as ONE type; only close copies merge (TITLE_MERGE_SIMILARITY), never different themes
  * router   one type covers >= 70% of the cluster: its subject IS the heading, no model is used
  * pick     otherwise the model is shown up to 5 subjects chosen by MEANING (one per theme, bigger themes first), each with
             its mail count, so a small theme is not left out and the model knows which theme is big
  * namer    a local model (Qwen2.5-1.5B-Instruct) writes <= 6 words
  * checker  plain code, not a model: rejects empty / too long / numbers / just a sender name / alarm words with no urgent
             mail in the cluster / a heading that just copies one subject of a mixed cluster / a heading that neither shares a word
             with the cluster's subjects (the sender's name does not count) nor is close to them in meaning (TITLE_MIN_SIMILARITY)
  * retry    one more try with a different prompt, then the most typical real subject; the old keyword label is the last resort

Nothing here learns or trains. The model is optional: without TITLE_MODEL_PATH, mixed clusters get the most typical real subject.
Merging and picking by meaning use the embedding model the topics already use. Nothing leaves this machine."""
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import Message, Score

ROUTER_SHARE = 0.70      # one subject type covering this much of the cluster becomes the heading
MAX_WORDS = 6
MAX_CHARS = 48
TYPICAL = 5              # subjects shown to the model
MIN_SEAT_SHARE = 0.04    # a subject type under this share of the cluster does not take one of those seats
MERGE_CAP = 60           # at most this many subject types per cluster are compared by meaning (the biggest ones)

_PREFIX = re.compile(r"^((re|fwd?|fw)\s*:\s*)+", re.I)
_DIGITS = re.compile(r"#?\b[\w.-]*\d[\w.-]*\b")          # any word containing a digit: ids, dates, amounts
_ALARM = re.compile(r"\b(urgent|asap|immediately|deadline|overdue|final notice|action required|expires?)\b", re.I)
_WORD = re.compile(r"[a-z]{3,}")
_STOP = set("""the and for you your are with this that from have has was were will not but all can our out new get just now more
than then them they their about into over how what when who why com www http https mail email via inc ltd team hello dear
thanks thank please""".split())


@dataclass
class Group:
    """One subject type, or several near-identical ones counted together. `sample` is a real subject of the biggest one."""
    key: str
    count: int
    sample: str
    vec: object = None          # unit vector of the cleaned sample, when an embedder was available


@dataclass
class Facts:
    n: int
    templates: list[tuple[str, int, str]]      # exact subject types (subject_key, count, one real subject), biggest first
    groups: list[Group]                        # the same, with near-copies merged by meaning; biggest first
    senders: Counter
    has_urgent: bool

    @property
    def top_share(self) -> float:
        return self.groups[0].count / self.n if self.n and self.groups else 0.0


@dataclass
class NameResult:
    label: str | None
    source: str                                 # template | model | fallback | none


class Namer(Protocol):
    def name(self, subjects: list[tuple[str, int]], senders: list[str], attempt: int) -> str | None: ...


def clean_heading(subject: str) -> str:
    """'Re: Your order #33532 has shipped' -> 'Your order has shipped'."""
    s = _PREFIX.sub("", (subject or "").strip())
    s = re.sub(r"\s+", " ", _DIGITS.sub(" ", s)).strip(" -–—:;,.#")
    return s or (subject or "").strip()


def _tokens(text: str) -> set[str]:
    return {w.rstrip("s") for w in _WORD.findall(text.lower()) if w not in _STOP}


def merge_similar(templates: list[tuple[str, int, str]], embedder) -> list[Group]:
    """Count subject types that mean the same as one. Greedy: the biggest type starts a group, a smaller one joins the first
    group whose starter it is >= TITLE_MERGE_SIMILARITY close to. Without an embedder (or if it fails) nothing is merged."""
    plain = [Group(k, c, s) for k, c, s in templates]
    if embedder is None or len(plain) < 2:
        return plain
    head, tail = plain[:MERGE_CAP], plain[MERGE_CAP:]
    try:
        V = embedder.embed([clean_heading(g.sample) for g in head])
    except Exception:
        return plain
    out: list[Group] = []
    for g, v in zip(head, V):
        g.vec = v
        for o in out:
            if float(o.vec @ v) >= settings.title_merge_similarity:
                o.count += g.count
                break
        else:
            out.append(Group(g.key, g.count, g.sample, v))
    out.sort(key=lambda g: (-g.count, g.key))
    return out + tail


def facts_of(msgs: list[Message], urgent_ids: set[str], embedder=None) -> Facts:
    by_key: dict[str, list[Message]] = {}
    for m in msgs:
        by_key.setdefault(m.subject_key or (m.subject or "").lower(), []).append(m)
    templates = sorted(((k, len(v), v[0].subject or k) for k, v in by_key.items()), key=lambda t: (-t[1], t[0]))
    return Facts(len(msgs), templates, merge_similar(templates, embedder), Counter(m.sender.split(" <")[0] for m in msgs),
                 any(m.message_id in urgent_ids for m in msgs))


def pick_subjects(groups: list[Group], n: int, k: int = TYPICAL) -> list[tuple[str, int]]:
    """The subjects the model reads, with their mail counts. Up to k. If there are more types than that and we have vectors:
    take the biggest first, then each time the type that is farthest in meaning from those already taken (a tie goes to the
    bigger one), so every theme gets a seat. Types under 4% of the cluster's n mails are skipped while others remain, so a
    stray one-off does not take a seat. Without vectors: the k biggest."""
    if len(groups) <= k or any(g.vec is None for g in groups[:MERGE_CAP]):
        chosen = groups[:k]
    else:
        pool = groups[:MERGE_CAP]
        floor = max(2, math.ceil(MIN_SEAT_SHARE * n))
        chosen = [pool[0]]
        while len(chosen) < k:
            rest = [g for g in pool if g not in chosen]
            if not rest:
                break
            real = [g for g in rest if g.count >= floor] or rest
            chosen.append(max(real, key=lambda g: (round(1.0 - max(float(c.vec @ g.vec) for c in chosen), 2), g.count)))
        chosen.sort(key=lambda g: (-g.count, g.key))
    return [(clean_heading(g.sample), g.count) for g in chosen]


def check(heading: str | None, facts: Facts, closeness=None) -> str | None:
    """None = acceptable. Otherwise the reason it was rejected. closeness(heading) -> 0..1 meaning-similarity to the
    cluster's subjects, or None when no embedding model is available (then only shared words count)."""
    if not heading or not heading.strip():
        return "empty"
    h = heading.strip()
    words = h.split()
    if len(words) > MAX_WORDS or len(h) > MAX_CHARS:
        return "too long"
    if "#" in h or re.search(r"\d", h):
        return "has numbers"
    low = h.lower()
    senders = {s.lower() for s in facts.senders}
    if low in senders or low in {s.split("@")[0] for s in senders}:
        return "just a sender name"
    if _ALARM.search(h) and not facts.has_urgent:
        return "alarm words but no urgent mail here"
    vocab = set()
    for _, _, subject in facts.templates:
        vocab |= _tokens(subject)
    sender_words = set().union(*(_tokens(s) for s in facts.senders)) if facts.senders else set()
    if not ((_tokens(h) - sender_words) & vocab):     # a sender's name alone does not make a heading true
        if closeness is None or closeness(h) < settings.title_min_similarity:
            return "shares no word with the subjects and is not close to them in meaning"
    if len(facts.groups) > 1 and any(low == clean_heading(subj).lower() for _, _, subj in facts.templates):
        return "copies one subject of a mixed group"
    return None


def _closeness(embedder, subjects: list[str]):
    """heading -> cosine similarity to the average of the subjects' embeddings; None if no embedder or it fails."""
    if embedder is None or not subjects:
        return None
    try:
        import numpy as np
        c = embedder.embed(subjects).mean(axis=0)
        c = c / max(float(np.linalg.norm(c)), 1e-9)
        return lambda h: float(embedder.embed([h])[0] @ c)
    except Exception:
        return None


def name_cluster(msgs: list[Message], urgent_ids: set[str], namer: Namer | None, embedder=None) -> NameResult:
    facts = facts_of(msgs, urgent_ids, embedder)
    if not facts.groups:
        return NameResult(None, "none")
    typical = clean_heading(facts.groups[0].sample)
    if facts.top_share >= ROUTER_SHARE:
        return NameResult(typical or None, "template")
    if namer is not None:
        subjects = pick_subjects(facts.groups, facts.n)
        senders = [s for s, _ in facts.senders.most_common(3)]
        closeness = _closeness(embedder, [s for s, _ in subjects])
        for attempt in (0, 1):
            try:
                heading = tidy(namer.name(subjects, senders, attempt))
            except Exception:       # a broken model must never break topic building
                heading = None
            if check(heading, facts, closeness) is None:
                return NameResult(heading, "model")
    return NameResult(typical or None, "fallback")


def tidy(text: str | None) -> str | None:
    if not text:
        return None
    first = text.strip().splitlines()[0] if text.strip() else ""
    return first.strip(" \"'`*-–—:;,.") or None


# ---------- the local model (optional) ----------
_SYSTEM = ("You name groups of emails. Look at ALL the subjects and reply with ONE short heading of 2 to 5 words that "
           "covers what they have in common. The number after each subject is how many emails have it, so bigger numbers "
           "matter more. Never copy a single subject. No quotes, no numbers, no full stop, no explanation.")
_SYSTEM_STRICT = _SYSTEM + " Use general words, like a folder name."
# generic examples (not from your mail) so the model sees the expected shape
_SHOTS = [
    ([("Your order has shipped", 30), ("Delivered: order", 12), ("Out for delivery today", 8)], "Order and delivery updates"),
    ([("Statement is ready", 20), ("New sign-in to your account", 9), ("Payment is due", 6)], "Bank account alerts"),
    ([("Weekend deals just for you", 25), ("Free delivery this weekend", 10), ("Off your next order", 9)], "Food delivery offers"),
]


def _prompt(subjects: list[tuple[str, int]], senders: list[str]) -> str:
    return "Email subjects in this group:\n" + "\n".join(f"- {s} ({n} mails)" for s, n in subjects) + \
        (f"\nMostly from: {', '.join(senders)}" if senders else "") + "\nHeading:"


class LlamaCppNamer:
    """Any chat GGUF (Qwen2.5-1.5B-Instruct) through llama-cpp-python. Loaded on first use, never trains, runs on CPU.
    `close()` frees the memory; rename() calls it when a naming pass is done."""

    def __init__(self, path: str):
        self.path = path
        self._llm = None

    def close(self):
        self._llm = None

    def name(self, subjects: list[tuple[str, int]], senders: list[str], attempt: int) -> str | None:
        if self._llm is None:
            from llama_cpp import Llama
            self._llm = Llama(model_path=self.path, n_ctx=1024, n_threads=2, verbose=False)
        msgs = [{"role": "system", "content": _SYSTEM_STRICT if attempt else _SYSTEM}]
        for subs, head in _SHOTS:
            msgs += [{"role": "user", "content": _prompt(subs, [])}, {"role": "assistant", "content": head}]
        msgs.append({"role": "user", "content": _prompt(subjects, senders)})
        out = self._llm.create_chat_completion(messages=msgs, max_tokens=16, temperature=0.0)
        return out["choices"][0]["message"]["content"]


_namers: dict[str, LlamaCppNamer] = {}


def get_namer() -> Namer | None:
    path = settings.title_model_path
    if not path:
        return None
    if path not in _namers:
        _namers[path] = LlamaCppNamer(path)
    return _namers[path]


# ---------- plug-in point for topics.py and hier.py ----------
def rename(db: Session, members: dict, msgs: dict[str, Message], names: dict, namer: Namer | None = None,
           embedder=None) -> dict:
    """names: {cluster key: (keyword label, summary)} as built by topics._label_clusters. Replaces each label with a heading
    from the pipeline above; the old keyword label stays when the pipeline finds nothing. Summaries are not touched.
    embedder: the embedding model the topics were built with (None = no merging or picking by meaning)."""
    ids = [m for v in members.values() for m in v]
    urgent = set(db.scalars(select(Score.message_id).where(Score.message_id.in_(ids), Score.cls == "urgent"))) if ids else set()
    namer = namer if namer is not None else get_namer()
    out = dict(names)
    try:
        for key, mids in members.items():
            group = [msgs[m] for m in mids if m in msgs]
            if not group or key not in out:
                continue
            res = name_cluster(group, urgent, namer, embedder)
            if res.label:
                out[key] = (res.label, out[key][1])
    finally:
        if hasattr(namer, "close"):        # give the memory back as soon as the naming pass is done
            namer.close()
    return out
