"""Shadow ingest: watch the mail, decide nothing.

Live ingest cannot be trusted until project identification is good enough,
and it cannot get good enough without measuring it against real mail. So
shadow mode runs the *real* matcher and classifier over real messages and
records what it would have done -- proposed project, confidence, owner,
next action -- while writing nothing to Notion and notifying nobody.

The guarantee is structural, not a promise: the ports handed to the
pipeline in shadow mode physically cannot write or send, and a test asserts
that a shadow run leaves both empty even when every message matches.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any

from . import classify as classify_mod
from . import matching
from .model import EventClass, ProjectRecord, RawEvent, iso, now
from .ports import ConnectorError
from .routing import assign
from .sources import from_gmail_message

_SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_matches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at   TEXT NOT NULL,
    event_id      TEXT NOT NULL,
    external_id   TEXT NOT NULL,
    received_at   TEXT,
    sender        TEXT,
    subject       TEXT,
    verdict       TEXT NOT NULL,
    project_id    TEXT,
    project_name  TEXT,
    match_basis   TEXT,
    match_confidence REAL,
    candidates    TEXT,
    event_class   TEXT,
    class_confidence REAL,
    proposed_owner TEXT,
    proposed_action TEXT,
    review        TEXT,
    reviewed_at   TEXT,
    UNIQUE(event_id)
);
CREATE INDEX IF NOT EXISTS shadow_verdict ON shadow_matches(verdict);
"""

#: Verdicts. Only `assigned` would have been acted on automatically.
ASSIGNED = "assigned"
REVIEW_AMBIGUOUS = "review_ambiguous"
REVIEW_UNMATCHED = "review_unmatched"
REVIEW_UNCLASSIFIED = "review_unclassified"
NO_ACTION = "no_action"


class ShadowStore:
    def __init__(self, path: str | Path) -> None:
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def record(self, **fields: Any) -> bool:
        """Insert one observation. Returns False if already seen (idempotent)."""
        columns = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        try:
            self._conn.execute(
                f"INSERT INTO shadow_matches({columns}) VALUES ({placeholders})",
                tuple(fields.values()),
            )
        except sqlite3.IntegrityError:
            return False
        self._conn.commit()
        return True

    def counts(self) -> dict[str, int]:
        return {
            row["verdict"]: row["n"] for row in self._conn.execute(
                "SELECT verdict, COUNT(*) AS n FROM shadow_matches"
                " GROUP BY verdict")
        }

    def queue(self, verdict: str, limit: int = 50) -> list[sqlite3.Row]:
        return list(self._conn.execute(
            "SELECT * FROM shadow_matches WHERE verdict=? AND reviewed_at IS NULL"
            " ORDER BY received_at DESC LIMIT ?", (verdict, limit)))

    def assigned(self, limit: int = 200) -> list[sqlite3.Row]:
        return list(self._conn.execute(
            "SELECT * FROM shadow_matches WHERE verdict=? ORDER BY received_at"
            " DESC LIMIT ?", (ASSIGNED, limit)))

    def mark_review(self, event_id: str, verdict_was_correct: bool,
                    note: str = "") -> None:
        """Human judgement on one observation, so precision can be measured."""
        self._conn.execute(
            "UPDATE shadow_matches SET review=?, reviewed_at=? WHERE event_id=?",
            ("correct" if verdict_was_correct else f"wrong:{note}",
             iso(now()), event_id),
        )
        self._conn.commit()

    def scorecard(self) -> dict[str, Any]:
        """Precision measured only over observations a human has judged."""
        reviewed = list(self._conn.execute(
            "SELECT verdict, review FROM shadow_matches"
            " WHERE reviewed_at IS NOT NULL"))
        auto = [r for r in reviewed if r["verdict"] == ASSIGNED]
        correct = [r for r in auto if r["review"] == "correct"]
        counts = self.counts()
        actionable = sum(v for k, v in counts.items() if k != NO_ACTION)
        return {
            "observations": sum(counts.values()),
            "by_verdict": counts,
            "auto_assign_rate": (counts.get(ASSIGNED, 0) / actionable
                                 if actionable else 0.0),
            "reviewed": len(reviewed),
            "auto_reviewed": len(auto),
            "precision": (len(correct) / len(auto)) if auto else None,
        }


