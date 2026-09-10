"""The Lesson Engine: discover patterns, propose nothing binding.

THE HARD RULE, RESTATED IN CODE
-------------------------------
This module may DISCOVER a lesson. It may not promote one. Every miner here
calls `store.propose_lesson`, which refuses any status beyond CANDIDATE --
see `store.set_lesson_status` for the governance gate. There is deliberately
no function in this file that writes a production rule anywhere.

WHY MIN_SUPPORT IS 2 AND CONFIDENCE IS CAPPED
--------------------------------------------
One occurrence is an anecdote. Jay's own instruction about overrides applies to
every pattern here: "Do not assume every override is a permanent rule.
Accumulate evidence." So a pattern needs at least two supporting observations
to be written down at all, and confidence is capped below the auto-link gate so
that no lesson can ever present itself as settled fact.
"""
from __future__ import annotations

import dataclasses
import json
import re
from collections import Counter, defaultdict

from ._reuse import gates
from .contract import (Disposition, EntityType as E, LessonStatus,
                       RelationType as R, VocabularyError)
from .store import GraphStore, StoreError

#: Two observations minimum. One is an anecdote.
MIN_SUPPORT = 2

#: A discovered lesson can never claim more certainty than a proposed link.
#: Keeps "the engine noticed a pattern" strictly weaker than "this is true".
LESSON_CONFIDENCE_CEILING = 0.90

_CORRECTION_STATUS = re.compile(
    r"correction|deficien|returned|revision required|resubmit", re.I)


@dataclasses.dataclass
class LessonReport:
    proposed: int = 0
    updated: int = 0
    lesson_ids: list[str] = dataclasses.field(default_factory=list)

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def _confidence(support: int) -> float:
    """More evidence, more confidence -- asymptotically, never certainty."""
    return round(min(LESSON_CONFIDENCE_CEILING, 0.55 + 0.08 * support), 3)


def _attrs(row) -> dict:
    return json.loads(row["attributes"] or "{}")


def mine_jurisdiction_corrections(store: GraphStore) -> list[dict]:
    """Which jurisdictions keep sending work back.

    Evidence: permits whose portal status reads like a correction, grouped by
    the jurisdiction they were SUBMITTED_TO.
    """
    by_jurisdiction: dict[str, list[str]] = defaultdict(list)
    for permit in store.entities(E.PERMIT):
        status = str(_attrs(permit).get("status", ""))
        if not status or not _CORRECTION_STATUS.search(status):
            continue
        for edge in store.relationships_from(permit["entity_id"]):
            if edge["relationship_type"] == R.SUBMITTED_TO.value:
                by_jurisdiction[edge["target_entity"]].append(permit["entity_id"])
    findings = []
    for jurisdiction, permits in by_jurisdiction.items():
        if len(permits) < MIN_SUPPORT:
            continue
        row = store.entity(jurisdiction)
        name = row["canonical_name"] if row else jurisdiction
        findings.append({
            "observation": (f"{len(permits)} permits submitted to {name} are in a "
                            f"corrections/deficiency state."),
            "pattern": f"jurisdiction_corrections::{jurisdiction}",
            "proposed_rule": (f"When preparing a submittal for {name}, pre-check the "
                              f"items that jurisdiction most often returns before "
                              f"first submission."),
            "expected_impact": "Fewer correction cycles per permit.",
            "supporting_events": permits,
            "confidence": _confidence(len(permits)),
        })
    return findings


