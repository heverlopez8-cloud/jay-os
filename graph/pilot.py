"""Phase 12: a controlled pilot before anything touches the vault in bulk.

WHY A PILOT AND NOT A FULL RUN
------------------------------
451 projects is a lot of rope. A bulk pass that merges two people or floods
notes with links is discovered after the damage, not during. So the pilot runs
one representative slice into a THROWAWAY database, asserts a list of
properties, and reports BEFORE/AFTER. Only a passing pilot justifies a wider
rollout -- and even then `backlinks` stays dry-run until Jay passes `--write`.

WHERE THE PILOT DATA COMES FROM
-------------------------------
The project, property, permit and documents are REAL -- read live from the
synced Notion catalogue and from `permits/B26-2089/`.

The review comment, decision, outcome, task and meeting are a FIXTURE. They
encode the actual B26-2089 deficiency notice of 2026-09-09 (Kimberly Pippin,
Queen Creek: "upload a narrative describing the exact scope of work") and what
followed, because the Gmail and review-comment connectors are not live in v1.
They are labelled `pilot_fixture` in every source_ref so nothing downstream can
mistake them for harvested data.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Iterator

from . import health as health_module
from . import lessons as lessons_module
from .adapters.base import SourceRecord
from .adapters.permit_folders import PermitFolders
from .adapters.projects_json import ProjectsJson
from .contract import EntityType as E, LessonStatus
from .pages import inject_backlinks, write_pages
from .pipeline import GraphPipeline
from .store import GraphStore

PILOT_ADDRESS = "18882 E VALLEJO ST"
PILOT_PERMIT = "B26-2089"
_STAMP = "2026-09-09T14:00:00+00:00"


class OneProject:
    """The real catalogue, narrowed to the pilot's project."""

    name = "projects_json"

    def __init__(self, needle: str = PILOT_ADDRESS) -> None:
        self.needle = needle.upper()

    def records(self) -> Iterator[SourceRecord]:
        for record in ProjectsJson().records():
            haystack = f"{record.payload.get('address','')} {record.payload.get('name','')}"
            if self.needle in haystack.upper():
                yield record


class PilotFixture:
    """The human workflow around B26-2089, as a future connector would supply it."""

    name = "pilot_fixture"

    def records(self) -> Iterator[SourceRecord]:
        yield SourceRecord(
            kind="review_comment", source=self.name,
            source_ref="pilot_fixture#queencreek-deficiency-2026-09-09",
            observed_at=_STAMP,
            payload={
                "permit_number": PILOT_PERMIT,
                "comment_id": "RC-B26-2089-001",
                "summary": "Queen Creek: submittal deficient, narrative required",
                "text": ("Please upload a narrative in pdf format describing the "
                         "exact scope of work for this permit application; it is "
                         "unclear from the plans uploaded what this panel upgrade "
                         "is for and what it is going to serve."),
                "reviewer_name": "Kimberly Pippin",
                "reviewer_email": "Kimberly.Pippin@queencreekaz.gov",
                "reviewer_role": "Permit Technician Supervisor",
                "jurisdiction": "Queen Creek",
                "received_at": _STAMP,
            })
        yield SourceRecord(
            kind="decision", source=self.name,
            source_ref="pilot_fixture#decision-dedicated-service",
            observed_at=_STAMP,
            payload={
                "decision_id": "DEC-B26-2089-001",
                "summary": ("Dedicated 200A metered service instead of extending "
                            "the existing panel"),
                "rationale": ("Extending the existing panel was not cost-efficient: "
                              "concrete demolition, new conduit and underground "
                              "routing would be required."),
                "decided_by": "Hever V. Lopez",
                "prompted_by_comment": "RC-B26-2089-001",
            })
        yield SourceRecord(
            kind="outcome", source=self.name,
            source_ref="pilot_fixture#outcome-narrative-uploaded",
            observed_at=_STAMP,
            payload={
                "outcome_id": "OUT-B26-2089-001",
                "summary": "Scope of work narrative produced and uploaded",
                "result": "narrative_delivered",
                "from_decision": "DEC-B26-2089-001",
            })
        yield SourceRecord(
            kind="task", source=self.name,
            source_ref="pilot_fixture#task-revised-sheets",
            observed_at=_STAMP,
            payload={
                "task_id": "TASK-B26-2089-001",
                "summary": ("Produce revised plan sheets showing the dedicated "
                            "200A metered service"),
                "status": "open",
                "assignee": "Hever V. Lopez",
                "blocked_by_comment": "RC-B26-2089-001",
            })
        yield SourceRecord(
            kind="meeting", source=self.name,
            source_ref="pilot_fixture#site-examination",
            observed_at=_STAMP,
            payload={
                "meeting_id": "MTG-B26-2089-001",
                "title": "Site examination of existing 400A panel capacity",
                "attendees": "Hever V. Lopez",
                "produced_decision": "DEC-B26-2089-001",
            })


REQUIRED_TYPES = (E.PROJECT, E.PROPERTY, E.JURISDICTION, E.PERMIT, E.DOCUMENT,
                  E.PERSON, E.REVIEW_COMMENT, E.DECISION, E.OUTCOME, E.TASK,
                  E.MEETING)