def observe(messages: list[dict], projects: list[ProjectRecord],
            store: ShadowStore, clients: list[str] | None = None) -> dict:
    """Run the real decision logic over real mail, changing nothing."""
    summary = {"seen": len(messages), "non_operational": 0, "new": 0,
               "duplicate": 0}

    for message in messages:
        raw = from_gmail_message(message, clients or [])
        if raw is None:
            summary["non_operational"] += 1
            continue

        match = matching.match_project(raw.text, projects)
        classification = classify_mod.classify(raw.text)
        verdict, owner, action = _judge(raw, match, classification)

        created = store.record(
            observed_at=iso(now()), event_id=raw.event_id,
            external_id=raw.external_id, received_at=iso(raw.received_at),
            sender=raw.sender, subject=raw.subject[:200], verdict=verdict,
            project_id=match.project.page_id if match.project else None,
            project_name=match.project.name if match.project else None,
            match_basis=match.basis, match_confidence=match.confidence,
            candidates=json.dumps(list(match.candidates)),
            event_class=classification.event_class.value,
            class_confidence=classification.confidence,
            proposed_owner=owner, proposed_action=action,
        )
        summary["new" if created else "duplicate"] += 1

    return summary


def _judge(raw: RawEvent, match, classification) -> tuple[str, str | None, str | None]:
    if classification.event_class in (EventClass.ACKNOWLEDGEMENT,):
        return NO_ACTION, None, None
    if not matching.is_confident(match):
        return (REVIEW_AMBIGUOUS if match.candidates else REVIEW_UNMATCHED,
                None, None)
    if not classify_mod.is_confident(classification):
        return REVIEW_UNCLASSIFIED, None, None
    permits = matching.extract_permit_numbers(raw.text)
    assignment = assign(
        classification.event_class, match.project, raw.received_at,
        jurisdiction=match.project.jurisdiction or "",
        permit=permits[0] if permits else "",
    )
    if assignment is None:
        return REVIEW_UNCLASSIFIED, None, None
    return ASSIGNED, assignment.owner.name, assignment.next_action[:400]


def render(store: ShadowStore) -> str:
    card = store.scorecard()
    lines = ["=" * 74,
             "SHADOW INGEST  (no Notion writes, no notifications, ever)",
             "=" * 74,
             f"observations        : {card['observations']}"]
    for verdict, count in sorted(card["by_verdict"].items(),
                                 key=lambda kv: -kv[1]):
        lines.append(f"  {verdict:<22} {count}")
    lines.append("")
    lines.append(f"auto-assign rate    : {card['auto_assign_rate']:.1%} "
                 f"(of actionable messages)")
    precision = card["precision"]
    lines.append(f"precision (reviewed): "
                 + (f"{precision:.1%} over {card['auto_reviewed']} judged"
                    if precision is not None
                    else "not measurable yet - nothing reviewed"))
    lines.append("")
    lines.append("GATE: 95% recall on actionable messages, 99% precision on")
    lines.append("automatic assignment. Below threshold goes to human review.")
    for label, verdict in (("AMBIGUOUS", REVIEW_AMBIGUOUS),
                           ("UNMATCHED", REVIEW_UNMATCHED),
                           ("UNCLASSIFIED", REVIEW_UNCLASSIFIED)):
        rows = store.queue(verdict, limit=8)
        if rows:
            lines.append("")
            lines.append(f"{label} REVIEW QUEUE ({len(rows)} shown)")
            for row in rows:
                lines.append(f"  {str(row['received_at'])[:10]}  "
                             f"{str(row['subject'])[:62]}")
    return "\n".join(lines)
