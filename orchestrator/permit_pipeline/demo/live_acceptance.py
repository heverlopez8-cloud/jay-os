"""Live acceptance run against real services.

Uses the live Notion connection, the live SMTP account, and the ZZ-TEST
canary project. Owner notifications are redirected to Jay so that a test
record can never page an employee.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from permit_pipeline import AuditStore, PermitPipeline, run_sla_sweep
from permit_pipeline import config, factory, health
from permit_pipeline.model import Channel, ProjectRecord, RawEvent, UTC
from permit_pipeline.notify import CompositeNotifier, SmtpNotifier
from permit_pipeline.ports import (
    ConnectorError, HttpNotionDirectory, HttpNotionPort, RecordingNotionPort,
)
from permit_pipeline.routing import JAY

DATABASE = "53b6c76e-76cd-4ea8-b765-014c1e728a9e"
CANARY_PAGE = "3c416cf6-a657-81ba-be4f-c025fe86f39c"
CANARY = ProjectRecord(
    CANARY_PAGE, "ZZ-TEST — Gate Canary (delete me)",
    ("ZZTEST-2026-00041",), address="1042 N CANARY LN GILBERT 85234",
    stage="4 · Submitted to City", status=("CITY REVIEW",),
    jurisdiction="Town of Gilbert",
)
EVENT = RawEvent(
    Channel.JURISDICTION_EMAIL,
    f"live-acceptance-{dt.datetime.now(UTC):%Y%m%d%H%M%S}",
    dt.datetime.now(UTC),
    "Town of Gilbert plan review update: Returned for corrections and "
    "requires your attention  ZZTEST-2026-00041",
    "Dear Customer,\n\nYour plans have been reviewed and are being returned "
    "for corrections. Please login to your OneStopShop portal at: "
    "https://onestopshop.gilbertaz.gov/ to review the correction "
    "requirements.\n",
    sender="energov.noreply@gilbertaz.gov",
)


def banner(text: str) -> None:
    print("\n" + "=" * 72)
    print(text)
    print("=" * 72)


def main() -> int:
    db = Path(__file__).parent / "live_acceptance.db"
    if db.exists():
        db.unlink()
    store = AuditStore(db)

    banner("CREDENTIALS (sources only, never values)")
    for key in ("NOTION_API_KEY", "EMAIL_SMTP_HOST", "EMAIL_ADDRESS"):
        print(f"  {key:<18} {config.source_of(key)}")

    banner("LIVE NOTION READ")
    try:
        directory = HttpNotionDirectory(
            config.require("NOTION_API_KEY", "project directory"), DATABASE)
        live_projects = directory.projects()
        health.record(store, "notion_write", True,
                      f"read {len(live_projects)} projects")
        print(f"  read {len(live_projects)} live projects from Notion")
    except ConnectorError as error:
        print(f"  FAILED: {error}")
        return 2

    notifier = CompositeNotifier(SmtpNotifier(redirect_to=JAY.email))
    health.record(store, "notification_transport", True, "smtp ready")

    # --- Run 1: the real Notion write path -------------------------------
    banner("RUN 1 — LIVE NOTION WRITE PATH (proves no silent downgrade)")
    print(f"  writer: {factory.describe_notion_writer()}")
    live_notion = factory.notion_writer()
    pipeline = PermitPipeline(
        store, _Static([CANARY]), live_notion, notifier)
    outcome = pipeline.process(EVENT)
    if outcome.failed_closed:
        print(f"  FAILED CLOSED as designed: "
              f"{outcome.exception.code.value}")
        print(f"  detail: {outcome.exception.detail[:160]}")
        print(f"  released={outcome.released} (must be False)")
        print(f"  open exceptions on disk: {len(store.open_exceptions())}")
        sent = [n for n in store.notifications_for(outcome.event_id)]
        for row in sent:
            print(f"  alert -> {row['recipient']} ok={bool(row['ok'])} "
                  f"({row['channel']})")
        health.record(store, "notion_write", False, outcome.exception.detail[:80])
    else:
        print("  LIVE NOTION WRITE SUCCEEDED (unattended, no human in the loop)")
        print(f"  fields written: {sorted(outcome.notion_updates)}")
        print(f"  owner        : {outcome.assignment.owner.name}")
        print(f"  ack due      : {outcome.assignment.due_at:%Y-%m-%d %H:%M} UTC")
        for note in outcome.notifications:
            print(f"  NOTIFICATION -> {note.recipient} ok={note.ok} "
                  f"via {note.channel}")
        health.record(store, "notion_write", True, "bridge write ok")

    # --- Run 2: full green path, real email ------------------------------
    banner("RUN 2 — FULL CHAIN with real owner notification")
    store2 = AuditStore(Path(__file__).parent / "live_acceptance2.db")
    recording = RecordingNotionPort()
    pipeline2 = PermitPipeline(store2, _Static([CANARY]), recording, notifier)
    second = RawEvent(
        EVENT.source, EVENT.external_id + "-b", EVENT.received_at,
        EVENT.subject, EVENT.body, EVENT.sender)
    outcome2 = pipeline2.process(second)

    print(f"  project      : {outcome2.match.project.name}")
    print(f"  classified   : {outcome2.classification.event_class.value} "
          f"({outcome2.classification.confidence:.2f})")
    print(f"  owner        : {outcome2.assignment.owner.name}")
    print(f"  ack due      : {outcome2.assignment.due_at:%Y-%m-%d %H:%M} UTC "
          f"({outcome2.assignment.sla_hours}h)")
    print(f"  complete by  : {outcome2.assignment.complete_by:%Y-%m-%d %H:%M} UTC")
    print(f"  released     : {outcome2.released}")
    for note in outcome2.notifications:
        print(f"  NOTIFICATION -> {note.recipient} ok={note.ok} "
              f"via {note.channel} [{note.detail[:60]}]")
    row = store2.open_events()[0]
    print(f"  state        : {row['state']}  acknowledged={row['acknowledged_at']}")

    banner("ESCALATION ON THE ACKNOWLEDGEMENT CLOCK")
    past = outcome2.assignment.due_at + dt.timedelta(minutes=1)
    escalated = run_sla_sweep(store2, notifier, at=past)
    print(f"  escalated: {escalated}")

    banner("HEALTH")
    for entry in health.report(store):
        flag = "OK  " if entry["healthy"] else "DOWN"
        print(f"  {flag} {entry['watcher']:<24} {str(entry['detail'])[:52]}")

    banner("AUDIT TRAIL (run 2)")
    for r in store2.trail(second.event_id):
        print(f"  {r['seq']:>3}  {r['at'][:19]}  {r['step']:<20} {r['outcome']}")

    store.close(); store2.close()
    return 0


class _Static:
    def __init__(self, records): self._r = records
    def projects(self): return list(self._r)


if __name__ == "__main__":
    raise SystemExit(main())
