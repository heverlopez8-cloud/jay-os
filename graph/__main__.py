"""Operate the graph engine.

    python -m graph ingest  [--projects N] [--folders] [--portal CSV --jurisdiction NAME]
    python -m graph pages   [--out DIR]
    python -m graph backlinks --vault PATH [--write]
    python -m graph lessons
    python -m graph health  [--vault PATH]
    python -m graph show ENTITY_ID
    python -m graph override --proposal TEXT --decision TEXT [--reason TEXT]
    python -m graph pilot   [--out DIR]

`backlinks` is dry-run unless `--write` is passed. Nothing in this CLI can
promote a lesson: that is `store.set_lesson_status` with Jay's approval.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import health as health_module
from . import lessons as lessons_module
from .adapters.permit_folders import PermitFolders
from .adapters.portal_csv import PortalCsv
from .adapters.projects_json import ProjectsJson
from .adapters.sentinel_contacts import SentinelContacts, SentinelMatchedEmails
from .contract import LessonStatus
from .pages import inject_backlinks, render_entity_page, write_pages
from .pipeline import GraphPipeline
from .store import GraphStore

DEFAULT_DB = Path(__file__).resolve().parent / "jayos-graph.sqlite3"
DEFAULT_PAGES = Path(__file__).resolve().parent / "pages"


def _store(args) -> GraphStore:
    return GraphStore(args.db)


def cmd_ingest(args) -> int:
    store = _store(args)
    pipeline = GraphPipeline(store)
    reports = []
    if args.projects is not None:
        reports.append(pipeline.ingest(ProjectsJson(limit=args.projects or None)))
    if args.folders:
        reports.append(pipeline.ingest(PermitFolders()))
    if args.portal:
        reports.append(pipeline.ingest(PortalCsv(args.portal, args.jurisdiction or "")))
    if args.contacts:
        reports.append(pipeline.ingest(SentinelContacts()))
    if args.matched_emails:
        reports.append(pipeline.ingest(SentinelMatchedEmails()))
    if not reports:
        print("nothing selected. pass --projects N, --folders and/or --portal CSV",
              file=sys.stderr)
        return 2
    for report in reports:
        print(json.dumps(report.as_dict(), indent=2))
        for refusal in report.refusals[:10]:
            print(f"  REFUSED {refusal}", file=sys.stderr)
    store.close()
    return 0


def cmd_pages(args) -> int:
    store = _store(args)
    report = write_pages(store, args.out)
    print(f"pages written={report.written} unchanged={report.unchanged} -> {args.out}")
    store.close()
    return 0


def cmd_backlinks(args) -> int:
    store = _store(args)
    try:
        report = inject_backlinks(store, args.vault, dry_run=not args.write)
    except FileNotFoundError as error:
        print(f"STOP: {error}", file=sys.stderr)
        store.close()
        return 2
    mode = "WROTE" if args.write else "DRY RUN (no files changed)"
    print(f"{mode}: notes updated={report.written} unchanged={report.unchanged} "
          f"skipped={report.skipped}")
    for path in report.paths[:20]:
        print(f"  {path}")
    store.close()
    return 0


def cmd_lessons(args) -> int:
    store = _store(args)
    report = lessons_module.generate(store)
    print(f"candidate lessons: new={report.proposed} updated={report.updated}")
    for row in store.lessons():
        print(f"\n  [{row['status']}] {row['lesson_id']}  confidence={row['confidence']}")
        print(f"    observation : {row['observation']}")
        print(f"    rule        : {row['proposed_rule']}")
        print(f"    impact      : {row['expected_impact']}")
    active = store.lessons(LessonStatus.ACTIVE)
    print(f"\n  promoted to ACTIVE: {len(active)} "
          f"(promotion requires Jay; the engine cannot do it)")
    store.close()
    return 0


def cmd_health(args) -> int:
    store = _store(args)
    print(health_module.render(health_module.report(store, args.vault)))
    store.close()
    return 0


def cmd_show(args) -> int:
    store = _store(args)
    try:
        print(render_entity_page(store, args.entity_id))
    except KeyError:
        print(f"no such entity: {args.entity_id}", file=sys.stderr)
        store.close()
        return 2
    store.close()
    return 0


def cmd_override(args) -> int:
    store = _store(args)
    override_id = store.record_override(
        agent_proposal=args.proposal, owner_decision=args.decision,
        reason_if_known=args.reason or "", context=args.context or "")
    print(f"recorded override {override_id}. One override is evidence, not a rule.")
    store.close()
    return 0


def cmd_pilot(args) -> int:
    from .pilot import run_pilot
    return run_pilot(Path(args.out))


def cmd_brief(args) -> int:
    from .brief import render
    store = _store(args)
    print(render(store))
    store.close()
    return 0


def cmd_snapshot(args) -> int:
    from . import health as health_module
    store = _store(args)
    report = health_module.report(store)
    snapshot_id = store.take_snapshot(report.isolated_node_rate, label=args.label)
    print(f"snapshot {snapshot_id}: {report.total_entities} entities, "
          f"{report.total_relationships} relationships, "
          f"isolated {report.isolated_node_rate:.1%}")
    store.close()
    return 0


def cmd_graphify_export(args) -> int:
    from .graphify_export import write_export
    store = _store(args)
    path = write_export(store, args.out)
    print(f"wrote {path} ({path.stat().st_size} bytes) -- graphify-shaped, "
          f"NOT written into graphify-out/. Hand this to Jay or graphify's "
          f"own tooling when a real merge is wanted.")
    store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m graph",
                                     description="JAY-OS Graph Integration Engine v1")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest")
    ingest.add_argument("--projects", nargs="?", type=int, const=0, default=None,
                        help="ingest the Notion project catalogue; optional row limit")
    ingest.add_argument("--folders", action="store_true")
    ingest.add_argument("--portal")
    ingest.add_argument("--jurisdiction", default="")
    ingest.add_argument("--contacts", action="store_true",
                        help="ingest known_clients/known_consultants from sentinel.toml")
    ingest.add_argument("--matched-emails", action="store_true", dest="matched_emails",
                        help="ingest production-matched inbound emails from the sentinel ledger")
    ingest.set_defaults(func=cmd_ingest)

    pages = subparsers.add_parser("pages")
    pages.add_argument("--out", default=str(DEFAULT_PAGES))
    pages.set_defaults(func=cmd_pages)

    backlinks = subparsers.add_parser("backlinks")
    backlinks.add_argument("--vault", required=True)
    backlinks.add_argument("--write", action="store_true",
                           help="actually modify notes; omit for a dry run")
    backlinks.set_defaults(func=cmd_backlinks)

    subparsers.add_parser("lessons").set_defaults(func=cmd_lessons)

    healthp = subparsers.add_parser("health")
    healthp.add_argument("--vault", default=None)
    healthp.set_defaults(func=cmd_health)

    show = subparsers.add_parser("show")
    show.add_argument("entity_id")
    show.set_defaults(func=cmd_show)

    override = subparsers.add_parser("override")
    override.add_argument("--proposal", required=True)
    override.add_argument("--decision", required=True)
    override.add_argument("--reason", default="")
    override.add_argument("--context", default="")
    override.set_defaults(func=cmd_override)

    pilot = subparsers.add_parser("pilot")
    pilot.add_argument("--out", default="/tmp/jayos-graph-pilot")
    pilot.set_defaults(func=cmd_pilot)

    subparsers.add_parser("brief").set_defaults(func=cmd_brief)

    snapshot = subparsers.add_parser("snapshot")
    snapshot.add_argument("--label", default="")
    snapshot.set_defaults(func=cmd_snapshot)

    graphify_export = subparsers.add_parser("graphify-export")
    graphify_export.add_argument(
        "--out", default=str(Path(__file__).resolve().parent
                             / "graphify_compatible_export.json"))
    graphify_export.set_defaults(func=cmd_graphify_export)

    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
