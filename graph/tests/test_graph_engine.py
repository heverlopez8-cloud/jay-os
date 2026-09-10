"""Tests for the Graph Integration Engine.

Written with `unittest` rather than pytest on purpose: the production image
carries no test dependencies, so these must be runnable with nothing but the
standard library. They also collect cleanly under pytest alongside the existing
291 sentinel tests.

Each test names the failure it prevents. A test whose purpose nobody remembers
gets deleted the first time it is inconvenient.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from graph import health as health_module
from graph import lessons as lessons_module
from graph.adapters.base import PendingConnector, SourceRecord
from graph.adapters.permit_folders import PermitFolders
from graph.adapters.portal_csv import PortalCsv
from graph.adapters.projects_json import ProjectsJson
from graph.contract import (Disposition, EntityStatus, EntityType as E,
                            LessonStatus, RelationType as R, VocabularyError,
                            actionable, disposition_for, validate_relationship)
from graph.ids import entity_id
from graph.pages import inject_backlinks, render_entity_page, write_pages
from graph.pipeline import GraphPipeline
from graph.places import jurisdiction_from_address
from graph.resolve import (FUZZY_CEILING, fuzzy_floor_for, resolve,
                           resolve_or_create)
from graph.store import (GovernanceError, GraphStore, ProvenanceError,
                         StoreError)
from graph._reuse import gates


def _store() -> GraphStore:
    return GraphStore(":memory:")


def _entity(store, etype=E.PROJECT, name="Test Project", **kw):
    kw.setdefault("source", "test")
    kw.setdefault("source_ref", "test#1")
    return store.upsert_entity(etype, name, **kw)


class TestEntityCreation(unittest.TestCase):
    """An entity must exist, carry provenance, and be findable."""

    def test_entity_is_created_with_required_fields(self):
        store = _store()
        eid = _entity(store, E.PROJECT, "18882 E Vallejo St")
        row = store.entity(eid)
        self.assertIsNotNone(row)
        for field in ("entity_id", "entity_type", "canonical_name", "created_at",
                      "updated_at", "confidence", "status"):
            self.assertIsNotNone(row[field], f"{field} must be populated")
        self.assertEqual(row["entity_type"], "PROJECT")

    def test_source_refs_are_recorded(self):
        store = _store()
        eid = _entity(store, source="projects_json", source_ref="projects.json#abc")
        refs = store.source_refs(eid)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["source"], "projects_json")

    def test_existing_jayos_id_is_reused_not_shadowed(self):
        store = _store()
        eid = _entity(store, E.PROJECT, "Vallejo", external_id="29216cf6-notion")
        self.assertIn("29216CF6-NOTION", eid)

    def test_unknown_entity_type_is_refused(self):
        from graph.contract import parse_entity_type
        with self.assertRaises(VocabularyError):
            parse_entity_type("PROJECT_V2")


class TestDeduplication(unittest.TestCase):
    """The same real thing must never become two entities."""

    def test_same_key_resolves_to_same_entity(self):
        store = _store()
        first = _entity(store, E.PERMIT, "B26-2089")
        second = _entity(store, E.PERMIT, "b26-2089  ")
        self.assertEqual(first, second)
        self.assertEqual(len(store.entities(E.PERMIT)), 1)

    def test_ids_are_deterministic_across_runs(self):
        self.assertEqual(entity_id(E.PERMIT, "B26-2089"),
                         entity_id(E.PERMIT, " b26-2089 "))

    def test_property_dedupes_on_parcel_number_across_sources(self):
        """A Notion project and a portal row at one parcel = ONE property."""
        store = _store()
        pipeline = GraphPipeline(store)
        pipeline._handle_project(SourceRecord(
            kind="project", source="projects_json", source_ref="p#1",
            observed_at="2026-09-10T00:00:00+00:00",
            payload={"project_id": "p1", "name": "Test", "apn": "314-14-919",
                     "address": "18882 E VALLEJO ST QUEEN CREEK 85142",
                     "permit_numbers": [], "client_emails": [],
                     "notion_page_id": "n1", "stage": "", "drive_url": ""}))
        pipeline._handle_portal_permit(SourceRecord(
            kind="portal_permit", source="portal_csv", source_ref="c#1",
            observed_at="2026-09-10T00:00:00+00:00",
            payload={"permit_number": "X-1", "apn": "314-14-919",
                     "number": "18882", "street": "E VALLEJO ST",
                     "city": "Queen Creek", "contractor": "", "owner_name": "",
                     "status": ""}))
        self.assertEqual(len(store.entities(E.PROPERTY)), 1,
                         "one parcel must be one property entity")

    def test_attribute_merge_never_blanks_a_known_field(self):
        store = _store()
        eid = _entity(store, E.PROJECT, "P", attributes={"stage": "Intake"})
        _entity(store, E.PROJECT, "P", attributes={"stage": "", "drive_url": "u"})
        attrs = json.loads(store.entity(eid)["attributes"])
        self.assertEqual(attrs["stage"], "Intake", "a later blank must not erase")
        self.assertEqual(attrs["drive_url"], "u")


class TestAliases(unittest.TestCase):
    def test_alias_is_recorded_and_resolves(self):
        store = _store()
        eid = _entity(store, E.COMPANY, "Professional CAD Design LLC")
        store.add_alias(eid, "PCD", "test")
        self.assertIn("PCD", store.aliases(eid))
        self.assertEqual(store.find_by_alias(E.COMPANY, "pcd")["entity_id"], eid)

    def test_alias_is_not_duplicated_on_rerun(self):
        store = _store()
        eid = _entity(store, E.COMPANY, "PCD LLC")
        self.assertTrue(store.add_alias(eid, "PCD", "test"))
        self.assertFalse(store.add_alias(eid, "PCD", "test"))
        self.assertEqual(len(store.aliases(eid)), 1)


class TestTypedRelationships(unittest.TestCase):
    """Typed, directional, and domain-constrained."""

    def test_legal_edge_is_created(self):
        store = _store()
        permit = _entity(store, E.PERMIT, "B26-2089")
        jurisdiction = _entity(store, E.JURISDICTION, "Queen Creek")
        _rel, disposition, created = store.relate(
            permit, R.SUBMITTED_TO, jurisdiction, source_reference="notice",
            confidence=1.0, extraction_method="asserted:test", asserted=True)
        self.assertTrue(created)
        self.assertIs(disposition, Disposition.ASSERTED)

    def test_illegal_endpoint_types_are_refused(self):
        store = _store()
        permit = _entity(store, E.PERMIT, "B26-2089")
        person = _entity(store, E.PERSON, "Kimberly Pippin")
        with self.assertRaises(VocabularyError):
            store.relate(permit, R.SUBMITTED_TO, person, source_reference="r",
                         confidence=1.0, extraction_method="test")

    def test_every_predicate_declares_a_domain(self):
        from graph.contract import RELATION_DOMAINS
        for predicate in R:
            self.assertIn(predicate, RELATION_DOMAINS,
                          f"{predicate.value} has no declared endpoints")
            self.assertTrue(RELATION_DOMAINS[predicate])

    def test_direction_is_meaningful(self):
        """PART_OF is not symmetric; the reverse must be refused."""
        store = _store()
        permit = _entity(store, E.PERMIT, "B26-2089")
        project = _entity(store, E.PROJECT, "Vallejo")
        store.relate(permit, R.PART_OF, project, source_reference="r",
                     confidence=1.0, extraction_method="t", asserted=True)
        with self.assertRaises(VocabularyError):
            store.relate(project, R.PART_OF, permit, source_reference="r",
                         confidence=1.0, extraction_method="t")

    def test_edge_against_unknown_entity_is_refused(self):
        store = _store()
        permit = _entity(store, E.PERMIT, "B26-2089")
        with self.assertRaises(StoreError):
            store.relate(permit, R.SUBMITTED_TO, "JURISDICTION-NOWHERE",
                         source_reference="r", confidence=1.0,
                         extraction_method="t")


class TestProvenance(unittest.TestCase):
    """An edge nobody can trace is not evidence."""

    def setUp(self):
        self.store = _store()
        self.subject = _entity(self.store, E.PERMIT, "B26-2089")
        self.obj = _entity(self.store, E.JURISDICTION, "Queen Creek")

    def test_missing_source_reference_is_refused(self):
        with self.assertRaises(ProvenanceError):
            self.store.relate(self.subject, R.SUBMITTED_TO, self.obj,
                              source_reference="  ", confidence=1.0,
                              extraction_method="t")

    def test_missing_extraction_method_is_refused(self):
        with self.assertRaises(ProvenanceError):
            self.store.relate(self.subject, R.SUBMITTED_TO, self.obj,
                              source_reference="r", confidence=1.0,
                              extraction_method="")

    def test_out_of_range_confidence_is_refused(self):
        for bad in (None, -0.1, 1.5):
            with self.assertRaises(ProvenanceError):
                self.store.relate(self.subject, R.SUBMITTED_TO, self.obj,
                                  source_reference="r", confidence=bad,
                                  extraction_method="t")

    def test_all_four_provenance_fields_are_stored(self):
        self.store.relate(self.subject, R.SUBMITTED_TO, self.obj,
                          source_reference="notice.pdf", confidence=0.93,
                          extraction_method="inferred:city_token",
                          observed_at="2026-09-09T00:00:00+00:00")
        edge = self.store.all_relationships()[0]
        self.assertEqual(edge["source_reference"], "notice.pdf")
        self.assertEqual(edge["extraction_method"], "inferred:city_token")
        self.assertEqual(edge["confidence"], 0.93)
        self.assertEqual(edge["observed_at"], "2026-09-09T00:00:00+00:00")
        self.assertTrue(json.loads(edge["provenance"]))

    def test_every_edge_from_a_real_ingest_has_provenance(self):
        store = _store()
        GraphPipeline(store).ingest(ProjectsJson(limit=10))
        for edge in store.all_relationships():
            self.assertTrue(edge["source_reference"].strip())
            self.assertTrue(edge["extraction_method"].strip())
            self.assertIsNotNone(edge["confidence"])
            self.assertTrue(edge["observed_at"].strip())


class TestConfidenceGates(unittest.TestCase):
    """The engine must use the PRODUCTION thresholds, not its own."""

    def test_gates_come_from_the_sentinel_contract(self):
        auto_link, propose, _ = gates()
        self.assertEqual(auto_link, 0.95)
        self.assertEqual(propose, 0.75)

    def test_dispositions_follow_the_gates(self):
        self.assertIs(disposition_for(1.0, asserted=True), Disposition.ASSERTED)
        self.assertIs(disposition_for(0.96), Disposition.LINKED)
        self.assertIs(disposition_for(0.80), Disposition.PROPOSED)
        self.assertIs(disposition_for(0.50), Disposition.WEAK)

    def test_only_strong_edges_are_actionable(self):
        self.assertTrue(actionable(Disposition.ASSERTED))
        self.assertTrue(actionable(Disposition.LINKED))
        self.assertFalse(actionable(Disposition.PROPOSED))
        self.assertFalse(actionable(Disposition.WEAK))

    def test_inference_never_reaches_asserted_confidence(self):
        """Inferred municipal edges stay below the auto-link gate."""
        from graph.places import MUNICIPAL_CONFIDENCE
        self.assertLess(MUNICIPAL_CONFIDENCE, gates()[0])


class TestIdempotency(unittest.TestCase):
    """Running twice must change nothing."""

    def test_duplicate_edge_is_not_created_twice(self):
        store = _store()
        a = _entity(store, E.PERMIT, "B26-2089")
        b = _entity(store, E.JURISDICTION, "Queen Creek")
        first, _d1, created1 = store.relate(a, R.SUBMITTED_TO, b,
                                           source_reference="r", confidence=1.0,
                                           extraction_method="t", asserted=True)
        second, _d2, created2 = store.relate(a, R.SUBMITTED_TO, b,
                                            source_reference="r", confidence=1.0,
                                            extraction_method="t", asserted=True)
        self.assertEqual(first, second)
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(store.count("relationships"), 1)

    def test_full_ingest_rerun_adds_nothing(self):
        store = _store()
        pipeline = GraphPipeline(store)
        first = pipeline.ingest(ProjectsJson(limit=15))
        entities, edges = store.count("entities"), store.count("relationships")
        second = pipeline.ingest(ProjectsJson(limit=15))
        self.assertEqual(store.count("entities"), entities)
        self.assertEqual(store.count("relationships"), edges)
        self.assertEqual(second.relationships_created, 0)
        self.assertEqual(second.entities_created, 0)
        self.assertGreater(first.relationships_created, 0)

    def test_pages_are_not_rewritten_when_unchanged(self):
        store = _store()
        GraphPipeline(store).ingest(ProjectsJson(limit=5))
        with tempfile.TemporaryDirectory() as out:
            first = write_pages(store, out)
            second = write_pages(store, out)
            self.assertGreater(first.written, 0)
            self.assertEqual(second.written, 0)
            self.assertEqual(second.unchanged, first.written)


class TestCandidateMerges(unittest.TestCase):
    """When unsure, split and ask. Never merge."""

    def test_near_miss_names_are_proposed_not_merged(self):
        store = _store()
        bob, _ = resolve_or_create(store, E.PERSON, "Bob Smith",
                                   source="t", source_ref="r1")
        robert, decision = resolve_or_create(store, E.PERSON, "Robert Smith",
                                             source="t", source_ref="r2")
        self.assertNotEqual(bob, robert, "must NOT merge automatically")
        self.assertEqual(len(store.candidate_merges()), 1)
        merge = store.candidate_merges()[0]
        self.assertLess(merge["confidence"], gates()[0])
        self.assertEqual(store.entity(robert)["status"],
                         EntityStatus.MERGE_CANDIDATE.value)

    def test_fuzzy_confidence_can_never_reach_the_auto_link_gate(self):
        self.assertLess(FUZZY_CEILING, gates()[0])

    def test_structured_types_use_a_stricter_floor_than_names(self):
        """Phase 1 duplicates: 111 candidates from one floor was unusable."""
        self.assertLess(fuzzy_floor_for(E.PERSON), fuzzy_floor_for(E.PROJECT))

    def test_email_identity_beats_name_similarity(self):
        store = _store()
        first, _ = resolve_or_create(store, E.PERSON, "Bob Smith", source="t",
                                     source_ref="r1", email="bob@x.com")
        second, decision = resolve_or_create(store, E.PERSON, "Bobby Smith",
                                             source="t", source_ref="r2",
                                             email="bob@x.com")
        self.assertEqual(first, second)
        self.assertIn("email", decision.basis)

    def test_merge_candidates_are_not_duplicated(self):
        store = _store()
        a = _entity(store, E.PERSON, "A Person")
        b = _entity(store, E.PERSON, "B Person")
        self.assertTrue(store.add_candidate_merge(a, b, 0.8, "test"))
        self.assertFalse(store.add_candidate_merge(b, a, 0.8, "test"))


class TestEventLedger(unittest.TestCase):
    def test_event_is_created_with_all_fields(self):
        store = _store()
        eid = store.record_event(
            event_type="reviewer_requested_clarification", source="notice.pdf",
            actor="graph-engine", action="classify",
            decision="classify_as_attached_casita", result="accepted",
            confidence=0.9, entity_ids=["PROJECT-1"],
            provenance={"page": 2})
        self.assertTrue(eid.startswith("EVT-"))
        row = store.events()[0]
        self.assertEqual(row["decision"], "classify_as_attached_casita")
        self.assertEqual(row["result"], "accepted")
        self.assertEqual(json.loads(row["entity_ids"]), ["PROJECT-1"])

    def test_event_ids_are_unique_within_a_day(self):
        store = _store()
        ids = {store.record_event(event_type="t", source="s", actor="a")
               for _ in range(25)}
        self.assertEqual(len(ids), 25)

    def test_ingest_writes_an_event(self):
        store = _store()
        report = GraphPipeline(store).ingest(ProjectsJson(limit=3))
        self.assertTrue(report.event_id)
        self.assertEqual(len(store.events("graph_ingest")), 1)


class TestLessonEngine(unittest.TestCase):
    """May discover. May not promote."""

    def test_lessons_are_created_as_candidates(self):
        store = _store()
        lid = store.propose_lesson(observation="o", pattern="p",
                                   proposed_rule="r", confidence=0.8)
        self.assertEqual(store.lessons()[0]["status"],
                         LessonStatus.CANDIDATE.value)
        self.assertTrue(lid.startswith("LESSON-"))

    def test_engine_cannot_write_an_active_lesson(self):
        store = _store()
        with self.assertRaises(GovernanceError):
            store.propose_lesson(observation="o", pattern="p", proposed_rule="r",
                                 confidence=0.99, status=LessonStatus.ACTIVE)

    def test_promotion_without_owner_approval_is_refused(self):
        store = _store()
        lid = store.propose_lesson(observation="o", pattern="p",
                                   proposed_rule="r", confidence=0.9)
        for status in (LessonStatus.VALIDATING, LessonStatus.APPROVED,
                       LessonStatus.ACTIVE):
            with self.assertRaises(GovernanceError):
                store.set_lesson_status(lid, status, actor="graph-engine")

    def test_owner_approval_is_recorded_in_history(self):
        store = _store()
        lid = store.propose_lesson(observation="o", pattern="p",
                                   proposed_rule="r", confidence=0.9)
        store.set_lesson_status(lid, LessonStatus.APPROVED, actor="jay",
                                owner_approval="Jay 2026-09-10")
        history = store.execute(
            "SELECT * FROM lesson_status_history WHERE lesson_id = ? "
            "ORDER BY rowid", (lid,))
        self.assertEqual(history[-1]["to_status"], "APPROVED")
        self.assertEqual(history[-1]["approval"], "Jay 2026-09-10")

    def test_generated_lessons_are_never_active(self):
        store = _store()
        GraphPipeline(store).ingest(ProjectsJson(limit=30))
        lessons_module.generate(store)
        self.assertEqual(len(store.lessons(LessonStatus.ACTIVE)), 0)
        for row in store.lessons():
            self.assertEqual(row["status"], LessonStatus.CANDIDATE.value)

    def test_one_observation_is_not_enough_for_a_lesson(self):
        store = _store()
        store.record_override(agent_proposal="use formal tone here",
                              owner_decision="use casual tone instead")
        findings = lessons_module.mine_owner_overrides(store)
        self.assertEqual(findings, [], "a single override is an anecdote")

    def test_repeated_override_becomes_a_candidate(self):
        store = _store()
        for _ in range(2):
            store.record_override(
                agent_proposal="write formal professional corporate email",
                owner_decision="write casual polite friendly email")
        findings = lessons_module.mine_owner_overrides(store)
        self.assertEqual(len(findings), 1)
        self.assertLess(findings[0]["confidence"], 1.0)

    def test_lesson_regeneration_is_idempotent(self):
        store = _store()
        GraphPipeline(store).ingest(ProjectsJson(limit=30))
        lessons_module.generate(store)
        count = store.count("lessons")
        lessons_module.generate(store)
        self.assertEqual(store.count("lessons"), count)


class TestOwnerOverrides(unittest.TestCase):
    def test_override_is_recorded_with_context(self):
        store = _store()
        store.record_override(agent_proposal="X", owner_decision="Y",
                              reason_if_known="because", context="email draft",
                              result="accepted")
        row = store.overrides()[0]
        self.assertEqual(row["agent_proposal"], "X")
        self.assertEqual(row["owner_decision"], "Y")
        self.assertEqual(row["reason_if_known"], "because")
        self.assertTrue(row["timestamp"])


class TestMalformedAndMissingInput(unittest.TestCase):
    """Bad input must be refused or survived, never silently accepted."""

    def test_record_without_source_ref_is_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            SourceRecord(kind="project", source="s", source_ref="  ",
                         observed_at="2026-01-01T00:00:00+00:00", payload={})

    def test_one_malformed_record_does_not_abort_the_ingest(self):
        class Mixed:
            name = "mixed"

            def records(self):
                yield SourceRecord(kind="project", source="mixed", source_ref="bad",
                                   observed_at="2026-01-01T00:00:00+00:00",
                                   payload={})  # no project_id -> raises
                yield SourceRecord(kind="project", source="mixed", source_ref="good",
                                   observed_at="2026-01-01T00:00:00+00:00",
                                   payload={"project_id": "ok", "name": "Good",
                                            "address": "1 A ST MESA 85201",
                                            "apn": "", "permit_numbers": [],
                                            "client_emails": [],
                                            "notion_page_id": "", "stage": "",
                                            "drive_url": ""})

        store = _store()
        report = GraphPipeline(store).ingest(Mixed())
        self.assertEqual(len(report.refusals), 1)
        self.assertGreaterEqual(report.entities_created, 1,
                                "the good record must still land")

    def test_unknown_record_kind_is_refused_not_crashed(self):
        class Weird:
            name = "weird"

            def records(self):
                yield SourceRecord(kind="telepathy", source="weird",
                                   source_ref="w1",
                                   observed_at="2026-01-01T00:00:00+00:00",
                                   payload={})

        store = _store()
        report = GraphPipeline(store).ingest(Weird())
        self.assertEqual(report.records, 1)
        self.assertEqual(len(report.refusals), 1)

    def test_missing_portal_export_raises_rather_than_reporting_zero(self):
        with self.assertRaises(FileNotFoundError):
            list(PortalCsv("/nonexistent/export.csv").records())

    def test_missing_project_catalogue_raises(self):
        with self.assertRaises(Exception):
            list(ProjectsJson("/nonexistent/projects.json").records())

    def test_missing_permit_root_yields_nothing_without_crashing(self):
        # An absent permits/ root means "no permits in flight", which is a
        # legitimate state -- unlike a missing catalogue or a named export that
        # is not there, both of which raise.
        self.assertEqual(list(PermitFolders("/nonexistent/permits").records()), [])

    def test_unimplemented_connector_raises_loudly(self):
        with self.assertRaises(NotImplementedError):
            list(PendingConnector("granola").records())

    def test_unknown_city_is_not_guessed(self):
        self.assertIsNone(jurisdiction_from_address("1 MAIN ST ATLANTIS 00000"))


class TestNotePreservation(unittest.TestCase):
    """Jay's own writing is never touched."""

    def setUp(self):
        self.store = _store()
        GraphPipeline(self.store).ingest(ProjectsJson(limit=5))
        self.entity = self.store.entities(E.PROJECT)[0]["entity_id"]
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        self.body = ("# My notes\n\nHandwritten content that must survive.\n\n"
                     "- a bullet Jay wrote\n")
        self.note = self.vault / "note.md"
        self.note.write_text(
            f"---\nentity_id: {self.entity}\ntags: [jay]\n---\n\n{self.body}")
        self.original = self.note.read_text()

    def tearDown(self):
        self.tmp.cleanup()

    def test_dry_run_changes_nothing_on_disk(self):
        report = inject_backlinks(self.store, self.vault, dry_run=True)
        self.assertGreaterEqual(report.written, 1)
        self.assertEqual(self.note.read_text(), self.original)

    def test_write_preserves_existing_content_and_frontmatter(self):
        inject_backlinks(self.store, self.vault, dry_run=False)
        updated = self.note.read_text()
        self.assertIn("Handwritten content that must survive.", updated)
        self.assertIn("- a bullet Jay wrote", updated)
        self.assertIn("tags: [jay]", updated)

    def test_rerun_is_byte_identical_and_adds_one_block(self):
        inject_backlinks(self.store, self.vault, dry_run=False)
        first = self.note.read_text()
        inject_backlinks(self.store, self.vault, dry_run=False)
        inject_backlinks(self.store, self.vault, dry_run=False)
        self.assertEqual(self.note.read_text(), first)
        self.assertEqual(first.count("jayos-graph:begin"), 1)

    def test_notes_without_entity_id_are_skipped(self):
        other = self.vault / "other.md"
        other.write_text("# untouched\n")
        inject_backlinks(self.store, self.vault, dry_run=False)
        self.assertEqual(other.read_text(), "# untouched\n")

    def test_missing_vault_raises_rather_than_reporting_success(self):
        with self.assertRaises(FileNotFoundError):
            inject_backlinks(self.store, "/nonexistent/vault", dry_run=True)

    def test_weak_edges_are_not_injected_into_notes(self):
        store = _store()
        a = _entity(store, E.PROJECT, "A Project")
        b = _entity(store, E.PROJECT, "B Project")
        store.relate(a, R.RELATED_TO, b, source_reference="r", confidence=0.4,
                     extraction_method="guess")
        note = self.vault / "weak.md"
        note.write_text(f"---\nentity_id: {a}\n---\n\nbody\n")
        report = inject_backlinks(store, self.vault, dry_run=True)
        self.assertEqual(report.written, 0, "WEAK edges must not flood notes")