def run_pilot(out_dir: Path) -> int:
    """Run the pilot. Returns 0 when every assertion holds, 1 otherwise."""
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = out_dir / "pilot-graph.sqlite3"
    if db_path.exists():
        db_path.unlink()  # a pilot always starts from nothing, by definition
    store = GraphStore(db_path)
    pipeline = GraphPipeline(store, actor="pilot")

    print("=" * 70)
    print("JAY-OS GRAPH ENGINE — PHASE 12 CONTROLLED PILOT")
    print("=" * 70)
    before = health_module.report(store)
    print(f"\nBEFORE  entities={before.total_entities} "
          f"relationships={before.total_relationships} "
          f"isolated_rate={before.isolated_node_rate:.1%}")

    print("\nINGEST")
    reports = [pipeline.ingest(OneProject()),
               pipeline.ingest(PermitFolders()),
               pipeline.ingest(PilotFixture())]
    for report in reports:
        print(f"  {report.source:16} records={report.records:<4} "
              f"entities+{report.entities_created:<3} resolved={report.entities_resolved:<3} "
              f"edges+{report.relationships_created:<3} refusals={len(report.refusals)}")
        for refusal in report.refusals:
            print(f"      REFUSED: {refusal}")

    print("\nENTITIES DETECTED")
    for row in store.entities():
        print(f"  {row['entity_type']:<15} {row['entity_id']:<46} {row['canonical_name'][:44]}")

    print("\nRELATIONSHIPS CREATED")
    for edge in store.all_relationships():
        print(f"  {edge['source_entity']:<44} --{edge['relationship_type']}--> "
              f"{edge['target_entity']}")
        print(f"      [{edge['disposition']}] confidence={edge['confidence']} "
              f"via {edge['extraction_method']}")

    print("\nDUPLICATES AVOIDED")
    resolved = sum(r.entities_resolved for r in reports)
    print(f"  {resolved} record(s) resolved onto an existing entity instead of "
          f"creating a duplicate")
    merges = store.candidate_merges()
    print(f"  {len(merges)} candidate merge(s) filed for Jay (never auto-merged)")
    for merge in merges:
        print(f"      {merge['left_entity']} / {merge['right_entity']} "
              f"confidence={merge['confidence']:.2f} — {merge['basis']}")

    print("\nCANDIDATE LESSONS")
    lesson_report = lessons_module.generate(store, actor="pilot")
    for row in store.lessons():
        print(f"  [{row['status']}] {row['lesson_id']} confidence={row['confidence']}")
        print(f"      {row['observation']}")
        print(f"      rule: {row['proposed_rule']}")
    if not store.lessons():
        print("  none (pilot slice is too small for a repeated pattern — expected)")

    print("\nPAGES + BACKLINKS")
    pages = write_pages(store, out_dir / "pages")
    print(f"  canonical pages written={pages.written} unchanged={pages.unchanged}")
    vault = out_dir / "fixture-vault"
    vault.mkdir(parents=True, exist_ok=True)
    project_rows = store.entities(E.PROJECT)
    note_body = ("# Vallejo — my notes\n\nHandwritten content that must survive "
                 "the backlink pass.\n")
    note = vault / "vallejo-note.md"
    if project_rows:
        note.write_text(f"---\nentity_id: {project_rows[0]['entity_id']}\n"
                        f"tags: [permits]\n---\n\n{note_body}", encoding="utf-8")
    original = note.read_text(encoding="utf-8") if note.exists() else ""
    dry = inject_backlinks(store, vault, dry_run=True)
    untouched = note.read_text(encoding="utf-8") == original if note.exists() else True
    print(f"  backlinks DRY RUN: would update {dry.written} note(s); "
          f"on-disk unchanged={untouched}")

    after = health_module.report(store, vault)
    print("\n" + health_module.render(after))

    print("\nGRAPH HEALTH CHANGE")
    print(f"  entities        {before.total_entities} -> {after.total_entities}")
    print(f"  relationships   {before.total_relationships} -> {after.total_relationships}")
    print(f"  isolated rate   {before.isolated_node_rate:.1%} -> "
          f"{after.isolated_node_rate:.1%}")

    print("\nVALIDATION")
    checks: list[tuple[str, bool, str]] = []
    checks.append(("no records refused",
                   all(not r.refusals for r in reports),
                   f"{sum(len(r.refusals) for r in reports)} refusals"))
    present = {row["entity_type"] for row in store.entities()}
    missing = [t.value for t in REQUIRED_TYPES if t.value not in present]
    checks.append(("all required entity types present", not missing,
                   f"missing {missing}"))
    checks.append(("every edge carries full provenance",
                   all(e["source_reference"].strip() and e["extraction_method"].strip()
                       and e["confidence"] is not None and e["observed_at"].strip()
                       for e in store.all_relationships()), "an edge lacked provenance"))
    checks.append(("no lesson promoted to ACTIVE",
                   len(store.lessons(LessonStatus.ACTIVE)) == 0, "a lesson was promoted"))
    checks.append(("no broken links", after.broken_links == 0,
                   f"{after.broken_links} broken"))
    checks.append(("isolated node rate is 0%", after.isolated_node_rate == 0.0,
                   f"{after.isolated_node_rate:.1%} isolated"))
    checks.append(("backlink dry run modified nothing", untouched,
                   "a note changed during a dry run"))
    rerun = [GraphPipeline(store, actor="pilot").ingest(s)
             for s in (OneProject(), PermitFolders(), PilotFixture())]
    checks.append(("rerun is idempotent",
                   all(r.relationships_created == 0 and r.entities_created == 0
                       for r in rerun),
                   f"rerun created {sum(r.relationships_created for r in rerun)} edges"))
    events = store.events("graph_ingest")
    checks.append(("every ingest wrote an event", len(events) >= 3,
                   f"{len(events)} ingest events"))

    ok = True
    for label, passed, detail in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}"
              + ("" if passed else f" — {detail}"))
        ok = ok and passed

    print("\n" + "=" * 70)
    print(f"PILOT {'PASSED' if ok else 'FAILED'}  —  database: {db_path}")
    print("=" * 70)
    store.close()
    return 0 if ok else 1
