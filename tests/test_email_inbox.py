"""Tests for src/email_inbox.py — email inbox integration (critical gap)."""

from unittest.mock import MagicMock

import pytest

from src.email_inbox import (
    EmailInboxClient,
    EmailInboxError,
    EmailMessage,
    extract_verification_code,
    extract_verification_link,
    is_payment_alert,
    is_platform_verification_email,
    wait_for_verification_email,
)


def _raw_email(subject: str, sender: str, body: str) -> bytes:
    return (
        f"From: {sender}\r\nSubject: {subject}\r\nContent-Type: text/plain\r\n\r\n{body}"
    ).encode()


class TestEmailInboxClientUnconfigured:
    def test_not_configured_without_env(self, monkeypatch):
        for var in ("EMAIL_IMAP_HOST", "EMAIL_IMAP_USER", "EMAIL_IMAP_PASSWORD"):
            monkeypatch.delenv(var, raising=False)
        client = EmailInboxClient()
        assert client.is_configured is False

    def test_unconfigured_fetch_returns_empty(self, monkeypatch):
        for var in ("EMAIL_IMAP_HOST", "EMAIL_IMAP_USER", "EMAIL_IMAP_PASSWORD"):
            monkeypatch.delenv(var, raising=False)
        client = EmailInboxClient()
        assert client.fetch_unread() == []


class TestEmailInboxClientFetch:
    def _mock_imap(self, uids: list[bytes], raw_by_uid: dict[bytes, bytes]):
        mock = MagicMock()
        mock.select.return_value = ("OK", [b""])
        mock.search.return_value = ("OK", [b" ".join(uids)])

        def fetch(uid, _spec):
            return ("OK", [(uid, raw_by_uid[uid])])

        mock.fetch.side_effect = fetch
        return mock

    def test_fetch_unread_parses_messages(self):
        raw = _raw_email("Verify your account", "no-reply@clickworker.com", "Click here to verify.")
        mock_imap = self._mock_imap([b"1"], {b"1": raw})
        client = EmailInboxClient(imap_client=mock_imap)

        messages = client.fetch_unread()

        assert len(messages) == 1
        assert messages[0].subject == "Verify your account"
        assert messages[0].sender == "no-reply@clickworker.com"
        assert "verify" in messages[0].body.lower()

    def test_fetch_unread_empty_search_result(self):
        mock_imap = MagicMock()
        mock_imap.select.return_value = ("OK", [b""])
        mock_imap.search.return_value = ("OK", [b""])
        client = EmailInboxClient(imap_client=mock_imap)

        assert client.fetch_unread() == []

    def test_fetch_unread_search_failure_returns_empty(self):
        mock_imap = MagicMock()
        mock_imap.select.return_value = ("OK", [b""])
        mock_imap.search.return_value = ("NO", [None])
        client = EmailInboxClient(imap_client=mock_imap)

        assert client.fetch_unread() == []

    def test_fetch_unread_raises_on_transport_error(self):
        mock_imap = MagicMock()
        mock_imap.select.side_effect = RuntimeError("connection reset")
        client = EmailInboxClient(imap_client=mock_imap)

        with pytest.raises(EmailInboxError):
            client.fetch_unread()

    def test_mark_as_read_calls_store(self):
        mock_imap = MagicMock()
        client = EmailInboxClient(imap_client=mock_imap)

        client.mark_as_read("1")

        mock_imap.store.assert_called_once_with("1", "+FLAGS", "\\Seen")

    def test_mark_as_read_raises_on_transport_error(self):
        mock_imap = MagicMock()
        mock_imap.store.side_effect = RuntimeError("boom")
        client = EmailInboxClient(imap_client=mock_imap)

        with pytest.raises(EmailInboxError):
            client.mark_as_read("1")


class TestExtractVerificationLink:
    def test_extracts_verify_link(self):
        body = "Please confirm: https://example.com/verify?token=abc123 thanks"
        assert extract_verification_link(body) == "https://example.com/verify?token=abc123"

    def test_no_link_returns_none(self):
        assert extract_verification_link("Hello, your order shipped.") is None

    def test_ignores_unrelated_links(self):
        body = "Visit https://example.com/dashboard for details."
        assert extract_verification_link(body) is None


class TestExtractVerificationCode:
    def test_extracts_numeric_code(self):
        assert extract_verification_code("Your code is 482910. It expires soon.") == "482910"

    def test_no_code_returns_none(self):
        assert extract_verification_code("No numbers here at all.") is None

    def test_ignores_short_numbers(self):
        assert extract_verification_code("Item #12 is out of stock.") is None


class TestIsPlatformVerificationEmail:
    def test_matches_verification_keyword(self):
        msg = EmailMessage(uid="1", sender="no-reply@toloka.com", subject="Confirm your email", body="")
        assert is_platform_verification_email(msg) is True

    def test_no_keyword_no_match(self):
        msg = EmailMessage(uid="1", sender="news@example.com", subject="Weekly newsletter", body="")
        assert is_platform_verification_email(msg) is False

    def test_platform_hint_filters_unrelated_sender(self):
        msg = EmailMessage(uid="1", sender="no-reply@unrelated.com", subject="Verify your account", body="")
        assert is_platform_verification_email(msg, platform_hint="clickworker") is False

    def test_platform_hint_matches_sender(self):
        msg = EmailMessage(uid="1", sender="no-reply@clickworker.com", subject="Verify your account", body="")
        assert is_platform_verification_email(msg, platform_hint="clickworker") is True


class TestIsPaymentAlert:
    def test_matches_known_sender(self):
        msg = EmailMessage(uid="1", sender="alerts@payoneer.com", subject="Update", body="")
        assert is_payment_alert(msg) is True

    def test_matches_subject_keyword(self):
        msg = EmailMessage(uid="1", sender="billing@platform.io", subject="You've been paid $12.00", body="")
        assert is_payment_alert(msg) is True

    def test_unrelated_email_not_a_payment_alert(self):
        msg = EmailMessage(uid="1", sender="news@example.com", subject="Weekly digest", body="")
        assert is_payment_alert(msg) is False


class TestWaitForVerificationEmail:
    @pytest.mark.asyncio
    async def test_returns_matching_message_immediately(self):
        msg = EmailMessage(uid="1", sender="no-reply@clickworker.com", subject="Verify your account", body="")
        client = MagicMock()
        client.fetch_unread.return_value = [msg]

        result = await wait_for_verification_email(client, platform_hint="clickworker", timeout_seconds=5)

        assert result is msg

    @pytest.mark.asyncio
    async def test_times_out_when_no_match(self):
        client = MagicMock()
        client.fetch_unread.return_value = []

        result = await wait_for_verification_email(
            client, platform_hint="clickworker", timeout_seconds=0.05, poll_interval_seconds=0.02
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_ignores_non_matching_messages(self):
        unrelated = EmailMessage(uid="1", sender="news@example.com", subject="Digest", body="")
        client = MagicMock()
        client.fetch_unread.return_value = [unrelated]

        result = await wait_for_verification_email(
            client, platform_hint="clickworker", timeout_seconds=0.05, poll_interval_seconds=0.02
        )

        assert result is None