class TestRollbackAndRecovery(unittest.TestCase):
    """Nothing is destroyed, so everything is recoverable."""

    def test_store_exposes_no_delete_api(self):
        for name in dir(GraphStore):
            self.assertFalse(name.startswith(("delete", "drop", "truncate", "purge")),
                             f"GraphStore.{name} would allow data loss")

    def test_retraction_preserves_the_original_claim(self):
        store = _store()
        a = _entity(store, E.PERMIT, "B26-2089")
        b = _entity(store, E.JURISDICTION, "Queen Creek")
        rel_id, _d, _c = store.relate(a, R.SUBMITTED_TO, b, source_reference="r",
                                      confidence=0.9, extraction_method="t")
        store.retract_relationship(rel_id, reason="wrong jurisdiction", actor="jay")
        self.assertEqual(len(store.all_relationships(active_only=True)), 0)
        self.assertEqual(len(store.all_relationships(active_only=False)), 1)
        retractions = store.execute("SELECT * FROM relationship_retractions")
        self.assertEqual(retractions[0]["reason"], "wrong jurisdiction")
        self.assertEqual(retractions[0]["actor"], "jay")

    def test_superseded_entity_keeps_its_row(self):
        store = _store()
        a = _entity(store, E.PERSON, "Bob Smith")
        b = _entity(store, E.PERSON, "Robert Smith")
        store.supersede_entity(a, merged_into=b, actor="jay")
        row = store.entity(a)
        self.assertIsNotNone(row, "the row must survive a merge")
        self.assertEqual(row["status"], EntityStatus.SUPERSEDED.value)
        self.assertEqual(json.loads(row["attributes"])["merged_into"], b)

    def test_retracting_unknown_relationship_is_refused(self):
        store = _store()
        with self.assertRaises(StoreError):
            store.retract_relationship(999, reason="x", actor="jay")

    def test_non_select_queries_are_refused(self):
        store = _store()
        with self.assertRaises(StoreError):
            store.execute("DELETE FROM entities")


