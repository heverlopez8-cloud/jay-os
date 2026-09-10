"""Live production-style run against the Notion ZZ-TEST canary project.

Runs the real pipeline over a real jurisdiction event and prints the exact
Notion write it decided on, so the write can be applied and then read back
out of Notion independently.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from permit_pipeline import AuditStore, PermitPipeline, run_sla_sweep
from permit_pipeline.model import Channel, ProjectRecord, RawEvent, UTC
from permit_pipeline.ports import (
    RecordingNotifier, RecordingNotionPort, StaticDirectory,
)
from permit_pipeline.sources import from_gmail_message

CANARY_PAGE = "3c416cf6-a657-81ba-be4f-c025fe86f39c"

CANARY = ProjectRecord(
    page_id=CANARY_PAGE,
    name="ZZ-TEST — Gate Canary (delete me)",
    permit_numbers=("ZZTEST-2026-00041",),
    address="1042 N CANARY LN GILBERT 85234",
    stage="4 · Submitted to City",
    status=("CITY REVIEW",),
    jurisdiction="Town of Gilbert",
)

# The real Gilbert EnerGov notice, re-pointed at the canary permit. This is
# byte-for-byte the wording that arrived on 2026-05-19 for COMM-2026-00141
# and that nobody acted on for 637 N Riata on 2026-04-15.
GMAIL_MESSAGE = {
    "id": "canary-energov-20260903",
    "sender": "energov.noreply@gilbertaz.gov",
    "subject": ("Town of Gilbert plan review update: Returned for corrections "
                "and requires your attention  ZZTEST-2026-00041"),
    "plaintextBody": (
        "Dear Customer,\n\nYour plans have been reviewed and are being "
        "returned for corrections. Please login to your OneStopShop portal "
        "at: https://onestopshop.gilbertaz.gov/ to review the correction "
        "requirements.\n"
    ),
    "date": "2026-09-03T17:05:00Z",
    "toRecipients": ["jay@professionalcadesign.com",
                     "info@professionalcadesign.com"],
}


def main() -> int:
    store = AuditStore(Path(__file__).parent / "live_canary.db")
    notion = RecordingNotionPort()
    notifier = RecordingNotifier()
    pipeline = PermitPipeline(store, StaticDirectory([CANARY]), notion, notifier)

    print("=" * 72)
    print("STEP 1  EVENT ENTERS  (Jay does nothing)")
    raw = from_gmail_message(GMAIL_MESSAGE)
    if raw is None:
        print("  message judged non-operational; nothing to do")
        return 1
    print(f"  channel : {raw.source.value}")
    print(f"  from    : {raw.sender}")
    print(f"  subject : {raw.subject}")
    print(f"  event id: {raw.event_id}")

    outcome = pipeline.process(raw)

    print("\nSTEP 2  PIPELINE DECISIONS")
    print(f"  project identified : {outcome.match.project.name} "
          f"(via {outcome.match.basis}, conf {outcome.match.confidence:.2f})")
    print(f"  classified as      : {outcome.classification.event_class.value} "
          f"(conf {outcome.classification.confidence:.2f})")
    print(f"  evidence           : {outcome.classification.evidence[0]}")
    print(f"  accountable owner  : {outcome.assignment.owner.name} "
          f"<{outcome.assignment.owner.email}>")
    print(f"  SLA                : {outcome.assignment.sla_hours}h, due "
          f"{outcome.assignment.due_at:%Y-%m-%d %H:%M} UTC")
    print(f"  released           : {outcome.released}   "
          f"failed_closed: {outcome.failed_closed}")

    print("\nSTEP 3  NOTION WRITE THE PIPELINE DECIDED ON")
    page_id, properties = notion.updates[0]
    print(f"  page: {page_id}")
    print(json.dumps(properties, indent=2)[:2000])

    print("\nSTEP 4  OWNER NOTIFICATION ACTUALLY SENT")
    sent = notifier.sent[0]
    print(f"  to      : {sent.recipient}")
    print(f"  subject : {sent.subject}")
    print("  body    :")
    for line in sent.body.splitlines():
        print(f"    {line}")

    print("\nSTEP 5  CLIENT COMMUNICATION PREPARED (not sent)")
    for line in (outcome.client_message or "").splitlines():
        print(f"    {line}")

    print("\nSTEP 6  ESCALATION WHEN THE OWNER STAYS SILENT")
    notifier.sent.clear()
    past_sla = outcome.assignment.due_at + dt.timedelta(minutes=1)
    escalated = run_sla_sweep(store, notifier, at=past_sla)
    print(f"  swept at {past_sla:%Y-%m-%d %H:%M} UTC -> escalated {escalated}")
    if notifier.sent:
        print(f"  to      : {notifier.sent[0].recipient}")
        print(f"  subject : {notifier.sent[0].subject}")

    print("\nSTEP 7  AUDIT TRAIL")
    for row in store.trail(raw.event_id):
        detail = json.loads(row["detail"])
        keys = ", ".join(f"{k}={str(v)[:48]}" for k, v in list(detail.items())[:3])
        print(f"  {row['seq']:>3}  {row['at'][:19]}  "
              f"{row['step']:<20} {row['outcome']:<10} {keys}")

    # Emit the write so it can be applied and verified independently.
    outbox = Path(__file__).parent / "notion_outbox.json"
    outbox.write_text(json.dumps(
        {"page_id": page_id, "properties": properties,
         "comment": notion.comments[0][1] if notion.comments else None},
        indent=2,
    ))
    print(f"\n  notion write emitted to {outbox.name}")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
