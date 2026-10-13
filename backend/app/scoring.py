"""Rule-based classifier + 0-100 importance score with a one-line reason.
An LLM pass can be layered on later; MODEL_VERSION lets old scores be kept."""
import re

MODEL_VERSION = "rules-v1"

_URGENT = re.compile(r"\b(urgent|asap|immediately|deadline|due (today|tomorrow)|action required|final notice|expires?)\b", re.I)
_QUESTION = re.compile(r"\?|\b(please (reply|confirm|let me know)|can you|could you|let me know|waiting for)\b", re.I)
_JUNK = re.compile(r"\b(unsubscribe|% off|sale|deal|newsletter|promotion|promo|limited time|webinar)\b", re.I)
_NOREPLY = re.compile(r"(no-?reply|donotreply|notifications?@|mailer)", re.I)


def score_message(sender: str, subject: str, body: str, is_bulk: bool, labels: list[str]) -> tuple[str, int, str]:
    text = f"{subject}\n{body[:1500]}"
    if is_bulk or _NOREPLY.search(sender):
        if _JUNK.search(text) or "CATEGORY_PROMOTIONS" in labels:
            return "junk", 5, "Bulk/promotional mail"
        return "fyi", 20, "Automated or bulk mail"
    if _URGENT.search(text):
        return "urgent", 90, "Mentions a deadline or urgent action from a person"
    if _QUESTION.search(text):
        score = 70 + (5 if "STARRED" in labels or "IMPORTANT" in labels else 0)
        return "needs_reply", score, "Looks like it is asking you something"
    if "CATEGORY_PROMOTIONS" in labels or _JUNK.search(text):
        return "junk", 10, "Promotional wording"
    return "fyi", 35, "Personal mail with no clear request"