class TestGraphHealth(unittest.TestCase):
    def test_isolated_node_rate_is_reported(self):
        store = _store()
        _entity(store, E.PROJECT, "Lonely Project")
        report = health_module.report(store)
        self.assertEqual(report.total_entities, 1)
        self.assertEqual(report.isolated_entities, 1)
        self.assertEqual(report.isolated_node_rate, 1.0)

    def test_connected_entities_are_not_isolated(self):
        store = _store()
        a = _entity(store, E.PERMIT, "B26-2089")
        b = _entity(store, E.JURISDICTION, "Queen Creek")
        store.relate(a, R.SUBMITTED_TO, b, source_reference="r", confidence=1.0,
                     extraction_method="t", asserted=True)
        self.assertEqual(health_module.report(store).isolated_node_rate, 0.0)

    def test_weakly_linked_entity_counts_as_effectively_isolated(self):
        store = _store()
        a = _entity(store, E.PROJECT, "A")
        b = _entity(store, E.PROJECT, "B")
        store.relate(a, R.RELATED_TO, b, source_reference="r", confidence=0.3,
                     extraction_method="guess")
        report = health_module.report(store)
        self.assertEqual(report.isolated_entities, 0)
        self.assertEqual(report.effectively_isolated, 2,
                         "a WEAK-only edge is not integration")

    def test_unmeasurable_orphan_notes_report_none_not_zero(self):
        store = _store()
        self.assertIsNone(health_module.report(store, None).orphan_notes)
        self.assertIsNone(
            health_module.report(store, "/nonexistent/vault").orphan_notes)

    def test_real_ingest_has_no_broken_links(self):
        store = _store()
        GraphPipeline(store).ingest(ProjectsJson(limit=25))
        self.assertEqual(health_module.report(store).broken_links, 0)

    def test_render_leads_with_the_isolated_rate(self):
        store = _store()
        _entity(store, E.PROJECT, "P")
        text = health_module.render(health_module.report(store))
        self.assertIn("ISOLATED NODE RATE", text.split("\n")[2])


