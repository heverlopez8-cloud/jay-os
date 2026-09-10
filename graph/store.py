"""The graph store. Append-only by construction, with no delete API.

WHY THERE IS NO `delete()`
--------------------------
Governance rule: do not delete or rewrite existing production knowledge. The
honest way to honour that is not discipline, it is the absence of a method.
Nothing in this module issues DELETE. A relationship that turns out to be
wrong is RETRACTED -- a retraction row is inserted and the edge's status flips
to `retracted` -- so the original claim, its source and the fact that someone
disagreed all survive. An entity that merges away becomes SUPERSEDED and keeps
its row.

WHY PROVENANCE IS A CONSTRUCTOR ARGUMENT, NOT AN OPTION
-------------------------------------------------------
Every relationship must carry source, timestamp, confidence and extraction
method. `relate()` raises if any are missing rather than defaulting them,
because a default provenance is a lie that cannot be audited later. The
cheap failure (refuse the edge) is preferred over the expensive one (an
unattributable edge teaching the lesson engine something).

WHY IDEMPOTENCY LIVES IN THE SCHEMA
-----------------------------------
`UNIQUE (source_entity, relationship_type, target_entity, source_reference)`
is what makes running the pipeline twice a no-op, the same way
`email_events.gmail_message_id` is what makes the sentinel's dedupe real.
Memory is not a guarantee; a constraint is.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .contract import (Disposition, EntityStatus, EntityType,
                       ENGINE_WRITABLE_LESSON_STATUSES, LessonStatus,
                       OWNER_ONLY_LESSON_STATUSES, RelationType,
                       disposition_for, validate_relationship)
from .ids import entity_id as make_entity_id, event_id as make_event_id, lesson_id as make_lesson_id

UTC = dt.timezone.utc


def now_iso() -> str:
    return dt.datetime.now(UTC).replace(microsecond=0).isoformat()


class StoreError(RuntimeError):
    """The store refused an operation."""


class ProvenanceError(StoreError):
    """An inferred fact arrived without the provenance that makes it auditable."""


class GovernanceError(StoreError):
    """Something tried to promote a lesson without owner authority."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    entity_id       TEXT PRIMARY KEY,
    entity_type     TEXT NOT NULL,
    canonical_name  TEXT NOT NULL,
    canonical_key   TEXT NOT NULL,
    external_id     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    confidence      REAL NOT NULL,
    status          TEXT NOT NULL,
    attributes      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS entities_type ON entities(entity_type);
CREATE INDEX IF NOT EXISTS entities_key  ON entities(entity_type, canonical_key);

CREATE TABLE IF NOT EXISTS entity_aliases (
    entity_id    TEXT NOT NULL,
    alias        TEXT NOT NULL,
    alias_key    TEXT NOT NULL,
    source       TEXT NOT NULL,
    recorded_at  TEXT NOT NULL,
    UNIQUE (entity_id, alias_key)
);
CREATE INDEX IF NOT EXISTS alias_lookup ON entity_aliases(alias_key);

CREATE TABLE IF NOT EXISTS entity_source_refs (
    entity_id    TEXT NOT NULL,
    source       TEXT NOT NULL,
    source_ref   TEXT NOT NULL,
    observed_at  TEXT NOT NULL,
    recorded_at  TEXT NOT NULL,
    UNIQUE (entity_id, source, source_ref)
);

CREATE TABLE IF NOT EXISTS relationships (
    rel_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source_entity      TEXT NOT NULL,
    relationship_type  TEXT NOT NULL,
    target_entity      TEXT NOT NULL,
    source_reference   TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    observed_at        TEXT NOT NULL,
    confidence         REAL NOT NULL,
    extraction_method  TEXT NOT NULL,
    disposition        TEXT NOT NULL,
    provenance         TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'active',
    UNIQUE (source_entity, relationship_type, target_entity, source_reference)
);
CREATE INDEX IF NOT EXISTS rel_out ON relationships(source_entity, status);
CREATE INDEX IF NOT EXISTS rel_in  ON relationships(target_entity, status);

