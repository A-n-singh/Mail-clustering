"""Topic headings, made by a small fixed pipeline (an orchestrator around ONE job: naming a cluster).

    facts -> router -> namer (tiny local model) -> checker -> retry once -> fallback

  * facts    count the subject types in the cluster (order numbers collapsed: 'Your order #123 has shipped' and '#456' are one type)
  * router   one subject type covers >= 70% of the cluster: that subject IS the heading, no model is used
  * namer    otherwise a tiny local model (Qwen2.5-0.5B-Instruct) reads the 5 most typical subjects and writes <= 6 words
  * checker  plain code, not a model: rejects empty / too long / numbers / just a sender name / alarm words with no urgent
             mail in the cluster / a heading that shares no word with the cluster's subjects (the sender's name does not count)
  * retry    one more try with a different prompt; then the most typical real subject; the old keyword label is the last resort

Nothing here learns or trains. The model is optional: without TITLE_MODEL_PATH, mixed clusters get the most typical real subject.
Nothing leaves this machine."""
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

_PREFIX = re.compile(r"^((re|fwd?|fw)\s*:\s*)+", re.I)
_DIGITS = re.compile(r"#?\b[\w.-]*\d[\w.-]*\b")          # any word containing a digit: ids, dates, amounts
_ALARM = re.compile(r"\b(urgent|asap|immediately|deadline|overdue|final notice|action required|expires?)\b", re.I)
_WORD = re.compile(r"[a-z]{3,}")
_STOP = set("""the and for you your are with this that from have has was were will not but all can our out new get just now more
than then them they their about into over how what when who why com www http https mail email via inc ltd team hello dear
thanks thank please""".split())


@dataclass
class Facts:
    n: int
    templates: list[tuple[str, int, str]]      # (subject_key, count, one real subject), biggest first
    senders: Counter
    has_urgent: bool

    @property
    def top_share(self) -> float:
        return self.templates[0][1] / self.n if self.n and self.templates else 0.0


@dataclass
class NameResult:
    label: str | None
    source: str                                 # template | model | fallback | none


class Namer(Protocol):
    def name(self, subjects: list[str], senders: list[str], attempt: int) -> str | None: ...


def clean_heading(subject: str) -> str:
    """'Re: Your order #33532 has shipped' -> 'Your order has shipped'."""
    s = _PREFIX.sub("", (subject or "").strip())
    s = re.sub(r"\s+", " ", _DIGITS.sub(" ", s)).strip(" -–—:;,.#")
    return s or (subject or "").strip()


def _tokens(text: str) -> set[str]:
    return {w.rstrip("s") for w in _WORD.findall(text.lower()) if w not in _STOP}


def facts_of(msgs: list[Message], urgent_ids: set[str]) -> Facts:
    by_key: dict[str, list[Message]] = {}
    for m in msgs:
        by_key.setdefault(m.subject_key or (m.subject or "").lower(), []).append(m)
    templates = sorted(((k, len(v), v[0].subject or k) for k, v in by_key.items()), key=lambda t: (-t[1], t[0]))
    return Facts(len(msgs), templates, Counter(m.sender.split(" <")[0] for m in msgs),
                 any(m.message_id in urgent_ids for m in msgs))


def check(heading: str | None, facts: Facts) -> str | None:
    """None = acceptable. Otherwise the reason it was rejected."""
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
        return "shares no word with the subjects"
    if len(facts.templates) > 1 and any(low == clean_heading(subj).lower() for _, _, subj in facts.templates):
        return "copies one subject of a mixed group"
    return None


def name_cluster(msgs: list[Message], urgent_ids: set[str], namer: Namer | None) -> NameResult:
    facts = facts_of(msgs, urgent_ids)
    if not facts.templates:
        return NameResult(None, "none")
    typical = clean_heading(facts.templates[0][2])
    if facts.top_share >= ROUTER_SHARE:
        return NameResult(typical or None, "template")
    if namer is not None:
        subjects = [clean_heading(s) for _, _, s in facts.templates[:TYPICAL]]
        senders = [s for s, _ in facts.senders.most_common(3)]
        for attempt in (0, 1):
            try:
                heading = namer.name(subjects, senders, attempt)
            except Exception:       # a broken model must never break topic building
                heading = None
            heading = tidy(heading)
            if check(heading, facts) is None:
                return NameResult(heading, "model")
    return NameResult(typical or None, "fallback")