class TestPagesRendering(unittest.TestCase):
    def test_page_contains_frontmatter_and_provenance(self):
        store = _store()
        GraphPipeline(store).ingest(ProjectsJson(limit=3))
        project = store.entities(E.PROJECT)[0]["entity_id"]
        page = render_entity_page(store, project)
        self.assertTrue(page.startswith("---\n"))
        self.assertIn(f"entity_id: {project}", page)
        self.assertIn("## Relationships", page)
        self.assertIn("confidence", page)
        self.assertIn("via `", page, "extraction method must be visible")

    def test_unknown_entity_page_raises(self):
        with self.assertRaises(KeyError):
            render_entity_page(_store(), "PROJECT-NOPE")


class TestRealDataIntegration(unittest.TestCase):
    """The engine must work on the actual catalogue, not just fixtures."""

    def test_ingest_of_real_catalogue_has_no_refusals(self):
        store = _store()
        report = GraphPipeline(store).ingest(ProjectsJson(limit=60))
        self.assertEqual(report.refusals, [])
        self.assertGreater(report.entities_created, 0)
        self.assertGreater(report.relationships_created, 0)

    def test_asserted_and_inferred_edges_are_distinguished(self):
        store = _store()
        GraphPipeline(store).ingest(ProjectsJson(limit=25))
        dispositions = {edge["disposition"] for edge in store.all_relationships()}
        self.assertIn("ASSERTED", dispositions)
        self.assertIn("PROPOSED", dispositions)
        for edge in store.all_relationships():
            if edge["disposition"] == "ASSERTED":
                self.assertTrue(edge["extraction_method"].startswith("asserted:"))
            if edge["disposition"] == "PROPOSED":
                self.assertTrue(edge["extraction_method"].startswith("inferred:"))


