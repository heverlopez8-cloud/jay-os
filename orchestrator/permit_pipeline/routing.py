"""Assign exactly one accountable owner, one next action and one clock.

Ownership follows the roles PCD already runs (visible in the Notion
"Automation Status" stamps): Eric coordinates with jurisdictions, Jay owns
money, escalations and anything the rules cannot place. There is no "team"
owner and no round-robin -- shared ownership is how the Riata corrections
sat for 72 days.

Redline corrections were Guillermo's. He is leaving, and Jay holds that
route himself until the new hires start (Jay's instruction, 2026-09-05).
GUILLERMO stays defined below so historical audit rows still resolve to a
person; nothing routes to him.

SLAs are deliberately short at the front of the chain. The failure this
pipeline exists to prevent is not a missed deadline; it is silence.
"""
from __future__ import annotations

import datetime as dt

from .model import (
    Assignment,
    EventClass,
    Person,
    ProjectRecord,
    UTC,
)

JAY = Person("jay", "Hever 'Jay' Lopez", "jay@professionalcadesign.com",
             "f041a089-16bb-458c-ad61-320b74797699")
ERIC = Person("eric", "Eric Ortiz", "eric@professionalcadesign.com",
              "396d872b-594c-81fb-9aed-0002b3b055ee")
GUILLERMO = Person("guillermo", "Guillermo Ochoa",
                   "guillermo@professionalcadesign.com",
                   "bbcb8d24-b994-49c0-b8fe-71977ff0c995")

PEOPLE = {p.key: p for p in (JAY, ERIC, GUILLERMO)}

#: Hours allowed to finish the work, once acknowledged. The Riata redlines
#: took 72 days; ten business days is the outer bound before Jay hears about it.
_COMPLETION_HOURS: dict[EventClass, int] = {
    EventClass.CORRECTIONS_REQUIRED: 80,
    EventClass.INFO_REQUEST: 40,
    EventClass.FEE_DUE: 40,
    EventClass.APPROVED: 40,
    EventClass.INSPECTION: 40,
    EventClass.CLIENT_ESCALATION: 8,
}

#: Owner, SLA in hours, and who we are waiting on, per event class.
_ROUTES: dict[EventClass, tuple[Person, int, str | None]] = {
    # Redline work belongs to the drafter, and it is the clock that matters:
    # acknowledge within one business day, never another 72-day gap. Jay is
    # the drafter of record until the new hires start.
    EventClass.CORRECTIONS_REQUIRED: (JAY, 24, None),
    # Jurisdiction questions are coordination, not drafting.
    EventClass.INFO_REQUEST: (ERIC, 48, "Jurisdiction"),
    # Money and anything that blocks issuance goes straight to Jay.
    EventClass.FEE_DUE: (JAY, 24, "Payment"),
    # An approval is only worth having if someone collects the permit.
    EventClass.APPROVED: (ERIC, 48, None),
    EventClass.INSPECTION: (ERIC, 48, None),
    # A client who had to chase us is Jay's, within hours.
    EventClass.CLIENT_ESCALATION: (JAY, 4, "Client"),
}

#: Stage-based fallback, used when the class carries no route of its own.
_STAGE_OWNERS: dict[str, Person] = {
    "1 · Intake": ERIC,
    "2 · Design": JAY,
    "3 · Review & Redlines": JAY,
    "4 · Submitted to City": ERIC,
    "5 · Approved": ERIC,
    "6 · Construction": ERIC,
    "On Hold": JAY,
}

