"""Operate the pipeline.

    python -m permit_pipeline ingest      --messages inbox.json
    python -m permit_pipeline portal-poll --permits BLDR-00568-2026
    python -m permit_pipeline ack-poll
    python -m permit_pipeline sweep
    python -m permit_pipeline health
    python -m permit_pipeline backfill    --messages history.json
    python -m permit_pipeline status
    python -m permit_pipeline ack EVT-... / complete EVT-...

Every subcommand that touches the world uses the real connectors and
fails closed if a credential is missing. `--dry-run` is the only way to
get simulated writes, and it says so on every line of output.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import backfill as backfill_mod
from . import config, enrich as enrich_mod, factory, health, portal
from . import shadow as shadow_mod
from . import watchers as watchers_mod
from .acknowledge import NotionAcknowledgementWatcher
from .audit import AuditStore
from .model import ProjectRecord
from .notify import CompositeNotifier, SmtpNotifier
from .pipeline import PermitPipeline, run_sla_sweep
from .ports import (
    ConnectorError, HttpNotionDirectory, HttpNotionPort, HttpNotionReader,
    RecordingNotifier, RecordingNotionPort, StaticDirectory,
)
from .routing import PEOPLE, JAY
from .sources import from_gmail_message

PROJECTS_DATABASE = "53b6c76e-76cd-4ea8-b765-014c1e728a9e"
SCOTTSDALE_BASE = "https://cityofscottsdaleaz-energovweb.tylerhost.net"


def _directory(args):
    if args.projects:
        records = json.loads(Path(args.projects).read_text())
        return StaticDirectory([
            ProjectRecord(
                page_id=r["page_id"], name=r["name"],
                permit_numbers=tuple(r.get("permit_numbers", ())),
                address=r.get("address", ""), apn=r.get("apn", ""),
                stage=r.get("stage"), status=tuple(r.get("status", ())),
                jurisdiction=r.get("jurisdiction"),
            ) for r in records
        ])
    return HttpNotionDirectory(
        config.require("NOTION_API_KEY", "reading the project list"),
        args.database,
    )


def _notion(args):
    if args.dry_run:
        return RecordingNotionPort()
    return factory.notion_writer()


def _notifier(args):
    if args.dry_run:
        return RecordingNotifier()
    # `--redirect-notifications` keeps canary and rehearsal traffic away from
    # real employees without pretending the transport is unavailable.
    return CompositeNotifier(SmtpNotifier(redirect_to=args.redirect_notifications))


def _pipeline(args, store):
    return PermitPipeline(store, _directory(args), _notion(args), _notifier(args))


def _scheduler_state() -> dict[str, bool]:
    """Ask Windows what it is actually running, rather than trusting config."""
    import subprocess

    states: dict[str, bool] = {}
    for task in watchers_mod.scheduled_tasks():
        try:
            result = subprocess.run(
                ["schtasks.exe", "/Query", "/TN", task, "/FO", "LIST"],
                capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            if line.strip().lower().startswith("status:"):
                states[task] = "disabled" not in line.lower()
                break
    return states


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="permit_pipeline")
    parser.add_argument("--db", default="permit_events.db")
    parser.add_argument("--database", default=PROJECTS_DATABASE,
                        help="Notion Projects database id")
    parser.add_argument("--projects", help="JSON project list instead of Notion")
    parser.add_argument("--dry-run", action="store_true",
                        help="simulate writes and notifications")
    parser.add_argument("--redirect-notifications", metavar="EMAIL",
                        help="send every notification here instead of the owner")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest")
    ingest.add_argument("--messages", required=True, type=Path)
    ingest.add_argument("--clients", type=Path)

    poll = sub.add_parser("portal-poll")
    poll.add_argument("--permits", required=True, nargs="+")
    poll.add_argument("--jurisdiction", default="City of Scottsdale")
    poll.add_argument("--portal-token", default=None)

    shadow_cmd = sub.add_parser(
        "shadow", help="observe real mail; write nothing, notify nobody")
    shadow_cmd.add_argument("--messages", required=True, type=Path)
    shadow_cmd.add_argument("--clients", type=Path)
    shadow_cmd.add_argument("--shadow-db", default="permit_shadow.db")

    sub.add_parser("watchers", help="reconcile the manifest with the scheduler")

    tri = sub.add_parser("triage",
                         help="which projects actually need a permit number")
    tri.add_argument("--messages", type=Path)

    back = sub.add_parser("backfill")
    back.add_argument("--messages", required=True, type=Path)
    back.add_argument("--clients", type=Path)

    ack = sub.add_parser("ack"); ack.add_argument("event_id")
    done = sub.add_parser("complete"); done.add_argument("event_id")
    health_cmd = sub.add_parser("health")
    health_cmd.add_argument("--no-alert", action="store_true",
                            help="report only; do not alert Jay")
    for name in ("sweep", "status", "ack-poll"):
        sub.add_parser(name)

    args = parser.parse_args(argv)
    if not hasattr(args, "no_alert"):
        args.no_alert = True
    store = AuditStore(args.db)
    if args.dry_run:
        print("[DRY RUN] nothing will be written to Notion or sent.",
              file=sys.stderr)

    try:
        if args.command == "ingest":
            pipeline = _pipeline(args, store)
            clients = json.loads(args.clients.read_text()) if args.clients else []
            messages = json.loads(args.messages.read_text())
            processed = exceptions = skipped = 0
            raws = []
            for message in messages:
                raw = from_gmail_message(message, clients)
                if raw is None:
                    skipped += 1
                    continue
                raws.append(raw)
            # Oldest first. Gmail hands mail back newest-first, so without
            # this the surviving copy of a duplicate pair is the LATER one
            # and `suppressed_by` would point forwards in time.
            raws.sort(key=lambda r: r.received_at)
            for raw in raws:
                outcome = pipeline.process(raw)
                processed += 1
                exceptions += 1 if outcome.failed_closed else 0
                print(f"{'EXCEPTION' if outcome.failed_closed else 'ok':<10} "
                      f"{raw.event_id}  {raw.subject[:58]}")
            health.record(store, "gmail_ingest", True,
                          f"{processed} events, {exceptions} exceptions")
            print(f"\n{processed} processed, {exceptions} exceptions, "
                  f"{skipped} non-operational")
            return 1 if exceptions else 0

        if args.command == "portal-poll":
            client = portal.CivicAccessClient(SCOTTSDALE_BASE, args.portal_token)
            producer = portal.PortalProducer(
                store, client, _pipeline(args, store), args.jurisdiction,
                "portal_poll_scottsdale")
            result = producer.poll(args.permits)
            print(f"transitions: {result['events']}")
            print(f"unchanged:   {result['unchanged']}")
            for error in result["errors"]:
                print(f"ERROR: {error}")
            return 1 if result["errors"] else 0

        if args.command == "ack-poll":
            reader = HttpNotionReader(
                config.require("NOTION_API_KEY", "acknowledgement detection"))
            watcher = NotionAcknowledgementWatcher(
                store, reader,
                {key: person.notion_user_id for key, person in PEOPLE.items()})
            promoted = watcher.poll()
            health.record(store, "ack_poll", True,
                          f"{len(promoted['acknowledged'])} acknowledged, "
                          f"{len(promoted['completed'])} completed")
            print(f"acknowledged: {promoted['acknowledged']}")
            print(f"completed:    {promoted['completed']}")
            return 0

        if args.command == "sweep":
            notifier = _notifier(args)
            active = health.active_watchers()

            # Preflight: prove the transports actually work rather than
            # assuming it. These checks change nothing.
            if not args.dry_run:
                if "notion_write" in active:
                    try:
                        # Deep check: proves the credential reaches the
                        # Projects database and its schema still matches.
                        health.record(store, "notion_write", True,
                                      _notion(args).check(args.database))
                    except ConnectorError as error:
                        health.record(store, "notion_write", False, str(error)[:120])
                if "notification_transport" in active:
                    try:
                        health.record(store, "notification_transport", True,
                                      notifier.check())
                    except ConnectorError as error:
                        health.record(store, "notification_transport", False,
                                      str(error)[:120])

            escalated = run_sla_sweep(store, notifier)
            health.record(store, "sla_sweep", True, f"{len(escalated)} escalated")
            alerted = health.sweep(store, notifier, watchers=active)
            for event_id in escalated:
                print(f"escalated {event_id}")
            print(f"{len(escalated)} escalated, "
                  f"{len(alerted)} watcher alerts sent")
            return 0

        if args.command == "health":
            active = health.active_watchers()
            # Stamp our own pulse first: a monitor that cannot prove it ran
            # is indistinguishable from one that never did.
            health.record(store, "health_self", True, "health check ran")
            entries = health.report(store, watchers=active)
            worst = 0
            for entry in entries:
                flag = "OK  " if entry["healthy"] else "DOWN"
                worst = max(worst, 0 if entry["healthy"] else 1)
                print(f"{flag} {entry['watcher']:<24} "
                      f"last_ok={str(entry['last_ok_at'])[:19]:<19} "
                      f"{str(entry['detail'])[:44]}")
            # This runs as its own scheduled task, independently of `sweep`,
            # so that a dead sweep is still reported by something alive.
            if not args.no_alert and not args.dry_run:
                alerted = health.sweep(store, _notifier(args), watchers=active)
                for watcher in alerted:
                    print(f"ALERTED Jay: {watcher} is down")
            return worst

        if args.command == "backfill":
            projects = _directory(args).projects()
            clients = json.loads(args.clients.read_text()) if args.clients else []
            messages = json.loads(args.messages.read_text())
            print(backfill_mod.render(
                backfill_mod.run(messages, projects, clients)))
            return 0

        if args.command == "shadow":
            projects = _directory(args).projects()
            clients = json.loads(args.clients.read_text()) if args.clients else []
            messages = json.loads(args.messages.read_text())
            shadow_store = shadow_mod.ShadowStore(args.shadow_db)
            try:
                summary = shadow_mod.observe(
                    messages, projects, shadow_store, clients)
                print(f"{summary['seen']} messages, {summary['new']} new, "
                      f"{summary['duplicate']} already seen, "
                      f"{summary['non_operational']} non-operational\n")
                print(shadow_mod.render(shadow_store))
            finally:
                shadow_store.close()
            return 0

        if args.command == "triage":
            projects = _directory(args).projects()
            messages = (json.loads(args.messages.read_text())
                        if args.messages else [])
            print(enrich_mod.render(enrich_mod.triage(projects, messages)))
            return 0

        if args.command == "watchers":
            states = _scheduler_state()
            print("MANIFEST")
            for watcher in watchers_mod.MANIFEST:
                task = watcher.scheduled_task or "-"
                print(f"  {watcher.name:<24} task={task:<22} "
                      f"enabled={watcher.enabled}")
            print(f"\nSCHEDULER: {states if states else 'no JAY-OS Permit tasks found'}")
            findings = watchers_mod.reconcile(states)
            print("\nDRIFT")
            if not findings:
                print("  none - the manifest matches the scheduler")
                return 0
            for finding in findings:
                print(f"  ! {finding}")
            return 1

        if args.command == "status":
            open_exceptions = store.open_exceptions()
            print(f"OPEN EXCEPTIONS ({len(open_exceptions)})")
            for row in open_exceptions:
                print(f"  {row['created_at'][:19]}  {row['code']:<26} "
                      f"{row['detail'][:74]}")
            print(f"\nOPEN WORK ({len(store.open_events())})")
            for row in store.open_events():
                print(f"  {row['state']:<14} {str(row['owner_key']):<11} "
                      f"due={str(row['due_at'])[:16]} {str(row['project_name'])[:40]}")
            # Without this the withheld sends sit on no active surface at all:
            # open_events() whitelists notified/acknowledged/escalated, and
            # both escalation queries require a non-NULL clock. "Countable by
            # someone who already knows to run the query" is not visible.
            suppressed = store.suppressed_recent(24)
            print(f"\nSUPPRESSED AS DUPLICATE (24h): {len(suppressed)}")
            for row in suppressed:
                print(f"  {row['recorded_at'][:19]}  "
                      f"{str(row['project_name'])[:32]:<32} "
                      f"{row['event_class']:<20} -> {row['recipient']}  "
                      f"(copy of {row['suppressed_by']})")
            return 0

        if args.command == "ack":
            store.acknowledge(args.event_id, how="cli")
            print(f"acknowledged {args.event_id}")
            return 0

        if args.command == "complete":
            store.complete(args.event_id, how="cli")
            print(f"completed {args.event_id}")
            return 0
    except ConnectorError as error:
        print(f"FAIL CLOSED: {error}", file=sys.stderr)
        return 2
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
