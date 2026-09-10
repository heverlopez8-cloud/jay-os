"""What changed overnight, as one page a human reads in thirty seconds.

WHY THIS EXISTS
---------------
The nightly job already re-ingests, mines lessons and prints a health block
into `graph/logs/ingest.log`. Nobody reads a log. And a health block is a
snapshot -- it says 1,308 entities, not "eleven new ones, and one of them is
a permit nobody has looked at."

`email_sentinel/brief.py` made the same argument about the sentinel:
"Capture was never the gap. Assembly was." This is that argument applied to
the graph.

HOW "OVERNIGHT" IS COMPUTED
---------------------------
The nightly run writes a row into `snapshots` (see `store.take_snapshot`).
This brief diffs the two most recent rows. That makes the delta a
subtraction over recorded state rather than a query over `created_at`
windows, which would quietly lie the first time a run is skipped, retried,
or takes place either side of midnight.

If there is only one snapshot, it says so. A first run has nothing to
compare against, and "no change" would be a false statement, not a modest one.

WHAT IT NEVER DOES
------------------
No writes to the graph, no promotion of anything, no vault contact. It reads
the store and returns text. A failure here costs a morning's convenience and
nothing else.
"""
from __future__ import annotations

import json
from typing import Any

from .contract import Disposition, EntityType, LessonStatus
from .store import GraphStore


def _delta(now: int, before: int) -> str:
    change = now - before
    if change > 0:
        return f"+{change}"
    if change < 0:
        return str(change)
    return "0"


def _new_since(store: GraphStore, iso_cutoff: str) -> dict[str, Any]:
    """Entities and edges recorded after the previous snapshot.

    Best-effort ONLY. `created_at` is second-precision (`now_iso` drops
    microseconds), so anything created in the same second as the previous
    snapshot cannot be distinguished from what preceded it. The authoritative
    "did anything change" answer is the snapshot count delta, never this --
    see `render`, which will not claim a quiet night on the strength of an
    empty result here.
    """
    entities = store.execute(
        "SELECT entity_id, entity_type, canonical_name FROM entities "
        "WHERE created_at > ? ORDER BY entity_type, canonical_name", (iso_cutoff,))
    edges = store.execute(
        "SELECT source_entity, relationship_type, target_entity, disposition, "
        "confidence, extraction_method FROM relationships "
        "WHERE created_at > ? AND status = 'active' ORDER BY rel_id", (iso_cutoff,))
    return {"entities": entities, "edges": edges}


def render(store: GraphStore) -> str:
    """The brief. Headline first, then only what needs a human."""
    snapshots = store.latest_snapshots(2)
    lines: list[str] = ["JAY-OS GRAPH — OVERNIGHT BRIEF", "=" * 64]

    if not snapshots:
        lines.append("  No snapshots recorded yet. Run the nightly job once.")
        return "\n".join(lines)

    current = snapshots[0]
    lines.append(f"  as of {current['taken_at']}")

    if len(snapshots) < 2:
        lines += [
            "",
            "  First snapshot — nothing to compare against yet.",
            f"  Baseline: {current['entities']} entities, "
            f"{current['relationships']} relationships.",
            "  Tomorrow's brief will show the change.",
        ]
        return "\n".join(lines)

    previous = snapshots[1]
    since = previous["taken_at"]
    lines += ["", f"  CHANGE SINCE {since}", "  " + "-" * 62]
    counted_change = 0
    for label, key in (("entities", "entities"),
                       ("relationships", "relationships"),
                       ("candidate merges", "candidate_merges"),
                       ("candidate lessons", "candidate_lessons"),
                       ("owner overrides", "overrides")):
        counted_change += abs(current[key] - previous[key])
        lines.append(f"    {label:<20} {current[key]:>6}   "
                     f"({_delta(current[key], previous[key])})")
    drift = current["isolated_rate"] - previous["isolated_rate"]
    lines.append(f"    {'isolated rate':<20} {current['isolated_rate']:>6.1%}   "
                 f"({drift:+.1%})")

    fresh = _new_since(store, since)

    if fresh["entities"]:
        lines += ["", f"  NEW ENTITIES ({len(fresh['entities'])})", "  " + "-" * 62]
        for row in fresh["entities"][:25]:
            lines.append(f"    {row['entity_type']:<14} {row['canonical_name'][:44]}")
        if len(fresh["entities"]) > 25:
            lines.append(f"    ... and {len(fresh['entities']) - 25} more")

    # Only edges a human might act on. WEAK edges are recorded, not reported.
    actionable = [e for e in fresh["edges"]
                  if e["disposition"] in (Disposition.ASSERTED.value,
                                          Disposition.LINKED.value)]
    proposed = [e for e in fresh["edges"]
                if e["disposition"] == Disposition.PROPOSED.value]
    if actionable or proposed:
        lines += ["", f"  NEW RELATIONSHIPS ({len(actionable)} firm, "
                      f"{len(proposed)} proposed)", "  " + "-" * 62]
        for edge in (actionable + proposed)[:15]:
            lines.append(f"    {edge['source_entity'][:30]:<30} "
                         f"--{edge['relationship_type']}--> "
                         f"{edge['target_entity'][:28]}")
            lines.append(f"      [{edge['disposition']}] via {edge['extraction_method']}")

    # --- the part that actually wants a decision -------------------------
    merges = store.candidate_merges()
    if merges:
        lines += ["", f"  NEEDS YOU — possible duplicates ({len(merges)})",
                  "  " + "-" * 62]
        for merge in merges[:8]:
            left = store.entity(merge["left_entity"])
            right = store.entity(merge["right_entity"])
            lines.append(
                f"    {(left['canonical_name'] if left else merge['left_entity'])[:26]:<26}"
                f" ~ {(right['canonical_name'] if right else merge['right_entity'])[:26]:<26}"
                f" {merge['confidence']:.2f}")
        if len(merges) > 8:
            lines.append(f"    ... and {len(merges) - 8} more")

    lessons = store.lessons(LessonStatus.CANDIDATE)
    if lessons:
        lines += ["", f"  NEEDS YOU — candidate lessons ({len(lessons)})",
                  "  " + "-" * 62]
        for lesson in lessons[:5]:
            lines.append(f"    [{lesson['lesson_id']}] conf {lesson['confidence']}")
            lines.append(f"      {lesson['observation'][:76]}")
            lines.append(f"      rule: {lesson['proposed_rule'][:72]}")
        lines.append("")
        lines.append("    None are active. Promotion needs you — the engine cannot.")

    # Refusals from the most recent ingest events, if any.
    refusals = []
    for event in store.events("graph_ingest"):
        if event["timestamp"] <= since:
            continue
        refusals += json.loads(event["provenance"] or "{}").get("refusal_samples", [])
    if refusals:
        lines += ["", f"  REFUSED LAST RUN ({len(refusals)})", "  " + "-" * 62]
        for refusal in refusals[:6]:
            lines.append(f"    {refusal[:74]}")

    # The verdict comes from the COUNTS, which are exact, never from the
    # detail queries, which are timestamp-bounded and can come back empty for
    # a window they cannot resolve. Printing "nothing changed" above a row
    # reading "+22" was the bug this replaced.
    if counted_change == 0 and not (merges or lessons or refusals):
        lines += ["", "  Nothing changed overnight. The sources were quiet."]
    elif counted_change and not (fresh["entities"] or fresh["edges"]):
        lines += ["",
                  f"  ({counted_change} counted change(s), but the per-item "
                  f"detail could not be resolved for this window -- the two "
                  f"snapshots fall within the same second.)"]

    return "\n".join(lines)
