"""Decide what is an operational event, and turn it into a RawEvent.

This is the gate that stops the pipeline drowning Jay in exceptions about
newsletters. Only mail that is plausibly business-operational enters the
chain; everything else is recorded as skipped and never becomes an event.

Relevance is deliberately generous -- a false exception costs a glance, a
missed correction cost 72 days on 637 N Riata.
"""
from __future__ import annotations

import datetime as dt
import email.utils
import re
from typing import Any, Iterable

from .matching import extract_permit_numbers
from .model import Channel, RawEvent, as_utc

#: Jurisdiction senders PCD actually receives review traffic from.
JURISDICTION_DOMAINS = (
    "phoenix.gov", "scottsdaleaz.gov", "gilbertaz.gov", "maricopa.gov",
    "mail.maricopa.gov", "mesaaz.gov", "tempe.gov", "chandleraz.gov",
    "glendaleaz.com", "peoriaaz.gov", "surpriseaz.gov", "avondaleaz.gov",
    "goodyearaz.gov", "buckeyeaz.gov", "queencreekaz.gov", "maranaaz.gov",
    "pima.gov", "azhousing.gov",
)

#: Vendor/automation noise that is never an operational project event.
#:
#: DO NOT ADD A LEAD SOURCE HERE. On 2026-09-05 this list was widened to
#: include angi.com and email.homeadvisor.com because ten of their messages
#: reached Jay as `project_not_matched` exceptions and looked like noise from
#: inside the permit pipeline. Jay corrected it the same evening: those are
#: LEADS -- new business, not junk. This firm has already lost a lead worth
#: $1.1-1.5M to a feed that went quiet on 2026-08-11 and was not noticed for
#: 24 days. A missed lead costs incomparably more than a noisy alert, so the
#: reversal is deliberate and must not be undone for tidiness.
#:
#: The real fix is not silence, it is a destination: a lead is not a permit
#: event and has no place in a permit register. It belongs in the Notion
#: Leads database, which the "PCD Lead Intake Form" workflow already writes
#: to. Until something routes it there, a lead surfacing as an exception in
#: front of a human is the correct failure -- loud and wrong beats quiet and
#: wrong.
NEVER_RELEVANT = (
    "accounts.google.com", "bluehost.com", "paypal.com", "intuit.com",
    "bitwarden.com", "kikoff.com", "openai.com", "anthropic.com",
    "slack.com", "atlassian.com", "expansive.com", "e.bluehost.com",
    # An unambiguous retail advert ("Get a school-ready Surface with a
    # flexible payment plan"). Nothing to do with PCD's work.
    "microsoftstore.microsoft.com",
)

_PERMIT_HINT = re.compile(
    r"\b(?:permit|plan review|plan check|submittal|resubmit|correction|"
    r"redline|inspection|jurisdiction|parcel|apn)\b",
    re.IGNORECASE,
)


def _domain(address: str) -> str:
    return address.split("@")[-1].strip().lower().rstrip(">")


def is_jurisdiction(sender: str) -> bool:
    domain = _domain(sender)
    return any(domain == d or domain.endswith("." + d)
               for d in JURISDICTION_DOMAINS)


#: PCD's own domain. Mail we sent is not evidence about the world -- it is a
#: record of what we said about it. Jay's rule, 2026-09-05: "the emails are
#: source of truth over what I or the team emails." Because jay@ forwards into
#: info@, our own outbound mail lands back in the ingest window and would
#: otherwise be read as an inbound event; the shadow run of 2026-09-05 picked
#: up two that way, including an n8n failure alert.
OWN_DOMAIN = "professionalcadesign.com"


def is_own_mail(sender: str) -> bool:
    domain = _domain(sender)
    return domain == OWN_DOMAIN or domain.endswith("." + OWN_DOMAIN)