def mine_owner_overrides(store: GraphStore) -> list[dict]:
    """Corrections Jay has made more than once.

    Grouped on a coarse signature of (what was proposed -> what Jay chose), so
    two phrasings of the same correction count as the same evidence.
    """
    def signature(text: str) -> str:
        words = re.findall(r"[a-z]{4,}", (text or "").lower())
        return " ".join(sorted(set(words))[:6])

    buckets: dict[tuple[str, str], list[int]] = defaultdict(list)
    for row in store.overrides():
        buckets[(signature(row["agent_proposal"]),
                 signature(row["owner_decision"]))].append(int(row["override_id"]))
    findings = []
    for (proposal, decision), ids in buckets.items():
        if len(ids) < MIN_SUPPORT or not proposal or not decision:
            continue
        findings.append({
            "observation": (f"Jay has overridden the same kind of proposal "
                            f"{len(ids)} times (proposed '{proposal}' -> chose "
                            f"'{decision}')."),
            "pattern": f"owner_override::{proposal}=>{decision}",
            "proposed_rule": (f"Default to Jay's choice ('{decision}') instead of "
                              f"proposing '{proposal}'."),
            "expected_impact": "Fewer corrections on the same point.",
            "supporting_events": [f"OVERRIDE-{i}" for i in ids],
            "confidence": _confidence(len(ids)),
        })
    return findings


def mine_weak_extraction_methods(store: GraphStore) -> list[dict]:
    """Heuristics that never clear the propose gate.

    An extraction method whose every edge lands WEAK is not earning its keep;
    it adds noise a human must filter. Worth telling Jay about, not worth
    silently disabling.
    """
    totals: Counter[str] = Counter()
    weak: Counter[str] = Counter()
    for edge in store.all_relationships():
        method = edge["extraction_method"]
        totals[method] += 1
        if Disposition(edge["disposition"]) is Disposition.WEAK:
            weak[method] += 1
    findings = []
    for method, count in weak.items():
        if count < MIN_SUPPORT or totals[method] != count:
            continue
        findings.append({
            "observation": (f"Every one of the {count} edges from "
                            f"'{method}' fell below the propose gate."),
            "pattern": f"weak_method::{method}",
            "proposed_rule": (f"Review '{method}': either strengthen its evidence "
                              f"or stop emitting it."),
            "expected_impact": "Less low-confidence noise in the graph.",
            "supporting_events": [method],
            "confidence": _confidence(count),
        })
    return findings


def mine_unplaced_properties(store: GraphStore) -> list[dict]:
    """Properties the engine could not put in a jurisdiction.

    Usually means a city missing from `places.ARIZONA_JURISDICTIONS`. That is a
    reviewed vocabulary change, so it surfaces as a lesson rather than the
    engine quietly adding cities to itself.
    """
    unplaced = []
    for prop in store.entities(E.PROPERTY):
        placed = any(edge["relationship_type"] == R.LOCATED_IN.value
                     for edge in store.relationships_from(prop["entity_id"]))
        if not placed:
            unplaced.append(prop["entity_id"])
    if len(unplaced) < MIN_SUPPORT:
        return []
    return [{
        "observation": (f"{len(unplaced)} properties have no jurisdiction, so their "
                        f"address contained no recognised city."),
        "pattern": "unplaced_properties",
        "proposed_rule": ("Review these addresses and extend "
                          "places.ARIZONA_JURISDICTIONS if a real city is missing."),
        "expected_impact": "Fewer isolated property nodes; better routing.",
        "supporting_events": unplaced[:25],
        "confidence": _confidence(min(len(unplaced), 4)),
    }]


def mine_repeated_refusals(store: GraphStore) -> list[dict]:
    """The engine learning from its own failed runs.

    Every ingest event records its refusals. A refusal that recurs across
    runs is not noise -- it is a record the pipeline cannot handle and keeps
    tripping on. Surfacing that as a lesson is cheaper than a human noticing
    the same line in the log for the fifth night running.
    """
    seen: dict[str, list[str]] = defaultdict(list)
    for event in store.events("graph_ingest"):
        prov = json.loads(event["provenance"] or "{}")
        for refusal in prov.get("refusal_samples", []):
            # Strip the volatile record ref; keep the reason, which is what repeats.
            reason = refusal.split(": ", 1)[-1][:120]
            seen[reason].append(event["event_id"])
    findings = []
    for reason, events in seen.items():
        if len(set(events)) < MIN_SUPPORT:
            continue
        findings.append({
            "observation": (f"The same ingest refusal recurred across "
                            f"{len(set(events))} nightly runs: '{reason}'."),
            "pattern": f"repeated_refusal::{reason[:60]}",
            "proposed_rule": ("Either fix the source record or teach the adapter "
                              "to handle this shape; it will not resolve itself."),
            "expected_impact": "One fewer permanent gap in the graph.",
            "supporting_events": sorted(set(events)),
            "confidence": _confidence(len(set(events))),
        })
    return findings


