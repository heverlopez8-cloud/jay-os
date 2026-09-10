"""Graph health. The headline number is the isolated node rate.

WHY ISOLATED NODE RATE AND NOT EDGE COUNT
-----------------------------------------
Jay was explicit: "The goal is not maximum links. The goal is meaningful
integration." Edge count is trivially gameable -- link everything to everything
and the number looks superb while the graph becomes useless. An isolated node
is an honest failure: a thing JAY-OS knows about but cannot connect to anything.
Driving that number down is real integration; driving edge count up is not.

Low-confidence edges are reported separately and deliberately NOT counted as
integration. An entity whose only edges are WEAK is still effectively isolated,
so `effectively_isolated` counts it as such.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import re
from collections import Counter
from pathlib import Path

from ._reuse import gates
from .contract import Disposition, LessonStatus
from .store import GraphStore

_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n", re.S)


@dataclasses.dataclass
class HealthReport:
    total_entities: int = 0
    total_relationships: int = 0
    entities_by_type: dict = dataclasses.field(default_factory=dict)
    relationships_by_type: dict = dataclasses.field(default_factory=dict)
    isolated_entities: int = 0
    isolated_node_rate: float = 0.0
    effectively_isolated: int = 0
    effectively_isolated_rate: float = 0.0
    duplicate_candidates: int = 0
    unresolved_entities: int = 0
    low_confidence_relationships: int = 0
    retracted_relationships: int = 0
    orphan_notes: int | None = None
    most_connected_entities: list = dataclasses.field(default_factory=list)
    new_relationships_last_24h: int = 0
    candidate_lessons: int = 0
    promoted_lessons: int = 0
    broken_links: int = 0

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def report(store: GraphStore, vault_root: str | Path | None = None) -> HealthReport:
    """Measure the graph. Unmeasurable things report None, never zero."""
    _auto, propose, _min = gates()
    health = HealthReport()

    entities = store.entities()
    health.total_entities = len(entities)
    health.entities_by_type = dict(
        Counter(row["entity_type"] for row in entities).most_common())

    edges = store.all_relationships()
    health.total_relationships = len(edges)
    health.relationships_by_type = dict(
        Counter(row["relationship_type"] for row in edges).most_common())

    known = {row["entity_id"] for row in entities}
    degree: Counter[str] = Counter()
    strong_degree: Counter[str] = Counter()
    for edge in edges:
        for end in (edge["source_entity"], edge["target_entity"]):
            degree[end] += 1
            if float(edge["confidence"]) >= propose:
                strong_degree[end] += 1
        if edge["source_entity"] not in known or edge["target_entity"] not in known:
            health.broken_links += 1

    health.isolated_entities = sum(1 for row in entities if degree[row["entity_id"]] == 0)
    health.effectively_isolated = sum(
        1 for row in entities if strong_degree[row["entity_id"]] == 0)
    if health.total_entities:
        health.isolated_node_rate = round(
            health.isolated_entities / health.total_entities, 4)
        health.effectively_isolated_rate = round(
            health.effectively_isolated / health.total_entities, 4)

    health.low_confidence_relationships = sum(
        1 for edge in edges if Disposition(edge["disposition"]) is Disposition.WEAK)
    health.retracted_relationships = len(
        store.execute("SELECT rel_id FROM relationships WHERE status = 'retracted'"))
    health.duplicate_candidates = len(store.candidate_merges())
    health.unresolved_entities = sum(
        1 for row in entities
        if row["status"] in ("MERGE_CANDIDATE", "PROVISIONAL"))

    health.most_connected_entities = [
        {"entity_id": eid, "degree": count,
         "canonical_name": (store.entity(eid) or {"canonical_name": "?"})["canonical_name"]}
        for eid, count in degree.most_common(10)]

    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)).isoformat()
    health.new_relationships_last_24h = len(store.execute(
        "SELECT rel_id FROM relationships WHERE created_at >= ?", (cutoff,)))

    health.candidate_lessons = len(store.lessons(LessonStatus.CANDIDATE))
    health.promoted_lessons = len(store.lessons(LessonStatus.ACTIVE))

    if vault_root is not None:
        root = Path(vault_root)
        if root.exists():
            orphans = 0
            for note in root.rglob("*.md"):
                front = _FRONTMATTER.match(note.read_text(encoding="utf-8",
                                                          errors="replace"))
                if not front:
                    continue
                found = re.search(r"^entity_id:\s*(\S+)\s*$", front.group(1), re.M)
                if found and store.entity(found.group(1).strip().strip("\"'")) is None:
                    orphans += 1
            health.orphan_notes = orphans
        # vault missing -> stays None. "Unmeasurable" is not "zero".
    return health


def render(health: HealthReport) -> str:
    """Human-readable health, headline metric first."""
    lines = [
        "JAY-OS GRAPH HEALTH",
        "=" * 62,
        f"  ISOLATED NODE RATE        {health.isolated_node_rate:>8.1%}   "
        f"({health.isolated_entities}/{health.total_entities} entities)",
        f"  effectively isolated      {health.effectively_isolated_rate:>8.1%}   "
        f"(no edge at/above the propose gate)",
        "-" * 62,
        f"  total_entities            {health.total_entities:>8}",
        f"  total_relationships       {health.total_relationships:>8}",
        f"  duplicate_candidates      {health.duplicate_candidates:>8}",
        f"  unresolved_entities       {health.unresolved_entities:>8}",
        f"  low_confidence_relations  {health.low_confidence_relationships:>8}",
        f"  retracted_relationships   {health.retracted_relationships:>8}",
        f"  new_relationships_24h     {health.new_relationships_last_24h:>8}",
        f"  candidate_lessons         {health.candidate_lessons:>8}",
        f"  promoted_lessons          {health.promoted_lessons:>8}",
        f"  broken_links              {health.broken_links:>8}",
        f"  orphan_notes              "
        f"{'unmeasured (no vault)' if health.orphan_notes is None else health.orphan_notes:>8}",
        "-" * 62,
        "  entities by type: " + ", ".join(
            f"{k}={v}" for k, v in health.entities_by_type.items()),
        "  relationships by type: " + ", ".join(
            f"{k}={v}" for k, v in health.relationships_by_type.items()),
    ]
    if health.most_connected_entities:
        lines.append("-" * 62)
        lines.append("  most connected:")
        for item in health.most_connected_entities[:5]:
            lines.append(f"    {item['degree']:>3}  {item['entity_id']}  "
                         f"{item['canonical_name'][:44]}")
    return "\n".join(lines)