#: The "From:" header a mail client writes into the body when a message is
#: forwarded or replied to. Outlook and Gmail both emit it.
_EMBEDDED_FROM = re.compile(
    # The display name is *non-greedy*: with `*` it swallowed the local part
    # of a bare `From: donotreplyshapephx@phoenix.gov` line and left
    # `x@phoenix.gov` as the address, which is un-replyable and -- because
    # `classify_channel` matches known clients by exact address -- silently
    # demoted a forwarded client message to CONSULTANT_EMAIL.
    r"^\s*From:\s*\"?([^\"<\n]*?)\"?\s*<?([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+)>?",
    re.IGNORECASE | re.MULTILINE,
)


#: Subject prefix a mail client writes when *forwarding*. `RE:` is
#: deliberately absent -- a reply is the case this exists to exclude.
_FORWARD_SUBJECT = re.compile(r"\s*fwd?\s*:", re.IGNORECASE)

#: Body separators written above a forwarded message. Outlook's
#: "-----Original Message-----" is not here: it heads replies as well as
#: forwards, so it cannot tell them apart.
_FORWARD_MARKER = re.compile(
    r"^\s*-+\s*forwarded message"
    r"|^\s*begin forwarded message\b"
    r"|^\s*subject:\s*fwd?\s*:",
    re.IGNORECASE | re.MULTILINE,
)


def is_forward(subject: str, body: str) -> bool:
    """True when our own message relays someone else's rather than answers it.

    A reply quotes the party it answers, so an embedded `From:` block alone
    cannot tell a forward from a reply. Reading our own reply as a forward
    re-ingests everything PCD sends as an inbound event attributed to the
    person we were writing to; replies quote by default, so that is not a
    corner case but most of the outbound mail in the ingest window.
    """
    return bool(_FORWARD_SUBJECT.match(subject or "")
                or _FORWARD_MARKER.search(body or ""))


def origin_sender(sender: str, body: str, subject: str = "") -> str:
    """Who actually wrote this, rather than who relayed it.

    Jay's rule, 2026-09-05: "a forwarded email from the city is the same as
    me receiving the email directly." Authority belongs to the author of the
    words, not to the envelope -- so a jurisdiction notice keeps jurisdiction
    authority however many hops it took to arrive.

    This matters in both directions. Nine of the messages in the 2026-09-05
    corpus reached PCD only because the project engineer forwarded them, and
    each carries `From: City of Phoenix <donotreplyshapephx@phoenix.gov>` in
    its body; treating those as consultant chatter loses real permit events.
    Equally, Jay forwards his own mail into info@, so a chain that ends at the
    city must not be discarded as our own just because he relayed it.

    A jurisdiction address anywhere in the chain wins, because the city's
    words are the thing being reported. Otherwise the envelope stands, except
    for our own mail wrapping someone else's, where the inner author stands.
    """
    embedded = [address for _, address in _EMBEDDED_FROM.findall(body or "")]
    for address in embedded:
        if is_jurisdiction(address):
            return address
    # Only a *forward* re-attributes. Without the `is_forward` test this
    # branch fired on every reply we send that quotes its parent, so ordinary
    # outbound mail became a stream of inbound events attributed to the
    # recipient -- and because a reply's body differs from the original, the
    # statement digest differed too and the duplicate gate did not catch it,
    # so one jurisdiction event bumped the Notion redline round twice.
    if embedded and is_own_mail(sender) and is_forward(subject, body):
        return embedded[0]
    return sender


def classify_channel(sender: str, known_clients: Iterable[str] = ()) -> Channel | None:
    """Return the channel this sender belongs to, or None if it is noise.

    Channel is also the authority ranking. A jurisdiction states what is true
    about a permit; a client or consultant reports it second-hand; we assert
    it. Only the first is evidence about the permit itself.
    """
    domain = _domain(sender)
    if any(domain == d or domain.endswith("." + d) for d in NEVER_RELEVANT):
        return None
    if is_own_mail(sender):
        return None
    if is_jurisdiction(sender):
        return Channel.JURISDICTION_EMAIL
    if sender.strip().lower() in {c.lower() for c in known_clients}:
        return Channel.CLIENT_EMAIL
    return Channel.CONSULTANT_EMAIL