class TestSentinelContactsAdapter(unittest.TestCase):
    """Real production email context: client identities and matched mail."""

    def test_known_clients_and_consultants_parse_with_roles(self):
        from graph.adapters.sentinel_contacts import SentinelContacts
        records = list(SentinelContacts().records())
        self.assertGreater(len(records), 0)
        roles = {r.payload["role"] for r in records}
        self.assertEqual(roles, {"client", "consultant"})
        for r in records:
            self.assertTrue(r.payload["email"])
            self.assertEqual(r.payload["email"], r.payload["email"].lower())

    def test_missing_sentinel_toml_raises(self):
        from graph.adapters.sentinel_contacts import SentinelContacts
        with self.assertRaises(FileNotFoundError):
            list(SentinelContacts(Path("/nonexistent/sentinel.toml")).records())

    def test_jurisdiction_senders_are_filtered_from_matched_emails(self):
        """A city staffer's email must never become a CLIENT."""
        from graph.adapters.sentinel_contacts import SentinelMatchedEmails
        try:
            records = list(SentinelMatchedEmails().records())
        except RuntimeError:
            self.skipTest("sentinel ledger not reachable in this environment")
        for r in records:
            self.assertFalse(r.payload["sender"].endswith(".gov"),
                             "a .gov sender reached the pipeline unfiltered")

    def test_notification_senders_are_filtered(self):
        from graph.adapters.sentinel_contacts import _is_notification_sender
        self.assertTrue(_is_notification_sender("notify@mail.notion.com"))
        self.assertTrue(_is_notification_sender("docs@email.pandadoc.net"))
        self.assertFalse(_is_notification_sender("casey@c-rowdhouse.com"))


