"""Export this graph in graphify's own JSON schema.

WHY
---
Two systems, two schemas, today:
  - This engine: relationships table, single float confidence, a
    `Disposition` enum (ASSERTED/LINKED/PROPOSED/WEAK).
  - graphify: `{"directed": false, "multigraph": false, "graph": {...}}`
    with `nodes`/`edges`/`hyperedges`, and a two-part confidence --
    a category (`EXTRACTED` or `INFERRED`) plus a numeric `confidence_score`.

Reconciling the ID question (`crosswalk.py`) does not by itself make the two
datasets combinable -- they still speak different JSON. This module makes
this engine's data GRAPHIFY-SHAPED, so that when Jay (or graphify's own
tooling) is ready to actually merge, the PCD-business graph is something
graphify's own merge/validation code can read without a translation step.

CONFIDENCE MAPPING
-------------------
Disposition.ASSERTED  -> category "EXTRACTED", score 1.0
    (a source of truth said it directly -- the closest analogue to
    graphify's EXTRACTED, which means "read directly off a note")
Disposition.LINKED / PROPOSED / WEAK -> category "INFERRED", score = our
    confidence float, unchanged
    (this engine worked it out -- graphify's INFERRED category, same idea)

graphify's VALIDATION_REPORT.md flagged edges whose INFERRED score was "not
in rubric set" as a real defect. This export only ever emits scores this
engine already computed from its own disposition_for() gates, so it cannot
produce an off-rubric value the way a hand-typed one could.

WHAT THIS DOES NOT DO
----------------------
It does not write into `graphify-out/`. It has no path there and no
authority to. It writes ONE local file this engine owns
(`graph/graphify_compatible_export.json`), for Jay or graphify's own tooling
to pick up when a real merge is wanted. It also does not import graphify's
current output -- that dataset carries graphify's own "BLOCKING ISSUES
FOUND" verdict (2026-08-28 VALIDATION_REPORT.md) and includes vault content
(`05_KNOWLEDGE/Unani`) this engine has no business touching, per
`AI_CONTEXT.md`: "the PCD tenant agent never receives health or family
material."
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contract import Disposition
from .crosswalk import vault_prefix_for
from .contract import EntityType
from .store import GraphStore

DEFAULT_OUT = Path(__file__).resolve().parent / "graphify_compatible_export.json"


def _node_id(entity_id: str) -> str:
    """graphify's own node IDs are lowercase path-slugs; ours are TYPE-KEY.
    Keep ours as-is rather than reshaping to match -- the crosswalk table is
    the join key, not a shared ID format. A node's own `id` field just needs
    to be unique and stable, which ours already is."""
    return entity_id.lower()


def _confidence(disposition: str, raw_confidence: float) -> tuple[str, float]:
    if disposition == Disposition.ASSERTED.value:
        return "EXTRACTED", 1.0
    return "INFERRED", round(float(raw_confidence), 3)


def build_export(store: GraphStore) -> dict[str, Any]:
    """Render the whole graph in graphify's node/edge/hyperedge shape."""
    nodes = []
    for row in store.entities():
        vault_id = store.vault_id_for(row["entity_id"])
        node = {
            "id": _node_id(row["entity_id"]),
            "label": row["canonical_name"],
            "entity_type": row["entity_type"],
            "source_file": None,   # this engine's entities are not vault notes
            "source_system": "jayos-graph-engine",
            "graph_engine_entity_id": row["entity_id"],
        }
        if vault_id:
            node["vault_id"] = vault_id
        prefix = vault_prefix_for(EntityType(row["entity_type"]))
        if prefix:
            node["vault_prefix_if_promoted"] = prefix
        nodes.append(node)

    edges = []
    for edge in store.all_relationships():
        category, score = _confidence(edge["disposition"], edge["confidence"])
        edges.append({
            "source": _node_id(edge["source_entity"]),
            "target": _node_id(edge["target_entity"]),
            "relation": edge["relationship_type"],
            "confidence": category,
            "confidence_score": score,
            "source_file": None,
            "source_system": "jayos-graph-engine",
            "source_reference": edge["source_reference"],
            "extraction_method": edge["extraction_method"],
            "observed_at": edge["observed_at"],
        })

    return {
        "directed": True,   # our edges ARE directional (Jay's own spec:
                            # "PROJECT-123 --SUBMITTED_TO--> JURISDICTION",
                            # "not simply PROJECT-123 <-> Scottsdale")
        "multigraph": True,  # two entities can carry more than one typed edge
        "graph": {
            "source_system": "jayos-graph-engine",
            "note": ("PCD business-operational graph (permits, projects, "
                     "clients, jurisdictions). Not vault-note content. "
                     "See graph/ARCHITECTURE.md."),
            "hyperedges": [],  # this engine has no hyperedge concept yet
        },
        "nodes": nodes,
        "edges": edges,
    }


def write_export(store: GraphStore, out_path: str | Path = DEFAULT_OUT) -> Path:
    path = Path(out_path)
    path.write_text(json.dumps(build_export(store), indent=2, sort_keys=True),
                    encoding="utf-8")
    return path