def is_relevant(sender: str, subject: str, body: str,
                known_clients: Iterable[str] = ()) -> bool:
    """A message is operational if it is from a jurisdiction, or it names a
    permit, or a known client is talking about permit work."""
    channel = classify_channel(sender, known_clients)
    if channel is None:
        return False
    if channel is Channel.JURISDICTION_EMAIL:
        return True
    text = f"{subject}\n{body}"
    if extract_permit_numbers(text):
        return True
    return bool(_PERMIT_HINT.search(text))


def from_gmail_message(
    message: dict[str, Any], known_clients: Iterable[str] = ()
) -> RawEvent | None:
    """Adapt one Gmail message dict into a RawEvent, or None if not relevant."""
    subject = message.get("subject", "") or ""
    plaintext = message.get("plaintextBody")
    body = plaintext or message.get("snippet") or ""
    # Attribute to the author, not the relay: a forwarded city notice is a
    # city notice. Everything downstream -- relevance, channel, authority --
    # is decided on this address rather than the envelope.
    sender = origin_sender(message.get("sender", ""), body, subject)
    if not is_relevant(sender, subject, body, known_clients):
        return None

    channel = classify_channel(sender, known_clients)
    assert channel is not None
    # `internalDate` FIRST. It is Gmail's own epoch-millisecond record of when
    # the message arrived here, which is what an SLA clock must run from. The
    # `Date:` header is sender-supplied, RFC-2822, and can be wrong or absent.
    received = message.get("internalDate") or message.get("date")
    return RawEvent(
        source=channel,
        external_id=str(message.get("id") or message.get("threadId") or subject),
        received_at=_parse_time(received),
        subject=subject,
        body=body,
        sender=sender,
        recipients=tuple(message.get("toRecipients", ()) or ()),
        attachments=tuple(
            a.get("filename", "") for a in message.get("attachments", ()) or ()
        ),
        # `gmail._plain_text` returns "" for payload shapes it cannot walk
        # (depth > 8, unusual multipart), and then this body is Gmail's
        # ~200-character preview. The duplicate gate must not suppress on one.
        body_is_preview=not plaintext and bool(message.get("snippet")),
    )


#: At or above this an epoch stamp is milliseconds; below it, seconds. 1e11
#: seconds is the year 5138 and 1e11 milliseconds is 1973, so no timestamp a
#: permit event can carry is ambiguous. Without the guard an epoch-*seconds*
#: value was divided by 1000 anyway and became a 1970 date, so the first SLA
#: sweep fired an "L1, 490000h overdue" escalation at Jay for mail that had
#: arrived minutes earlier.
_EPOCH_MILLIS_FLOOR = 1e11


def _from_epoch(value: float) -> dt.datetime:
    seconds = value / 1000 if abs(value) >= _EPOCH_MILLIS_FLOOR else value
    return dt.datetime.fromtimestamp(seconds, dt.timezone.utc)


def _parse_time(value: Any) -> dt.datetime:
    """When this actually arrived. Falling back to `now()` is a real failure.

    Verified 2026-09-05 against 104 live info@ messages: EVERY ONE fell back
    to `now()`, because Gmail's `Date:` header is RFC 2822
    ("Sat, 05 Sep 2026 17:57:32 +0000 (UTC)") and this function understood
    only ISO strings and epoch digits. That silently stamped every event with
    its ingest time, which breaks two things at once -- an SLA clock that
    starts late never escalates, and the duplicate gate compares
    `abs(delta received_at)`, so events weeks apart would collapse into one
    60-minute window and suppress each other.
    """
    if isinstance(value, dt.datetime):
        return as_utc(value)
    if isinstance(value, (int, float)):
        return _from_epoch(value)
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.isdigit():  # Gmail internalDate, epoch milliseconds
            return _from_epoch(int(text))
        try:
            return as_utc(
                dt.datetime.fromisoformat(text.replace("Z", "+00:00")))
        except ValueError:
            pass
        try:  # RFC 2822, the only format a real Gmail `Date:` header uses
            parsed = email.utils.parsedate_to_datetime(text)
            if parsed is not None:
                return as_utc(parsed)
        except (TypeError, ValueError):
            pass
    return dt.datetime.now(dt.timezone.utc)