MINERS = (mine_jurisdiction_corrections, mine_owner_overrides,
          mine_weak_extraction_methods, mine_unplaced_properties,
          mine_repeated_refusals)


def _attach_to_graph(store: GraphStore, lesson_id: str, finding: dict,
                     actor: str) -> None:
    """Put the lesson IN the graph, linked to the evidence that produced it.

    Without this a lesson lives only in the `lessons` table -- discoverable
    by the brief, but invisible to the graph it was learned from. You could
    not ask "what did we learn from this jurisdiction?" because the lesson
    and the jurisdiction were in different worlds. `LEARNED_FROM` is in the
    frozen vocabulary precisely for this and was, until now, unreachable.

    Only entities that already exist are linked. Supporting evidence is a
    mix of entity ids, event ids and bare strings depending on the miner, so
    anything that does not resolve to a real node is skipped rather than
    conjured -- the same no-phantom-endpoints rule `relate()` enforces.
    """
    # Key on the bare sequence so entity_id() yields LESSON-00001 rather than
    # LESSON-LESSON-00001 -- the graph entity id and the lesson_id are then
    # the same string, which is what makes them joinable without a lookup.
    sequence = lesson_id.split("-", 1)[1] if "-" in lesson_id else lesson_id
    lesson_entity = store.upsert_entity(
        E.LESSON, finding["observation"][:80], key=sequence,
        source="lesson_engine", source_ref=f"lesson:{lesson_id}",
        confidence=float(finding["confidence"]),
        attributes={"lesson_id": lesson_id,
                    "pattern": finding["pattern"],
                    "proposed_rule": finding["proposed_rule"],
                    "status": LessonStatus.CANDIDATE.value})

    for reference in finding.get("supporting_events", ()):
        target = store.entity(reference)
        if target is None:
            continue  # an event id or a bare method name, not a node
        try:
            store.relate(
                lesson_entity, R.LEARNED_FROM, reference,
                source_reference=f"lesson:{lesson_id}",
                confidence=float(finding["confidence"]),
                extraction_method="inferred:lesson_engine_supporting_evidence",
                provenance={"lesson_id": lesson_id, "actor": actor})
        except (VocabularyError, StoreError):
            # LEARNED_FROM accepts OUTCOME/DECISION/REVIEW_COMMENT/EMAIL only.
            # A lesson supported by a PERMIT is real evidence but not a legal
            # edge; the lesson still stands on its own record.
            continue


def generate(store: GraphStore, actor: str = "graph-engine") -> LessonReport:
    """Run every miner. Produces CANDIDATE lessons and nothing stronger."""
    report = LessonReport()
    before = {row["lesson_id"] for row in store.lessons()}
    for miner in MINERS:
        for finding in miner(store):
            lesson_id = store.propose_lesson(
                observation=finding["observation"],
                pattern=finding["pattern"],
                proposed_rule=finding["proposed_rule"],
                expected_impact=finding.get("expected_impact", ""),
                confidence=finding["confidence"],
                supporting_events=finding.get("supporting_events", ()),
                status=LessonStatus.CANDIDATE)
            _attach_to_graph(store, lesson_id, finding, actor)
            report.lesson_ids.append(lesson_id)
            if lesson_id in before:
                report.updated += 1
            else:
                report.proposed += 1
    store.record_event(
        event_type="lesson_generation", source="lesson_engine", actor=actor,
        action="mine event history and graph for repeated patterns",
        result=f"{report.proposed} new, {report.updated} updated",
        provenance=report.as_dict())
    return report
