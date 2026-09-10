"""Production notification transports.

Reuses what JAY-OS already runs on: the Gmail SMTP account configured in
the Hermes environment (`EMAIL_*`), and Notion @mentions through the same
Notion connection the pipeline writes records with.

`CompositeNotifier` treats delivery as succeeded only if at least one
channel actually landed. If every channel fails it raises, so the caller
fails closed and raises an exception instead of recording a success that
never reached a human.
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from . import config
from .model import Notification, now
from .ports import ConnectorError

#: Canary/test records must never page a real employee. Anything addressed
#: to a person while `redirect_to` is set is rerouted, with the original
#: recipient preserved in the body so the test is still meaningful.
class SmtpNotifier:
    """Sends real mail through the configured JAY-OS Gmail account."""

    def __init__(self, redirect_to: str | None = None, timeout: int = 30) -> None:
        self.host = config.require("EMAIL_SMTP_HOST", "owner email notification")
        self.port = int(config.get("EMAIL_SMTP_PORT") or 587)
        self.address = config.require("EMAIL_ADDRESS", "owner email notification")
        self._password = config.require("EMAIL_PASSWORD", "owner email notification")
        self.redirect_to = redirect_to
        self.timeout = timeout

    def check(self) -> str:
        """Prove the transport works without sending anything."""
        try:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(self.address, self._password)
        except Exception as error:
            raise ConnectorError(
                f"SMTP check failed for {self.host}:{self.port}: "
                f"{type(error).__name__}: {error}") from error
        return f"authenticated to {self.host}:{self.port}"

    def send(self, notification: Notification) -> Notification:
        target = self.redirect_to or notification.recipient
        body = notification.body
        if self.redirect_to and self.redirect_to != notification.recipient:
            body = (
                f"[TEST ROUTING] This notification was addressed to "
                f"{notification.recipient} and redirected to {target} so that "
                f"no live employee is paged by a test record.\n\n"
                f"{'-' * 60}\n\n{body}"
            )
        message = EmailMessage()
        message["From"] = self.address
        message["To"] = target
        message["Subject"] = notification.subject
        message.set_content(body)
        try:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(self.address, self._password)
                server.send_message(message)
        except Exception as error:
            raise ConnectorError(
                f"SMTP delivery to {target} failed: "
                f"{type(error).__name__}: {error}"
            ) from error
        return Notification(
            channel="email", recipient=target, subject=notification.subject,
            body=body, sent_at=now(), ok=True,
            detail=f"smtp {self.host}:{self.port}",
        )


class NotionMentionNotifier:
    """@mentions the owner on the project record.

    This is the channel that puts the work where the work lives, and it is
    the one that survives an employee not reading email.
    """

    def __init__(self, notion, page_resolver, mention_resolver) -> None:
        self._notion = notion
        self._page_of = page_resolver
        self._mention_of = mention_resolver

    def send(self, notification: Notification) -> Notification:
        page_id = self._page_of(notification)
        if not page_id:
            raise ConnectorError("no Notion page to notify on")
        self._notion.comment(
            page_id, notification.subject + "\n\n" + notification.body,
            mention_user_id=self._mention_of(notification),
        )
        return Notification(
            channel="notion", recipient=notification.recipient,
            subject=notification.subject, body=notification.body,
            sent_at=now(), ok=True, detail=f"notion comment on {page_id}",
        )


class CompositeNotifier:
    """Delivers over several channels; succeeds only if one truly lands."""

    def __init__(self, *channels) -> None:
        if not channels:
            raise ValueError("a notifier with no channels cannot notify")
        self.channels = channels
        self.sent: list[Notification] = []
        self.failures: list[str] = []

    def check(self) -> str:
        """Check every channel that supports it; one healthy channel is enough."""
        results, errors = [], []
        for channel in self.channels:
            checker = getattr(channel, "check", None)
            if checker is None:
                continue
            try:
                results.append(f"{type(channel).__name__}: {checker()}")
            except ConnectorError as error:
                errors.append(f"{type(channel).__name__}: {error}")
        if not results:
            raise ConnectorError("; ".join(errors) or "no checkable channels")
        return "; ".join(results + errors)

    def send(self, notification: Notification) -> Notification:
        errors: list[str] = []
        delivered: Notification | None = None
        for channel in self.channels:
            try:
                result = channel.send(notification)
            except ConnectorError as error:
                errors.append(f"{type(channel).__name__}: {error}")
                continue
            self.sent.append(result)
            if delivered is None:
                delivered = result
        self.failures.extend(errors)
        if delivered is None:
            raise ConnectorError(
                "every notification channel failed: " + "; ".join(errors)
            )
        if errors:
            # Landed, but degraded. Say so in the audit record.
            delivered = Notification(
                channel=delivered.channel, recipient=delivered.recipient,
                subject=delivered.subject, body=delivered.body,
                sent_at=delivered.sent_at, ok=True,
                detail=delivered.detail + " | degraded: " + "; ".join(errors),
            )
        return delivered
