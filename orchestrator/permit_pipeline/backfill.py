"""Historical audit: what would this pipeline have caught, and when?

Strictly read-only. It runs real jurisdiction mail through the real
decision logic with recording ports, so nothing is written to Notion and
nothing is emailed. The output is the recovery list -- projects that had
an actionable event nobody worked, and the SLA breaches that followed.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
from pathlib import Path

from .audit import AuditStore
from .model import EventClass, ExceptionCode, ProjectRecord, now
from .pipeline import PermitPipeline
from .ports import RecordingNotifier, RecordingNotionPort, StaticDirectory
from .sources import from_gmail_message


def run(messages: list[dict], projects: list[ProjectRecord],
        clients: list[str] | None = None,
        at: dt.datetime | None = None) -> dict:
    """Replay history. Mutates nothing outside a throwaway audit database."""
    moment = at or now()
    store = AuditStore(":memory:")
    notion = RecordingNotionPort()
    notifier = RecordingNotifier()
    pipeline = PermitPipeline(store, StaticDirectory(projects), notion, notifier)

    report: dict = {
        "messages_seen": len(messages),
        "non_operational": 0,
        "events": 0,
        "matched": [],
        "ambiguous": [],
        "unmatched": [],
        "unclassified": [],
        "no_action": 0,
        "by_class": collections.Counter(),
        "sla_breaches": [],
        "projects_with_missed_action": collections.Counter(),
    }

    for message in messages:
        raw = from_gmail_message(message, clients or [])
        if raw is None:
            report["non_operational"] += 1
            continue
        report["events"] += 1
        outcome = pipeline.process(raw)

        if outcome.exception is not None:
            code = outcome.exception.code
            entry = {
                "subject": raw.subject[:90], "sender": raw.sender,
                "received": raw.received_at.isoformat()[:10],
                "detail": outcome.exception.detail[:160],
            }
            if code is ExceptionCode.PROJECT_AMBIGUOUS:
                report["ambiguous"].append(entry)
            elif code is ExceptionCode.PROJECT_NOT_MATCHED:
                report["unmatched"].append(entry)
            elif code is ExceptionCode.CLASSIFICATION_UNCERTAIN:
                report["unclassified"].append(entry)
            continue

        if outcome.assignment is None:
            report["no_action"] += 1
            continue

        event_class = outcome.classification.event_class
        report["by_class"][event_class.value] += 1
        project_name = outcome.match.project.name
        report["matched"].append({
            "project": project_name,
            "class": event_class.value,
            "owner": outcome.assignment.owner.name,
            "received": raw.received_at.isoformat()[:10],
            "due": outcome.assignment.due_at.isoformat()[:10],
            "subject": raw.subject[:90],
        })

        # Nothing acknowledged these historically, so every one of them is
        # a breach measured against today.
        overdue_days = (moment - outcome.assignment.due_at).days
        if overdue_days > 0 and event_class not in (EventClass.ACKNOWLEDGEMENT,):
            report["sla_breaches"].append({
                "project": project_name, "class": event_class.value,
                "owner": outcome.assignment.owner.name,
                "due": outcome.assignment.due_at.isoformat()[:10],
                "days_overdue": overdue_days,
                "subject": raw.subject[:90],
            })
            if event_class in (EventClass.CORRECTIONS_REQUIRED,
                               EventClass.FEE_DUE,
                               EventClass.CLIENT_ESCALATION,
                               EventClass.INFO_REQUEST):
                report["projects_with_missed_action"][project_name] += 1

    store.close()
    return report


def render(report: dict) -> str:
    lines: list[str] = []
    add = lines.append
    add("=" * 74)
    add("HISTORICAL BACKFILL AUDIT  (read-only; nothing written, nothing sent)")
    add("=" * 74)
    add(f"messages examined      : {report['messages_seen']}")
    add(f"  non-operational      : {report['non_operational']}")
    add(f"  operational events   : {report['events']}")
    add(f"  confidently matched  : {len(report['matched'])}")
    add(f"  ambiguous (refused)  : {len(report['ambiguous'])}")
    add(f"  unmatched (refused)  : {len(report['unmatched'])}")
    add(f"  unclassified         : {len(report['unclassified'])}")
    add(f"  receipts / no action : {report['no_action']}")
    add("")
    add("BY EVENT CLASS")
    for name, count in report["by_class"].most_common():
        add(f"  {name:<24} {count}")
    add("")
    add(f"SLA BREACHES THAT WOULD HAVE FIRED: {len(report['sla_breaches'])}")
    for breach in sorted(report["sla_breaches"],
                         key=lambda b: -b["days_overdue"])[:15]:
        add(f"  {breach['days_overdue']:>4}d  {breach['class']:<22}"
            f" {breach['owner']:<16} {breach['project'][:44]}")
    add("")
    add("PROJECTS WITH MISSED ACTIONABLE EVENTS")
    for name, count in report["projects_with_missed_action"].most_common(15):
        add(f"  {count:>3}  {name[:64]}")
    if report["ambiguous"]:
        add("")
        add("AMBIGUOUS - need a permit number to resolve")
        for entry in report["ambiguous"][:10]:
            add(f"  {entry['received']}  {entry['subject'][:64]}")
    if report["unmatched"]:
        add("")
        add("UNMATCHED - no project identity in the message")
        for entry in report["unmatched"][:10]:
            add(f"  {entry['received']}  {entry['subject'][:64]}")
    return "\n".join(lines)