CREATE TABLE IF NOT EXISTS relationship_retractions (
    rel_id      INTEGER NOT NULL,
    at          TEXT NOT NULL,
    actor       TEXT NOT NULL,
    reason      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_merges (
    left_entity   TEXT NOT NULL,
    right_entity  TEXT NOT NULL,
    confidence    REAL NOT NULL,
    basis         TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'OPEN',
    UNIQUE (left_entity, right_entity)
);

CREATE TABLE IF NOT EXISTS events (
    event_id     TEXT PRIMARY KEY,
    timestamp    TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    source       TEXT NOT NULL,
    action       TEXT NOT NULL DEFAULT '',
    decision     TEXT NOT NULL DEFAULT '',
    result       TEXT NOT NULL DEFAULT '',
    actor        TEXT NOT NULL,
    confidence   REAL,
    provenance   TEXT NOT NULL DEFAULT '{}',
    entity_ids   TEXT NOT NULL DEFAULT '[]',
    recorded_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_type ON events(event_type);

CREATE TABLE IF NOT EXISTS lessons (
    lesson_id         TEXT PRIMARY KEY,
    observation       TEXT NOT NULL,
    supporting_events TEXT NOT NULL DEFAULT '[]',
    pattern           TEXT NOT NULL,
    proposed_rule     TEXT NOT NULL,
    expected_impact   TEXT NOT NULL DEFAULT '',
    confidence        REAL NOT NULL,
    status            TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE (pattern)
);

CREATE TABLE IF NOT EXISTS lesson_status_history (
    lesson_id   TEXT NOT NULL,
    from_status TEXT NOT NULL,
    to_status   TEXT NOT NULL,
    at          TEXT NOT NULL,
    actor       TEXT NOT NULL,
    approval    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS owner_overrides (
    override_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_proposal  TEXT NOT NULL,
    owner_decision  TEXT NOT NULL,
    reason_if_known TEXT NOT NULL DEFAULT '',
    context         TEXT NOT NULL DEFAULT '',
    result          TEXT NOT NULL DEFAULT '',
    timestamp       TEXT NOT NULL,
    recorded_at     TEXT NOT NULL,
    entity_ids      TEXT NOT NULL DEFAULT '[]'
);
"""


class GraphStore:
    """Append-only graph storage. Open it, write to it, never delete from it."""

    def __init__(self, path: str | Path = "graph/jayos-graph.sqlite3") -> None:
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.path))
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "GraphStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ---------------------------------------------------------------- entities

    def upsert_entity(self, entity_type: EntityType, canonical_name: str, *,
                      key: str | None = None, external_id: str | None = None,
                      source: str, source_ref: str,
                      observed_at: str | None = None,
                      confidence: float = 1.0,
                      status: EntityStatus = EntityStatus.ACTIVE,
                      attributes: dict[str, Any] | None = None) -> str:
        """Create or refresh a canonical entity. Idempotent on (type, key).

        Attributes MERGE. An ingest that knows less than a previous one must
        never blank a field that was already populated, so absent keys are
        left alone rather than overwritten with None.
        """
        natural_key = key or canonical_name
        eid = make_entity_id(entity_type, natural_key, external_id)
        stamp = now_iso()
        observed = observed_at or stamp
        existing = self._db.execute(
            "SELECT entity_id, attributes, confidence FROM entities WHERE entity_id = ?",
            (eid,)).fetchone()
        if existing is None:
            self._db.execute(
                "INSERT INTO entities (entity_id, entity_type, canonical_name, "
                "canonical_key, external_id, created_at, updated_at, confidence, "
                "status, attributes) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (eid, entity_type.value, canonical_name, str(natural_key).upper().strip(),
                 external_id, stamp, stamp, float(confidence), status.value,
                 json.dumps(attributes or {}, sort_keys=True)))
        else:
            merged = json.loads(existing["attributes"])
            for field, value in (attributes or {}).items():
                if value not in (None, "", [], {}):
                    merged[field] = value
            self._db.execute(
                "UPDATE entities SET canonical_name = ?, updated_at = ?, "
                "confidence = MAX(confidence, ?), attributes = ? WHERE entity_id = ?",
                (canonical_name or existing["entity_id"], stamp, float(confidence),
                 json.dumps(merged, sort_keys=True), eid))
        self.add_source_ref(eid, source, source_ref, observed)
        self._db.commit()
        return eid

    def add_alias(self, entity_id: str, alias: str, source: str) -> bool:
        """Record another name for the same thing. Returns False if already known."""
        text = (alias or "").strip()
        if not text:
            return False
        key = " ".join(text.upper().split())
        cursor = self._db.execute(
            "INSERT OR IGNORE INTO entity_aliases (entity_id, alias, alias_key, "
            "source, recorded_at) VALUES (?,?,?,?,?)",
            (entity_id, text, key, source, now_iso()))
        self._db.commit()
        return cursor.rowcount > 0

    def add_source_ref(self, entity_id: str, source: str, source_ref: str,
                       observed_at: str) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO entity_source_refs (entity_id, source, "
            "source_ref, observed_at, recorded_at) VALUES (?,?,?,?,?)",
            (entity_id, source, source_ref, observed_at, now_iso()))

    def entity(self, entity_id: str) -> sqlite3.Row | None:
        return self._db.execute(
            "SELECT * FROM entities WHERE entity_id = ?", (entity_id,)).fetchone()

    def entities(self, entity_type: EntityType | None = None) -> list[sqlite3.Row]:
        if entity_type is None:
            return self._db.execute(
                "SELECT * FROM entities ORDER BY entity_type, canonical_name").fetchall()
        return self._db.execute(
            "SELECT * FROM entities WHERE entity_type = ? ORDER BY canonical_name",
            (entity_type.value,)).fetchall()

    def aliases(self, entity_id: str) -> list[str]:
        return [r["alias"] for r in self._db.execute(
            "SELECT alias FROM entity_aliases WHERE entity_id = ? ORDER BY alias",
            (entity_id,)).fetchall()]

    def source_refs(self, entity_id: str) -> list[sqlite3.Row]:
        return self._db.execute(
            "SELECT * FROM entity_source_refs WHERE entity_id = ? ORDER BY observed_at",
            (entity_id,)).fetchall()

    def find_by_key(self, entity_type: EntityType, key: str) -> sqlite3.Row | None:
        return self._db.execute(
            "SELECT * FROM entities WHERE entity_type = ? AND canonical_key = ?",
            (entity_type.value, " ".join(str(key).upper().split()))).fetchone()

    def find_by_alias(self, entity_type: EntityType, alias: str) -> sqlite3.Row | None:
        key = " ".join(str(alias).upper().split())
        return self._db.execute(
            "SELECT e.* FROM entities e JOIN entity_aliases a "
            "ON a.entity_id = e.entity_id WHERE e.entity_type = ? AND a.alias_key = ?",
            (entity_type.value, key)).fetchone()

    def supersede_entity(self, entity_id: str, merged_into: str, actor: str) -> None:
        """Mark an entity merged away. The row stays; nothing is deleted."""
        attrs = self.entity(entity_id)
        if attrs is None:
            raise StoreError(f"unknown entity {entity_id}")
        merged = json.loads(attrs["attributes"])
        merged["merged_into"] = merged_into
        merged["merged_by"] = actor
        merged["merged_at"] = now_iso()
        self._db.execute(
            "UPDATE entities SET status = ?, updated_at = ?, attributes = ? "
            "WHERE entity_id = ?",
            (EntityStatus.SUPERSEDED.value, now_iso(),
             json.dumps(merged, sort_keys=True), entity_id))
        self._db.commit()

    # ----------------------------------------------------------- relationships

    def relate(self, source_entity: str, predicate: RelationType,
               target_entity: str, *, source_reference: str,
               confidence: float, extraction_method: str,
               observed_at: str | None = None,
               provenance: dict[str, Any] | None = None,
               asserted: bool = False) -> tuple[int, Disposition, bool]:
        """Record a typed edge. Returns (rel_id, disposition, created).

        Refuses, rather than guesses, when:
          * either endpoint does not exist (no phantom nodes),
          * the predicate does not accept those endpoint types,
          * provenance is incomplete.
        """
        if not source_reference or not str(source_reference).strip():
            raise ProvenanceError(
                f"{predicate.value} edge has no source_reference. An edge "
                f"nobody can trace back is not evidence.")
        if not extraction_method or not str(extraction_method).strip():
            raise ProvenanceError(
                f"{predicate.value} edge has no extraction_method. How it was "
                f"inferred is part of whether it can be trusted.")
        if confidence is None or not (0.0 <= float(confidence) <= 1.0):
            raise ProvenanceError(
                f"{predicate.value} edge needs a confidence in [0,1], got {confidence!r}")

        subject = self.entity(source_entity)
        target = self.entity(target_entity)
        if subject is None or target is None:
            missing = source_entity if subject is None else target_entity
            raise StoreError(
                f"refusing to create {predicate.value} against unknown entity "
                f"{missing}. Create the entity first so it has provenance too.")

        subject_type = EntityType(subject["entity_type"])
        target_type = EntityType(target["entity_type"])
        validate_relationship(subject_type, predicate, target_type)

        stamp = now_iso()
        observed = observed_at or stamp
        disposition = disposition_for(float(confidence), asserted=asserted)
        record = dict(provenance or {})
        record.setdefault("source", source_reference)
        record.setdefault("extraction_method", extraction_method)
        record.setdefault("observed_at", observed)
        record.setdefault("recorded_at", stamp)
        record.setdefault("subject_type", subject_type.value)
        record.setdefault("object_type", target_type.value)

        cursor = self._db.execute(
            "INSERT OR IGNORE INTO relationships (source_entity, relationship_type, "
            "target_entity, source_reference, created_at, observed_at, confidence, "
            "extraction_method, disposition, provenance) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (source_entity, predicate.value, target_entity, source_reference, stamp,
             observed, float(confidence), extraction_method, disposition.value,
             json.dumps(record, sort_keys=True)))
        created = cursor.rowcount > 0
        self._db.commit()
        row = self._db.execute(
            "SELECT rel_id, disposition FROM relationships WHERE source_entity = ? "
            "AND relationship_type = ? AND target_entity = ? AND source_reference = ?",
            (source_entity, predicate.value, target_entity, source_reference)).fetchone()
        return int(row["rel_id"]), Disposition(row["disposition"]), created

    def retract_relationship(self, rel_id: int, *, reason: str, actor: str) -> None:
        """Disagree with an edge without destroying the record of it."""
        if not self._db.execute("SELECT 1 FROM relationships WHERE rel_id = ?",
                                (rel_id,)).fetchone():
            raise StoreError(f"unknown relationship {rel_id}")
        self._db.execute(
            "INSERT INTO relationship_retractions (rel_id, at, actor, reason) "
            "VALUES (?,?,?,?)", (rel_id, now_iso(), actor, reason))
        self._db.execute(
            "UPDATE relationships SET status = 'retracted' WHERE rel_id = ?", (rel_id,))
        self._db.commit()

    def relationships_from(self, entity_id: str, active_only: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM relationships WHERE source_entity = ?"
        if active_only:
            sql += " AND status = 'active'"
        return self._db.execute(sql + " ORDER BY relationship_type", (entity_id,)).fetchall()

    def relationships_to(self, entity_id: str, active_only: bool = True) -> list[sqlite3.Row]:
        """Inbound edges. These are the backlinks."""
        sql = "SELECT * FROM relationships WHERE target_entity = ?"
        if active_only:
            sql += " AND status = 'active'"
        return self._db.execute(sql + " ORDER BY relationship_type", (entity_id,)).fetchall()

    def all_relationships(self, active_only: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM relationships"
        if active_only:
            sql += " WHERE status = 'active'"
        return self._db.execute(sql + " ORDER BY rel_id").fetchall()

    # -------------------------------------------------------- candidate merges

    def add_candidate_merge(self, left: str, right: str, confidence: float,
                            basis: str) -> bool:
        """Two entities MIGHT be the same. Never merged automatically."""
        pair = tuple(sorted((left, right)))
        cursor = self._db.execute(
            "INSERT OR IGNORE INTO candidate_merges (left_entity, right_entity, "
            "confidence, basis, created_at) VALUES (?,?,?,?,?)",
            (pair[0], pair[1], float(confidence), basis, now_iso()))
        self._db.commit()
        return cursor.rowcount > 0

    def candidate_merges(self, status: str = "OPEN") -> list[sqlite3.Row]:
        return self._db.execute(
            "SELECT * FROM candidate_merges WHERE status = ? ORDER BY confidence DESC",
            (status,)).fetchall()

    # ------------------------------------------------------------ event ledger

    def record_event(self, *, event_type: str, source: str, actor: str,
                     action: str = "", decision: str = "", result: str = "",
                     confidence: float | None = None,
                     provenance: dict[str, Any] | None = None,
                     entity_ids: Iterable[str] = (),
                     timestamp: str | None = None) -> str:
        stamp = timestamp or now_iso()
        sequence = int(self._db.execute(
            "SELECT COUNT(*) FROM events WHERE substr(timestamp,1,10) = ?",
            (stamp[:10],)).fetchone()[0]) + 1
        eid = make_event_id(stamp, sequence)
        while self._db.execute("SELECT 1 FROM events WHERE event_id = ?", (eid,)).fetchone():
            sequence += 1
            eid = make_event_id(stamp, sequence)
        self._db.execute(
            "INSERT INTO events (event_id, timestamp, event_type, source, action, "
            "decision, result, actor, confidence, provenance, entity_ids, recorded_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, stamp, event_type, source, action, decision, result, actor,
             confidence, json.dumps(provenance or {}, sort_keys=True),
             json.dumps(list(entity_ids)), now_iso()))
        self._db.commit()
        return eid

    def events(self, event_type: str | None = None) -> list[sqlite3.Row]:
        if event_type is None:
            return self._db.execute("SELECT * FROM events ORDER BY timestamp").fetchall()
        return self._db.execute(
            "SELECT * FROM events WHERE event_type = ? ORDER BY timestamp",
            (event_type,)).fetchall()

    # ----------------------------------------------------------------- lessons

    def propose_lesson(self, *, observation: str, pattern: str,
                       proposed_rule: str, confidence: float,
                       supporting_events: Iterable[str] = (),
                       expected_impact: str = "",
                       status: LessonStatus = LessonStatus.CANDIDATE) -> str:
        """Record a discovered pattern. CANDIDATE at most -- never ACTIVE.

        The hard rule: the engine may DISCOVER a lesson. It may not promote
        one. Passing an owner-only status here raises.
        """
        if status not in ENGINE_WRITABLE_LESSON_STATUSES:
            raise GovernanceError(
                f"the lesson engine may only write "
                f"{sorted(s.value for s in ENGINE_WRITABLE_LESSON_STATUSES)}; "
                f"{status.value} is a governance decision reserved for Jay.")
        existing = self._db.execute(
            "SELECT lesson_id FROM lessons WHERE pattern = ?", (pattern,)).fetchone()
        stamp = now_iso()
        if existing:
            self._db.execute(
                "UPDATE lessons SET observation = ?, proposed_rule = ?, "
                "confidence = ?, supporting_events = ?, updated_at = ? "
                "WHERE lesson_id = ?",
                (observation, proposed_rule, float(confidence),
                 json.dumps(sorted(set(supporting_events))), stamp,
                 existing["lesson_id"]))
            self._db.commit()
            return str(existing["lesson_id"])
        sequence = int(self._db.execute("SELECT COUNT(*) FROM lessons").fetchone()[0]) + 1
        lid = make_lesson_id(sequence)
        self._db.execute(
            "INSERT INTO lessons (lesson_id, observation, supporting_events, pattern, "
            "proposed_rule, expected_impact, confidence, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (lid, observation, json.dumps(sorted(set(supporting_events))), pattern,
             proposed_rule, expected_impact, float(confidence), status.value,
             stamp, stamp))
        self._db.execute(
            "INSERT INTO lesson_status_history (lesson_id, from_status, to_status, "
            "at, actor) VALUES (?,?,?,?,?)", (lid, "", status.value, stamp, "graph-engine"))
        self._db.commit()
        return lid

    def set_lesson_status(self, lesson_id: str, status: LessonStatus, *,
                          actor: str, owner_approval: str = "") -> None:
        """Move a lesson along. Owner-only statuses require explicit approval.

        `owner_approval` is not a formality. Without it this raises, which is
        the mechanism that keeps a discovered pattern from becoming a
        production rule on its own.
        """
        row = self._db.execute(
            "SELECT status FROM lessons WHERE lesson_id = ?", (lesson_id,)).fetchone()
        if row is None:
            raise StoreError(f"unknown lesson {lesson_id}")
        if status in OWNER_ONLY_LESSON_STATUSES and not owner_approval.strip():
            raise GovernanceError(
                f"promoting {lesson_id} to {status.value} requires Jay's explicit "
                f"approval. No agent may grant itself this.")
        stamp = now_iso()
        self._db.execute(
            "UPDATE lessons SET status = ?, updated_at = ? WHERE lesson_id = ?",
            (status.value, stamp, lesson_id))
        self._db.execute(
            "INSERT INTO lesson_status_history (lesson_id, from_status, to_status, "
            "at, actor, approval) VALUES (?,?,?,?,?,?)",
            (lesson_id, row["status"], status.value, stamp, actor, owner_approval))
        self._db.commit()

    def lessons(self, status: LessonStatus | None = None) -> list[sqlite3.Row]:
        if status is None:
            return self._db.execute(
                "SELECT * FROM lessons ORDER BY confidence DESC").fetchall()
        return self._db.execute(
            "SELECT * FROM lessons WHERE status = ? ORDER BY confidence DESC",
            (status.value,)).fetchall()

    # --------------------------------------------------------- owner overrides

    def record_override(self, *, agent_proposal: str, owner_decision: str,
                        reason_if_known: str = "", context: str = "",
                        result: str = "", timestamp: str | None = None,
                        entity_ids: Iterable[str] = ()) -> int:
        """Capture a correction as evidence. One override is not a rule."""
        cursor = self._db.execute(
            "INSERT INTO owner_overrides (agent_proposal, owner_decision, "
            "reason_if_known, context, result, timestamp, recorded_at, entity_ids) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (agent_proposal, owner_decision, reason_if_known, context, result,
             timestamp or now_iso(), now_iso(), json.dumps(list(entity_ids))))
        self._db.commit()
        return int(cursor.lastrowid)

    def overrides(self) -> list[sqlite3.Row]:
        return self._db.execute(
            "SELECT * FROM owner_overrides ORDER BY timestamp").fetchall()

    # ------------------------------------------------------------------ counts

    def count(self, table: str) -> int:
        allowed = {"entities", "relationships", "events", "lessons",
                   "candidate_merges", "owner_overrides", "entity_aliases",
                   "relationship_retractions", "entity_source_refs"}
        if table not in allowed:
            raise StoreError(f"{table} is not a countable table")
        return int(self._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def execute(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        """Read-only escape hatch for health metrics. SELECT only."""
        if not sql.lstrip().upper().startswith("SELECT"):
            raise StoreError("GraphStore.execute is for SELECT only")
        return self._db.execute(sql, params).fetchall()