class TestKnownContactHandler(unittest.TestCase):
    """The two-tier claim: list membership is asserted, comment-parsed
    property links are inferred -- and a CLIENT never attempts WORKS_ON."""

    def _project_with_property(self, store, address="637 N RIATA ST GILBERT 85234"):
        pipeline = GraphPipeline(store)
        pipeline._handle_project(SourceRecord(
            kind="project", source="projects_json", source_ref="p#1",
            observed_at="2026-09-10T00:00:00+00:00",
            payload={"project_id": "riata", "name": "Riata", "apn": "",
                     "address": address, "permit_numbers": [],
                     "client_emails": [], "notion_page_id": "", "stage": "",
                     "drive_url": ""}))
        return pipeline

    def test_known_client_gets_asserted_client_of_regardless_of_project_match(self):
        store = _store()
        pipeline = self._project_with_property(store)
        pipeline._handle_known_contact(SourceRecord(
            kind="known_contact", source="sentinel_known_contacts",
            source_ref="s#1", observed_at="2026-09-10T00:00:00+00:00",
            payload={"email": "nobody@example.com", "role": "client",
                     "comment": "No address here at all"}))
        client = store.find_by_key(E.CLIENT, "nobody@example.com")
        self.assertIsNotNone(client)
        edges = store.relationships_from(client["entity_id"])
        client_of = [e for e in edges if e["relationship_type"] == "CLIENT_OF"]
        self.assertEqual(len(client_of), 1)
        self.assertEqual(client_of[0]["disposition"], "ASSERTED")
        self.assertEqual(client_of[0]["confidence"], 1.0)
        self.assertEqual(len(edges), 1, "no property edge without an address to parse")

    def test_known_client_with_matching_comment_gets_owns_not_works_on(self):
        store = _store()
        pipeline = self._project_with_property(store)
        pipeline._handle_known_contact(SourceRecord(
            kind="known_contact", source="sentinel_known_contacts",
            source_ref="s#1", observed_at="2026-09-10T00:00:00+00:00",
            payload={"email": "casey@c-rowdhouse.com", "role": "client",
                     "comment": "Casey Rowden Nair, 637 N Riata St"}))
        client = store.find_by_key(E.CLIENT, "casey@c-rowdhouse.com")
        edges = store.relationships_from(client["entity_id"])
        types = {e["relationship_type"] for e in edges}
        self.assertIn("OWNS", types)
        self.assertNotIn("WORKS_ON", types, "a CLIENT must never attempt WORKS_ON")
        owns = next(e for e in edges if e["relationship_type"] == "OWNS")
        self.assertLess(owns["confidence"], gates()[0],
                        "a comment-parsed address match is inferred, not asserted")

    def test_known_consultant_with_matching_comment_gets_works_on(self):
        store = _store()
        pipeline = self._project_with_property(store)
        pipeline._handle_known_contact(SourceRecord(
            kind="known_contact", source="sentinel_known_contacts",
            source_ref="s#1", observed_at="2026-09-10T00:00:00+00:00",
            payload={"email": "joe@burke.com", "role": "consultant",
                     "comment": "Joe Burke, 637 N Riata St"}))
        person = store.find_by_key(E.PERSON, "joe@burke.com")
        edges = store.relationships_from(person["entity_id"])
        self.assertEqual({e["relationship_type"] for e in edges}, {"WORKS_ON"})

    def test_apn_comment_link_reaches_the_auto_link_gate(self):
        store = _store()
        prop = _entity(store, E.PROPERTY, "18513 W Southgate Ave", key="18513920")
        pipeline = GraphPipeline(store)
        pipeline._handle_known_contact(SourceRecord(
            kind="known_contact", source="sentinel_known_contacts",
            source_ref="s#1", observed_at="2026-09-10T00:00:00+00:00",
            payload={"email": "jp@example.com", "role": "client",
                     "comment": "Jesus Palacio, APN 185-13-920"}))
        client = store.find_by_key(E.CLIENT, "jp@example.com")
        owns = [e for e in store.relationships_from(client["entity_id"])
                if e["relationship_type"] == "OWNS"]
        self.assertEqual(len(owns), 1)
        self.assertGreaterEqual(owns[0]["confidence"], gates()[0],
                                "an APN match is strong enough to auto-link")


