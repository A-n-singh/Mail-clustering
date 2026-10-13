"""Optional LLM refinement for mail the rules could not decide on (FR-07).
Privacy: only sender, subject and the first 1,500 characters of the cleaned body are sent; never attachments."""
import json
from datetime import date

from sqlalchemy.orm import Session

from .config import settings
from .models import LlmUsage

CLASSES = {"urgent", "needs_reply", "fyi", "junk"}
SCORE = {"urgent": 90, "needs_reply": 70, "fyi": 35, "junk": 8}
PROMPT = (
    "Classify this email for its owner. Reply with JSON only: "
    '{{"class": "urgent|needs_reply|fyi|junk", "reason": "<one short sentence>"}}.\n'
    "urgent = time-critical action; needs_reply = a person expects an answer; fyi = informational; junk = promo/noise.\n\n"
    "From: {sender}\nSubject: {subject}\n\n{body}"
)


def llm_enabled() -> bool:
    return bool(settings.anthropic_api_key)


def _call_model(prompt: str) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    r = client.messages.create(model=settings.llm_model, max_tokens=120,
                               messages=[{"role": "user", "content": prompt}])
    return r.content[0].text


def budget_left(db: Session) -> bool:
    row = db.get(LlmUsage, date.today().isoformat())
    return (row.calls if row else 0) < settings.llm_daily_cap


def _spend(db: Session):
    day = date.today().isoformat()
    row = db.get(LlmUsage, day) or LlmUsage(day=day, calls=0)
    row.calls += 1
    db.merge(row)


def classify(db: Session, sender: str, subject: str, body: str, call=None):
    """-> (class, score, reason) or None if disabled, over budget, or the reply was unusable."""
    if not llm_enabled() or not budget_left(db):
        return None
    _spend(db)
    try:
        text = (call or _call_model)(PROMPT.format(sender=sender, subject=subject, body=body[:1500]))
        data = json.loads(text[text.index("{"): text.rindex("}") + 1])
        cls = data["class"]
        if cls not in CLASSES:
            return None
        return cls, SCORE[cls], str(data.get("reason", "Classified by LLM"))[:200]
    except Exception:
        return None  # keep the rule-based result