_NEXT_ACTIONS: dict[EventClass, str] = {
    EventClass.CORRECTIONS_REQUIRED: (
        "Plans returned for corrections ({jurisdiction}, {date}, permit "
        "{permit}). Download EVERY comment - the comment sheet is not the "
        "whole review; walk the returned-file redlines too (SOP-0004 #2). "
        "Then: numbered response letter, cloud + delta each change, "
        "coordinate repeated values across sheets, re-seal any revised "
        "sealed sheet, run the QA checker, and confirm whether fees are due "
        "at resubmittal. Acknowledge receipt today."
    ),
    EventClass.INFO_REQUEST: (
        "{jurisdiction} requested information on permit {permit} ({date}). "
        "Answer the reviewer directly, copy Jay, and record the answer on "
        "this project. If it needs an engineering decision or contradicts a "
        "prior approval, escalate to Jay before responding (SOP-0004)."
    ),
    EventClass.FEE_DUE: (
        "Fees are due on permit {permit} ({jurisdiction}, {date}) and are "
        "blocking issuance. Confirm the amount, confirm who pays, and get "
        "the client an answer before the permit stalls."
    ),
    EventClass.APPROVED: (
        "Permit {permit} approved/issued by {jurisdiction} ({date}). "
        "Retrieve the issued permit, file it, tell the client, and advance "
        "the project stage."
    ),
    EventClass.INSPECTION: (
        "Inspection activity on permit {permit} ({jurisdiction}, {date}). "
        "Confirm the result and the next inspection, and update the client."
    ),
    EventClass.CLIENT_ESCALATION: (
        "CLIENT ESCALATION ({date}): the client chased us for status on "
        "permit {permit}. Reply directly with (a) what the current status "
        "actually is, (b) what is required next, and (c) a committed date. "
        "This is a client-trust event, not a status update."
    ),
}

#: Event classes for which we prepare client-facing wording automatically.
CLIENT_COMMUNICATION_CLASSES = frozenset({
    EventClass.CORRECTIONS_REQUIRED,
    EventClass.APPROVED,
    EventClass.FEE_DUE,
    EventClass.CLIENT_ESCALATION,
})


def business_hours_from(start: dt.datetime, hours: int) -> dt.datetime:
    """Add SLA hours, skipping weekends so a Friday event is not 'late' Monday."""
    remaining = hours
    cursor = start.astimezone(UTC)
    while remaining > 0:
        cursor += dt.timedelta(hours=1)
        if cursor.weekday() < 5:
            remaining -= 1
    return cursor


def assign(
    event_class: EventClass,
    project: ProjectRecord,
    occurred_at: dt.datetime,
    jurisdiction: str = "",
    permit: str = "",
) -> Assignment | None:
    """Return the single accountable owner, or None so the caller fails closed."""
    route = _ROUTES.get(event_class)
    if route is not None:
        owner, sla_hours, waiting_on = route
        reason = f"route:{event_class.value}"
    else:
        owner_from_stage = _STAGE_OWNERS.get(project.stage or "")
        if owner_from_stage is None:
            return None
        owner, sla_hours, waiting_on = owner_from_stage, 48, None
        reason = f"stage:{project.stage}"

    template = _NEXT_ACTIONS.get(event_class)
    if template is None:
        return None

    next_action = template.format(
        jurisdiction=jurisdiction or project.jurisdiction or "the jurisdiction",
        permit=permit or (project.permit_numbers[0] if project.permit_numbers
                          else "(number not stated)"),
        date=occurred_at.astimezone(UTC).strftime("%Y-%m-%d"),
    )

    completion_hours = _COMPLETION_HOURS.get(event_class, 80)
    return Assignment(
        owner=owner,
        next_action=next_action,
        due_at=business_hours_from(occurred_at, sla_hours),
        sla_hours=sla_hours,
        waiting_on=waiting_on,
        reason=reason,
        complete_by=business_hours_from(occurred_at, completion_hours),
        completion_hours=completion_hours,
    )


def next_redline_status(current: tuple[str, ...]) -> str:
    """Bump the redline round, because SOP-0004 requires the round be logged."""
    highest = 0
    for value in current:
        text = value.upper().strip()
        if text.startswith("REDLINES"):
            tail = text.replace("REDLINES", "").strip()
            if tail.isdigit():
                highest = max(highest, int(tail))
    return f"REDLINES {min(highest + 1, 5):02d}"


def escalation_target(level: int) -> Person:
    """Unacknowledged work climbs to Jay and stays there, loudly."""
    return JAY