class TestMatchedEmailHandler(unittest.TestCase):
    """Sender-to-project links the PRODUCTION matcher already computed."""

    def test_refuses_when_project_not_yet_ingested(self):
        store = _store()
        pipeline = GraphPipeline(store)
        pipeline._handle_matched_email(SourceRecord(
            kind="matched_email", source="sentinel_matched_emails",
            source_ref="e#1", observed_at="2026-09-10T00:00:00+00:00",
            payload={"sender": "x@example.com", "project_external_id": "nope",
                     "match_confidence": 0.95, "subject": "s"}))
        self.assertEqual(len(pipeline._report.refusals), 1)
        self.assertEqual(store.count("entities"), 0)

    def test_known_client_sender_gets_client_of_and_owns(self):
        store = _store()
        pipeline = GraphPipeline(store)
        pipeline._handle_project(SourceRecord(
            kind="project", source="projects_json", source_ref="p#1",
            observed_at="2026-09-10T00:00:00+00:00",
            payload={"project_id": "ext-1", "name": "Test", "apn": "",
                     "address": "1 A ST MESA 85201", "permit_numbers": [],
                     "client_emails": [], "notion_page_id": "", "stage": "",
                     "drive_url": ""}))
        _entity(store, E.CLIENT, "Cash Hasse", key="cash@example.com",
               attributes={"role": "client"})
        pipeline._handle_matched_email(SourceRecord(
            kind="matched_email", source="sentinel_matched_emails",
            source_ref="e#1", observed_at="2026-09-10T00:00:00+00:00",
            payload={"sender": "cash@example.com", "project_external_id": "ext-1",
                     "match_confidence": 0.95, "subject": "s"}))
        client = store.find_by_key(E.CLIENT, "cash@example.com")
        types = {e["relationship_type"] for e in store.relationships_from(client["entity_id"])}
        self.assertIn("CLIENT_OF", types)
        self.assertIn("OWNS", types)

    def test_unclassified_sender_becomes_person_at_inferred_confidence(self):
        store = _store()
        pipeline = GraphPipeline(store)
        pipeline._handle_project(SourceRecord(
            kind="project", source="projects_json", source_ref="p#1",
            observed_at="2026-09-10T00:00:00+00:00",
            payload={"project_id": "ext-2", "name": "Test2", "apn": "",
                     "address": "2 B ST MESA 85201", "permit_numbers": [],
                     "client_emails": [], "notion_page_id": "", "stage": "",
                     "drive_url": ""}))
        pipeline._handle_matched_email(SourceRecord(
            kind="matched_email", source="sentinel_matched_emails",
            source_ref="e#1", observed_at="2026-09-10T00:00:00+00:00",
            payload={"sender": "mystery@example.com", "project_external_id": "ext-2",
                     "match_confidence": 0.97, "subject": "s"}))
        person = store.find_by_key(E.PERSON, "mystery@example.com")
        self.assertIsNotNone(person)
        edge = store.relationships_from(person["entity_id"])[0]
        self.assertEqual(edge["extraction_method"],
                         "inferred:matched_email_unclassified_sender")
        self.assertLess(edge["confidence"], gates()[0])


