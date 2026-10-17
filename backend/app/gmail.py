"""Gmail access behind a small interface so sync/actions can be tested with a fake."""
import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime
from typing import Protocol


@dataclass
class RawMail:
    message_id: str
    thread_id: str
    sender: str
    subject: str
    date: datetime
    labels: list[str]
    snippet: str
    body: str
    is_html: bool = False
    list_unsubscribe: str | None = None
    headers: dict = field(default_factory=dict)
    size_estimate: int = 0


class GmailClient(Protocol):
    def search(self, query: str, limit: int) -> tuple[list[str], int]:
        """Newest-first ids matching a Gmail query, plus Gmail's total estimate."""
    def get_message(self, message_id: str) -> RawMail: ...
    def profile_history_id(self) -> str: ...
    def history_changes(self, start_history_id: str) -> tuple[list[str], list[str]] | None:
        """(changed_ids, deleted_ids) or None if start_history_id has expired."""
    def modify_labels(self, message_id: str, add: list[str], remove: list[str]) -> None: ...
    def trash(self, message_id: str) -> None: ...
    def untrash(self, message_id: str) -> None: ...
    def ensure_label(self, name: str) -> str: ...
    def delete_permanently(self, message_ids: list[str]) -> None:
        """IRREVERSIBLE. Callers must only pass mail that is already in Trash."""


class GoogleGmailClient:
    def __init__(self, credentials):
        from googleapiclient.discovery import build
        self.svc = build("gmail", "v1", credentials=credentials, cache_discovery=False)

    def search(self, query, limit):
        ids, token, est = [], None, 0
        while len(ids) < limit:
            r = self.svc.users().messages().list(
                userId="me", q=query, pageToken=token, maxResults=min(500, limit - len(ids))).execute()
            est = r.get("resultSizeEstimate", est)
            ids += [m["id"] for m in r.get("messages", [])]
            token = r.get("nextPageToken")
            if not token:
                break
        return ids[:limit], est

    def get_message(self, message_id):
        m = self.svc.users().messages().get(userId="me", id=message_id, format="full").execute()
        h = {x["name"].lower(): x["value"] for x in m["payload"].get("headers", [])}
        body, is_html = _extract_body(m["payload"])
        try:
            date = parsedate_to_datetime(h["date"])
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
        except Exception:
            date = datetime.fromtimestamp(int(m["internalDate"]) / 1000, timezone.utc)
        return RawMail(m["id"], m["threadId"], h.get("from", ""), h.get("subject", ""), date,
                       m.get("labelIds", []), m.get("snippet", ""), body, is_html,
                       h.get("list-unsubscribe"), h, m.get("sizeEstimate", 0))

    def profile_history_id(self):
        return self.svc.users().getProfile(userId="me").execute()["historyId"]

    def history_changes(self, start_history_id):
        from googleapiclient.errors import HttpError
        changed, deleted, token = set(), set(), None
        try:
            while True:
                r = self.svc.users().history().list(
                    userId="me", startHistoryId=start_history_id, pageToken=token).execute()
                for h in r.get("history", []):
                    for key in ("messagesAdded", "labelsAdded", "labelsRemoved"):
                        changed |= {x["message"]["id"] for x in h.get(key, [])}
                    deleted |= {x["message"]["id"] for x in h.get("messagesDeleted", [])}
                token = r.get("nextPageToken")
                if not token:
                    return list(changed - deleted), list(deleted)
        except HttpError as e:
            if e.resp.status == 404:
                return None
            raise

    def modify_labels(self, message_id, add, remove):
        self.svc.users().messages().modify(
            userId="me", id=message_id, body={"addLabelIds": add, "removeLabelIds": remove}).execute()

    def trash(self, message_id):
        self.svc.users().messages().trash(userId="me", id=message_id).execute()

    def untrash(self, message_id):
        self.svc.users().messages().untrash(userId="me", id=message_id).execute()

    def delete_permanently(self, message_ids):
        for i in range(0, len(message_ids), 1000):  # batchDelete takes at most 1000 ids
            self.svc.users().messages().batchDelete(
                userId="me", body={"ids": message_ids[i:i + 1000]}).execute()

    def ensure_label(self, name):
        for lab in self.svc.users().labels().list(userId="me").execute().get("labels", []):
            if lab["name"] == name:
                return lab["id"]
        return self.svc.users().labels().create(
            userId="me", body={"name": name}).execute()["id"]


def _extract_body(payload) -> tuple[str, bool]:
    plain = html = ""
    stack = [payload]
    while stack:
        p = stack.pop()
        stack.extend(p.get("parts", []))
        data = p.get("body", {}).get("data")
        if not data:
            continue
        text = base64.urlsafe_b64decode(data + "==").decode("utf-8", "replace")
        if p.get("mimeType") == "text/plain":
            plain = plain or text
        elif p.get("mimeType") == "text/html":
            html = html or text
    return (plain, False) if plain else (html, True)


def parse_sender(raw: str) -> tuple[str, str, str]:
    """-> (display name, email address, domain)"""
    name, addr = parseaddr(raw)
    addr = addr.lower()
    domain = addr.split("@")[-1] if "@" in addr else ""
    return (name or addr or raw), addr, domain