def tidy(text: str | None) -> str | None:
    if not text:
        return None
    first = text.strip().splitlines()[0] if text.strip() else ""
    return first.strip(" \"'`*-–—:;,.") or None


# ---------- the tiny local model (optional) ----------
_SYSTEM = ("You name groups of emails. Look at ALL the subjects and reply with ONE short heading of 2 to 5 words that "
           "covers what they have in common. Never copy a single subject. No quotes, no numbers, no full stop, no explanation.")
_SYSTEM_STRICT = _SYSTEM + " Use general words, like a folder name."
# generic examples (not from your mail) so the tiny model sees the expected shape
_SHOTS = [
    (["Your order has shipped", "Delivered: order", "Out for delivery today"], "Order and delivery updates"),
    (["Statement is ready", "New sign-in to your account", "Payment is due"], "Bank account alerts"),
    (["Weekend deals just for you", "Free delivery this weekend", "Off your next order"], "Food delivery offers"),
]


def _prompt(subjects: list[str], senders: list[str]) -> str:
    return "Email subjects in this group:\n" + "\n".join(f"- {x}" for x in subjects) + \
        (f"\nMostly from: {', '.join(senders)}" if senders else "") + "\nHeading:"


class LlamaCppNamer:
    """Qwen2.5-0.5B-Instruct (or any chat GGUF) through llama-cpp-python. Loaded on first use, never trains, runs on CPU."""

    def __init__(self, path: str):
        self.path = path
        self._llm = None

    def name(self, subjects: list[str], senders: list[str], attempt: int) -> str | None:
        if self._llm is None:
            from llama_cpp import Llama
            self._llm = Llama(model_path=self.path, n_ctx=512, n_threads=2, verbose=False)
        msgs = [{"role": "system", "content": _SYSTEM_STRICT if attempt else _SYSTEM}]
        for subs, head in _SHOTS:
            msgs += [{"role": "user", "content": _prompt(subs, [])}, {"role": "assistant", "content": head}]
        msgs.append({"role": "user", "content": _prompt(subjects, senders)})
        out = self._llm.create_chat_completion(messages=msgs, max_tokens=16, temperature=0.0)
        return out["choices"][0]["message"]["content"]


_namer: Namer | None = None
_namer_path: str | None = None


def get_namer() -> Namer | None:
    global _namer, _namer_path
    path = settings.title_model_path
    if not path:
        return None
    if _namer is None or _namer_path != path:
        _namer, _namer_path = LlamaCppNamer(path), path
    return _namer


def set_namer(n: Namer | None):          # used by tests
    global _namer, _namer_path
    _namer, _namer_path = n, settings.title_model_path


# ---------- plug-in point for topics.py and hier.py ----------
def rename(db: Session, members: dict, msgs: dict[str, Message], names: dict, namer: Namer | None = None) -> dict:
    """names: {cluster key: (keyword label, summary)} as built by topics._label_clusters. Replaces each label with a heading
    from the pipeline above; the old keyword label stays when the pipeline finds nothing. Summaries are not touched."""
    ids = [m for v in members.values() for m in v]
    urgent = set(db.scalars(select(Score.message_id).where(Score.message_id.in_(ids), Score.cls == "urgent"))) if ids else set()
    namer = namer if namer is not None else get_namer()
    out = dict(names)
    for key, mids in members.items():
        group = [msgs[m] for m in mids if m in msgs]
        if not group or key not in out:
            continue
        res = name_cluster(group, urgent, namer)
        if res.label:
            out[key] = (res.label, out[key][1])
    return out
