"""Domain model for the permit/review event pipeline.

Every operational event that enters the business becomes a `PermitEvent`.
The non-negotiable rule is encoded in `Decision`: an event may only be
released to the business when it carries a project, a status, a next
action, exactly one owner, an SLA, a notification and an audit trail.
Anything short of that becomes an `Exception_` that a human must resolve.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import enum
import hashlib
import json
from typing import Any


UTC = dt.timezone.utc


def now() -> dt.datetime:
    return dt.datetime.now(UTC)


def iso(moment: dt.datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def as_utc(moment: dt.datetime) -> dt.datetime:
    """Aware, in UTC. A naive datetime is read as UTC rather than rejected.

    Naive timestamps do reach us: `dt.datetime.fromisoformat("2026-09-05T17:57:32")`
    is tz-less, and so is a bare `"2026-09-05"`. Mixing one with an aware value
    raises `TypeError: can't subtract offset-naive and offset-aware datetimes`,
    and TypeError is not a ConnectorError, so `__main__` does not catch it: a
    single tz-less date aborted the whole ingest run and every message behind
    it went unprocessed -- the silence this pipeline exists to prevent.
    """
    return (moment.replace(tzinfo=UTC) if moment.tzinfo is None
            else moment.astimezone(UTC))


class EventClass(str, enum.Enum):
    """What the jurisdiction actually told us."""

    CORRECTIONS_REQUIRED = "corrections_required"
    APPROVED = "approved"
    INFO_REQUEST = "info_request"
    FEE_DUE = "fee_due"
    INSPECTION = "inspection"
    CLIENT_ESCALATION = "client_escalation"
    ACKNOWLEDGEMENT = "acknowledgement"
    UNKNOWN = "unknown"


#: Classes that carry no work. They are still matched, logged and audited --
#: they simply do not generate an action, an owner or a notification.
NO_ACTION_CLASSES = frozenset({EventClass.ACKNOWLEDGEMENT})


class ExceptionCode(str, enum.Enum):
    """Every way the chain is allowed to stop. None of them are silent."""

    PROJECT_NOT_MATCHED = "project_not_matched"
    PROJECT_AMBIGUOUS = "project_ambiguous"
    CLASSIFICATION_UNCERTAIN = "classification_uncertain"
    OWNER_UNDETERMINED = "owner_undetermined"
    NOTION_WRITE_FAILED = "notion_write_failed"
    NOTIFICATION_FAILED = "notification_failed"
    CONNECTOR_FAILED = "connector_failed"
    SLA_BREACHED = "sla_breached"


class EventState(str, enum.Enum):
    """Where a piece of work actually is.

    NOTIFIED is not ACKNOWLEDGED. Telling someone is not the same as them
    having seen it, and neither is the same as the work being done -- the
    escalation clock has to know the difference.
    """

    NOTIFIED = "notified"
    ACKNOWLEDGED = "acknowledged"
    COMPLETED = "completed"
    ESCALATED = "escalated"
    NO_ACTION = "no_action"
    EXCEPTION = "exception"
    #: A second copy of a statement already delivered. The work is real but
    #: it is the same work, so this row owes nobody anything -- it must never
    #: enter an acknowledgement or completion clock.
    SUPPRESSED_DUPLICATE = "suppressed_duplicate"


#: States that still owe the business something.
OPEN_STATES = frozenset({
    EventState.NOTIFIED, EventState.ACKNOWLEDGED, EventState.ESCALATED,
})


class Channel(str, enum.Enum):
    JURISDICTION_EMAIL = "jurisdiction_email"
    JURISDICTION_PORTAL = "jurisdiction_portal"
    CLIENT_EMAIL = "client_email"
    CONSULTANT_EMAIL = "consultant_email"
    MEETING = "meeting"
    MANUAL = "manual"


@dataclasses.dataclass(frozen=True)
class RawEvent:
    """An event exactly as it entered the business, before interpretation."""

    source: Channel
    external_id: str
    received_at: dt.datetime
    subject: str
    body: str
    sender: str = ""
    recipients: tuple[str, ...] = ()
    attachments: tuple[str, ...] = ()
    #: True when `body` is a truncated preview rather than the full message.
    #: Gmail's `snippet` is ~200 characters, and jurisdiction form letters
    #: share their opening paragraph verbatim, so two genuinely different
    #: notices whose plaintext extraction failed carry identical previews.
    #: Length cannot tell them apart -- a 200-char preview clears any
    #: sensible floor -- so the duplicate gate needs provenance instead.
    body_is_preview: bool = False

    @property
    def event_id(self) -> str:
        """Stable id so replaying the same source event is idempotent."""
        digest = hashlib.sha256(
            f"{self.source.value}:{self.external_id}".encode()
        ).hexdigest()
        return f"EVT-{digest[:16]}"

    @property
    def text(self) -> str:
        return f"{self.subject}\n{self.body}"


@dataclasses.dataclass(frozen=True)
class Person:
    key: str
    name: str
    email: str
    notion_user_id: str


@dataclasses.dataclass(frozen=True)
class ProjectRecord:
    """The slice of the Notion project record this pipeline reasons about."""

    page_id: str
    name: str
    permit_numbers: tuple[str, ...] = ()
    address: str = ""
    apn: str = ""
    stage: str | None = None
    status: tuple[str, ...] = ()
    current_owner_id: str | None = None
    jurisdiction: str | None = None


@dataclasses.dataclass(frozen=True)
class Classification:
    event_class: EventClass
    confidence: float
    evidence: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class Match:
    project: ProjectRecord | None
    confidence: float
    basis: str
    candidates: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class Assignment:
    """Exactly one accountable owner, plus the two clocks they are held to.

    `due_at` is when they must have acknowledged. `complete_by` is when the
    work itself is due. Separating them is what stops "I saw it" from being
    mistaken for "it is handled".
    """

    owner: Person
    next_action: str
    due_at: dt.datetime
    sla_hours: int
    waiting_on: str | None
    reason: str
    complete_by: dt.datetime | None = None
    completion_hours: int | None = None


@dataclasses.dataclass(frozen=True)
class Exception_:
    """A failure that a human must resolve. Never silent, always notified."""

    event_id: str
    code: ExceptionCode
    detail: str
    created_at: dt.datetime = dataclasses.field(default_factory=now)

    def summary(self) -> str:
        return f"[{self.code.value}] {self.detail}"


@dataclasses.dataclass(frozen=True)
class Notification:
    channel: str
    recipient: str
    subject: str
    body: str
    sent_at: dt.datetime | None = None
    ok: bool = False
    detail: str = ""


@dataclasses.dataclass
class Outcome:
    """The complete, auditable result of putting one event through the chain."""

    event_id: str
    raw: RawEvent
    classification: Classification | None = None
    match: Match | None = None
    assignment: Assignment | None = None
    notion_updates: dict[str, Any] = dataclasses.field(default_factory=dict)
    notifications: list[Notification] = dataclasses.field(default_factory=list)
    exception: Exception_ | None = None
    client_message: str | None = None
    released: bool = False

    @property
    def failed_closed(self) -> bool:
        return self.exception is not None

    def to_json(self) -> str:
        def enc(value: Any) -> Any:
            if isinstance(value, dt.datetime):
                return iso(value)
            if isinstance(value, enum.Enum):
                return value.value
            if dataclasses.is_dataclass(value):
                return dataclasses.asdict(value)
            return str(value)

        return json.dumps(dataclasses.asdict(self), default=enc, indent=2)
