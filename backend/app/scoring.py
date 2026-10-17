"""Rule-based classifier + 0-100 importance score with a one-line reason.
Looks at the sender name and subject FIRST; the body is only a fallback when those say nothing.
MODEL_VERSION lets old scores be kept if the rules change."""
import re

MODEL_VERSION = "rules-v2"

_URGENT = re.compile(r"\b(urgent|asap|immediately|deadline|due (today|tomorrow)|action required|final notice|expires?|overdue)\b", re.I)
_QUESTION = re.compile(r"\?|\b(please (reply|confirm|let me know)|can you|could you|let me know|waiting for)\b", re.I)
_JUNK = re.compile(r"\b(unsubscribe|\d+% off|sale|deals?|newsletter|promotion|promo|limited time|webinar|digest|offer)\b", re.I)
_NOREPLY = re.compile(r"(no-?reply|donotreply|notifications?|mailer|alerts?|updates?|news)", re.I)


def score_message(sender: str, subject: str, body: str, is_bulk: bool, labels: list[str]) -> tuple[str, int, str]:
    head = f"{sender}\n{subject}"
    automated = is_bulk or bool(_NOREPLY.search(sender))
    if automated:
        if _JUNK.search(head) or "CATEGORY_PROMOTIONS" in labels:
            return "junk", 5, "Bulk/promotional (from sender and subject)"
        if _URGENT.search(subject):
            return "urgent", 80, "Automated mail, but the subject says action is needed"
        if _JUNK.search(body[:500]):
            return "junk", 8, "Bulk mail with promotional wording"
        return "fyi", 20, "Automated or bulk mail"
    if _URGENT.search(subject):
        return "urgent", 90, "Subject mentions a deadline or urgent action"
    if _QUESTION.search(subject):
        return "needs_reply", 75 + (5 if {"STARRED", "IMPORTANT"} & set(labels) else 0), "Subject asks you something"
    if "CATEGORY_PROMOTIONS" in labels or _JUNK.search(head):
        return "junk", 10, "Promotional subject"
    # nothing decisive in the sender/subject -> look at the body
    text = body[:1500]
    if _URGENT.search(text):
        return "urgent", 85, "Body mentions a deadline or urgent action"
    if _QUESTION.search(text):
        return "needs_reply", 65, "Body looks like it asks you something"
    return "fyi", 35, "Personal mail with no clear request"