class TestVaultCrosswalk(unittest.TestCase):
    """Correspondence recorded, never a rename -- DATA_DICTIONARY.md's own rule."""

    def test_crosswalk_does_not_change_the_entity_id(self):
        store = _store()
        eid = _entity(store, E.PROJECT, "Vallejo", external_id="notion-abc")
        store.record_vault_correspondence(eid, "PRJ-2026-0042", source="test",
                                          source_ref="r1", confidence=0.9)
        self.assertEqual(store.entity(eid)["entity_id"], eid,
                         "the entity's own id must never be rewritten")

    def test_lookup_both_directions(self):
        store = _store()
        eid = _entity(store, E.PERSON, "Casey Rowden Nair")
        store.record_vault_correspondence(eid, "PER-0007", source="test",
                                          source_ref="r1", confidence=0.85)
        self.assertEqual(store.vault_id_for(eid), "PER-0007")
        self.assertEqual(store.entity_for_vault_id("PER-0007"), eid)

    def test_malformed_vault_id_is_refused(self):
        store = _store()
        eid = _entity(store, E.PERSON, "X")
        with self.assertRaises(StoreError):
            store.record_vault_correspondence(eid, "not-a-real-id", source="t",
                                              source_ref="r", confidence=0.9)

    def test_unknown_entity_is_refused(self):
        store = _store()
        with self.assertRaises(StoreError):
            store.record_vault_correspondence("PERSON-NOPE", "PER-0001",
                                              source="t", source_ref="r",
                                              confidence=0.9)

    def test_idempotent(self):
        store = _store()
        eid = _entity(store, E.PERSON, "X")
        first = store.record_vault_correspondence(eid, "PER-0001", source="t",
                                                   source_ref="r", confidence=0.9)
        second = store.record_vault_correspondence(eid, "PER-0001", source="t",
                                                    source_ref="r", confidence=0.9)
        self.assertTrue(first)
        self.assertFalse(second)

    def test_established_identifier_detection_matches_the_dictionary_rule(self):
        from graph.crosswalk import has_established_identifier
        self.assertTrue(has_established_identifier({"notion_page_id": "abc"}))
        self.assertTrue(has_established_identifier({"apn": "314-14-919"}))
        self.assertTrue(has_established_identifier({"email": "x@y.com"}))
        self.assertFalse(has_established_identifier({}))

    def test_project_type_has_a_vault_prefix_but_permit_does_not(self):
        from graph.crosswalk import vault_prefix_for
        self.assertEqual(vault_prefix_for(E.PROJECT), "PRJ")
        self.assertIsNone(vault_prefix_for(E.PERMIT),
                          "PERMIT has no DATA_DICTIONARY.md equivalent -- must "
                          "not be guessed")


class TestGraphifyExport(unittest.TestCase):
    """Schema-compatible output, not a merge -- never touches graphify-out/."""

    def test_export_shape_matches_graphify(self):
        from graph.graphify_export import build_export
        store = _store()
        GraphPipeline(store).ingest(ProjectsJson(limit=5))
        d = build_export(store)
        self.assertIn("directed", d)
        self.assertIn("multigraph", d)
        self.assertIn("nodes", d)
        self.assertIn("edges", d)
        self.assertIsInstance(d["graph"]["hyperedges"], list)

    def test_confidence_categories_stay_in_graphify_rubric(self):
        from graph.graphify_export import build_export
        store = _store()
        GraphPipeline(store).ingest(ProjectsJson(limit=15))
        d = build_export(store)
        cats = {e["confidence"] for e in d["edges"]}
        self.assertTrue(cats <= {"EXTRACTED", "INFERRED"})

    def test_asserted_disposition_always_exports_as_extracted_1_0(self):
        from graph.graphify_export import build_export
        store = _store()
        a = _entity(store, E.PERMIT, "B26-2089")
        b = _entity(store, E.JURISDICTION, "Queen Creek")
        store.relate(a, R.SUBMITTED_TO, b, source_reference="r", confidence=1.0,
                     extraction_method="t", asserted=True)
        d = build_export(store)
        self.assertEqual(d["edges"][0]["confidence"], "EXTRACTED")
        self.assertEqual(d["edges"][0]["confidence_score"], 1.0)

    def test_write_export_produces_valid_json(self):
        from graph.graphify_export import write_export
        import json as jsonlib
        store = _store()
        GraphPipeline(store).ingest(ProjectsJson(limit=5))
        with tempfile.TemporaryDirectory() as out:
            path = write_export(store, Path(out) / "export.json")
            reloaded = jsonlib.loads(path.read_text())
            self.assertEqual(len(reloaded["nodes"]), store.count("entities"))

    def test_export_never_writes_outside_its_own_output_file(self):
        """The module may EXPLAIN in its docstring why it doesn't touch
        graphify-out or the vault; the actual code must contain no hardcoded
        path into either. Checked on the source with the module docstring
        stripped, so the explanation itself doesn't trip the assertion."""
        from graph import graphify_export
        import ast
        import inspect
        source = inspect.getsource(graphify_export)
        module = ast.parse(source)
        docstring = ast.get_docstring(module) or ""
        code_only = source.replace(docstring, "", 1)
        self.assertNotIn("graphify-out", code_only)
        self.assertNotIn("JAY-OS", code_only)
        # and confirm the one file it DOES write is parameterised, not fixed
        self.assertIn("out_path: str | Path = DEFAULT_OUT", source)


class TestPilot(unittest.TestCase):
    """The Phase 12 pilot is itself a regression test."""

    def test_pilot_passes_end_to_end(self):
        from graph.pilot import run_pilot
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as out:
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = run_pilot(Path(out))
            self.assertEqual(code, 0, buffer.getvalue()[-2000:])
            self.assertIn("PILOT PASSED", buffer.getvalue())

    def test_pilot_never_touches_the_production_database(self):
        from graph import pilot
        import inspect
        source = inspect.getsource(pilot.run_pilot)
        self.assertIn("out_dir", source)
        self.assertNotIn("jayos-graph.sqlite3", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
