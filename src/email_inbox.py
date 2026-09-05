"""Email inbox integration — platform verifications + payment alerts.

Deterministic parsing/matching logic (mirrors ``payoneer_webhook.py`` and
``scam_detection.py``); the IMAP transport is injected so none of this needs
a live mailbox to test. Uses Python's stdlib ``imaplib`` — no package to
install, only credentials to configure (``EMAIL_IMAP_HOST`` /
``EMAIL_IMAP_PORT`` / ``EMAIL_IMAP_USER`` / ``EMAIL_IMAP_PASSWORD``).
Degrades to returning no messages + a logged warning when unconfigured,
matching ``cold_archive.py``'s ``HF_TOKEN`` fallback pattern — a missing
mailbox should never crash task execution.
"""

from __future__ import annotations

import asyncio
import email as email_lib
import imaplib
import logging
import os
import re
import time
from dataclasses import dataclass
from email.message import Message
from typing import Any, Optional

logger = logging.getLogger(__name__)

_VERIFICATION_LINK_RE = re.compile(
    r"https?://[^\s\"'<>]+(?:verify|confirm|activate)[^\s\"'<>]*", re.IGNORECASE
)
_VERIFICATION_CODE_RE = re.compile(r"\b\d{4,8}\b")

_PAYMENT_SENDER_KEYWORDS = ("payoneer", "paypal", "stripe", "wise")
_PAYMENT_SUBJECT_KEYWORDS = (
    "payment received",
    "you've been paid",
    "you have been paid",
    "invoice paid",
    "payout",
    "funds received",
)
_VERIFICATION_KEYWORDS = ("verify", "confirm", "activate")


class EmailInboxError(Exception):
    """Raised when a configured mailbox fails at the IMAP transport level."""


@dataclass
class EmailMessage:
    uid: str
    sender: str
    subject: str
    body: str


class EmailInboxClient:
    """Thin IMAP wrapper.

    Soft dependency on *configuration*, not a package — ``imaplib`` is
    stdlib, so this class always exists; it just refuses to connect (and
    returns empty results rather than raising) until the env vars are set.
    """

    def __init__(self, imap_client: Any = None) -> None:
        self._host = os.environ.get("EMAIL_IMAP_HOST")
        self._port = int(os.environ.get("EMAIL_IMAP_PORT", "993"))
        self._user = os.environ.get("EMAIL_IMAP_USER")
        self._password = os.environ.get("EMAIL_IMAP_PASSWORD")
        self._imap_client = imap_client

    @property
    def is_configured(self) -> bool:
        return bool(self._host and self._user and self._password)

    def _connect(self) -> Any:
        if self._imap_client is not None:
            return self._imap_client
        if not self._host or not self._user or not self._password:
            raise EmailInboxError("Email inbox not configured (EMAIL_IMAP_HOST/USER/PASSWORD unset)")
        conn = imaplib.IMAP4_SSL(self._host, self._port)
        conn.login(self._user, self._password)
        return conn

    def _disconnect(self, conn: Any) -> None:
        # Only close connections we opened ourselves — an injected test
        # double is the caller's to manage.
        if self._imap_client is None:
            try:
                conn.logout()
            except Exception:
                pass

    def fetch_unread(self, folder: str = "INBOX", limit: int = 20) -> list[EmailMessage]:
        if not self.is_configured and self._imap_client is None:
            logger.warning(
                "Email inbox not configured (EMAIL_IMAP_HOST/USER/PASSWORD unset) — no messages fetched"
            )
            return []

        conn = self._connect()
        messages: list[EmailMessage] = []
        try:
            conn.select(folder)
            status, data = conn.search(None, "UNSEEN")
            if status != "OK" or not data or not data[0]:
                return []
            uids = data[0].split()[-limit:]
            for uid in uids:
                status, msg_data = conn.fetch(uid, "(RFC822)")
                if status != "OK" or not msg_data or msg_data[0] is None:
                    continue
                raw = msg_data[0][1]
                parsed = email_lib.message_from_bytes(raw)
                messages.append(_to_email_message(uid.decode() if isinstance(uid, bytes) else str(uid), parsed))
        except EmailInboxError:
            raise
        except Exception as exc:
            raise EmailInboxError(f"IMAP fetch failed: {exc}") from exc
        finally:
            self._disconnect(conn)
        return messages

    def mark_as_read(self, uid: str, folder: str = "INBOX") -> None:
        conn = self._connect()
        try:
            conn.select(folder)
            conn.store(uid, "+FLAGS", "\\Seen")
        except Exception as exc:
            raise EmailInboxError(f"IMAP mark-as-read failed: {exc}") from exc
        finally:
            self._disconnect(conn)


def _to_email_message(uid: str, parsed: Message) -> EmailMessage:
    subject = str(parsed.get("subject", "") or "")
    sender = str(parsed.get("from", "") or "")
    return EmailMessage(uid=uid, sender=sender, subject=subject, body=_extract_body(parsed))


def _extract_body(parsed: Message) -> str:
    if parsed.is_multipart():
        for part in parsed.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if not isinstance(payload, bytes):
                    continue
                return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        return ""
    payload = parsed.get_payload(decode=True)
    if not isinstance(payload, bytes):
        return ""
    return payload.decode(parsed.get_content_charset() or "utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Deterministic parsing / matching
# ---------------------------------------------------------------------------

def extract_verification_link(body: str) -> Optional[str]:
    match = _VERIFICATION_LINK_RE.search(body)
    return match.group(0) if match else None


def extract_verification_code(body: str) -> Optional[str]:
    match = _VERIFICATION_CODE_RE.search(body)
    return match.group(0) if match else None


def is_platform_verification_email(msg: EmailMessage, platform_hint: str = "") -> bool:
    """True if ``msg`` looks like an account-verification email.

    With a ``platform_hint`` (e.g. "clickworker"), also requires the hint
    to appear in the sender or subject — so a verification email from an
    unrelated service isn't mistaken for the one we're waiting on.
    """
    text = f"{msg.sender} {msg.subject}".lower()
    if not any(kw in text for kw in _VERIFICATION_KEYWORDS):
        return False
    if platform_hint and platform_hint.lower() not in text:
        return False
    return True


def is_payment_alert(msg: EmailMessage) -> bool:
    sender = msg.sender.lower()
    subject = msg.subject.lower()
    if any(kw in sender for kw in _PAYMENT_SENDER_KEYWORDS):
        return True
    return any(kw in subject for kw in _PAYMENT_SUBJECT_KEYWORDS)


async def wait_for_verification_email(
    client: EmailInboxClient,
    platform_hint: str = "",
    timeout_seconds: float = 120,
    poll_interval_seconds: float = 5,
) -> Optional[EmailMessage]:
    """Poll the inbox until a matching verification email arrives or timeout."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        for msg in client.fetch_unread():
            if is_platform_verification_email(msg, platform_hint):
                return msg
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(poll_interval_seconds)
