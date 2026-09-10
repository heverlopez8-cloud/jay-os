"""Append-only audit trail.

Every step of every event is written here before anything else is
believed. The point is not compliance theatre: it is that a failed
automation nobody knows about is worse than no automation, so the record
of what the system decided must survive the process that decided it.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any

from .model import UTC, as_utc, iso, now

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id   TEXT NOT NULL,
    at         TEXT NOT NULL,
    step       TEXT NOT NULL,
    outcome    TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS audit_log_event ON audit_log(event_id);

CREATE TABLE IF NOT EXISTS events (
    event_id     TEXT PRIMARY KEY,
    source       TEXT NOT NULL,
    external_id  TEXT NOT NULL,
    received_at  TEXT NOT NULL,
    subject      TEXT NOT NULL,
    sender       TEXT NOT NULL DEFAULT '',
    project_id   TEXT,
    project_name TEXT,
    event_class  TEXT,
    owner_key    TEXT,
    due_at       TEXT,
    state        TEXT NOT NULL,
    acknowledged_at TEXT,
    completed_at    TEXT,
    complete_by     TEXT,
    escalated_at    TEXT,
    escalation_level INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS portal_state (
    jurisdiction  TEXT NOT NULL,
    permit_number TEXT NOT NULL,
    status        TEXT,
    review_status TEXT,
    updated_at    TEXT,
    observed_at   TEXT NOT NULL,
    PRIMARY KEY (jurisdiction, permit_number)
);

CREATE TABLE IF NOT EXISTS heartbeats (
    watcher       TEXT PRIMARY KEY,
    last_ok_at    TEXT,
    last_fail_at  TEXT,
    last_detail   TEXT NOT NULL DEFAULT '',
    expect_every_minutes INTEGER NOT NULL DEFAULT 60,
    alerted_at    TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id  TEXT NOT NULL,
    channel   TEXT NOT NULL,
    recipient TEXT NOT NULL,
    subject   TEXT NOT NULL,
    body      TEXT NOT NULL,
    sent_at   TEXT,
    ok        INTEGER NOT NULL DEFAULT 0,
    detail    TEXT NOT NULL DEFAULT '',
    -- Why this row is not a delivery: 'sent', 'failed' or 'suppressed'.
    -- `ok=0` alone conflates "we chose not to send" with "we tried and
    -- could not", which is the distinction the ledger exists to keep and
    -- which any `WHERE ok=0` dashboard would otherwise report as an
    -- outage. Defaults to '' for rows written before this column existed.
    decision  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS exceptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT NOT NULL,
    code        TEXT NOT NULL,
    detail      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS exceptions_open ON exceptions(resolved_at);

-- Every notification DECISION, sent and withheld alike. A send that did not
-- happen has to be a row rather than a gap, or "we chose not to send" and
-- "we failed to send" become the same silence.
CREATE TABLE IF NOT EXISTS notification_ledger (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id         TEXT NOT NULL,
    channel          TEXT NOT NULL,
    project_id       TEXT NOT NULL DEFAULT '',
    event_class      TEXT NOT NULL DEFAULT '',
    recipient        TEXT NOT NULL,
    statement_digest TEXT NOT NULL,
    occurred_at      TEXT NOT NULL,
    decision         TEXT NOT NULL,
    suppressed_by    TEXT,
    recorded_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS notification_ledger_lookup
    ON notification_ledger(decision, channel, statement_digest);
"""


class AuditStore:
    def __init__(self, path: str | Path = "permit_events.db") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Add columns that older databases predate, without losing history."""
        existing = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(events)")
        }
        for column in ("completed_at", "complete_by"):
            if column not in existing:
                self._conn.execute(f"ALTER TABLE events ADD COLUMN {column} TEXT")
        notification_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(notifications)")
        }
        if "decision" not in notification_columns:
            self._conn.execute(
                "ALTER TABLE notifications ADD COLUMN decision TEXT"
                " NOT NULL DEFAULT ''"
            )

    def close(self) -> None:
        self._conn.close()

    # -- append-only trail -------------------------------------------------
    def log(
        self, event_id: str, step: str, outcome: str, **detail: Any
    ) -> None:
        self._conn.execute(
            "INSERT INTO audit_log(event_id, at, step, outcome, detail)"
            " VALUES (?,?,?,?,?)",
            (event_id, iso(now()), step, outcome, json.dumps(detail, default=str)),
        )
        self._conn.commit()

    def trail(self, event_id: str) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM audit_log WHERE event_id=? ORDER BY seq",
                (event_id,),
            )
        )

    # -- event state -------------------------------------------------------
    def seen(self, event_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM events WHERE event_id=?", (event_id,)
        ).fetchone()
        return row is not None

    def upsert_event(self, **fields: Any) -> None:
        columns = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        updates = ", ".join(f"{k}=excluded.{k}" for k in fields if k != "event_id")
        self._conn.execute(
            f"INSERT INTO events({columns}) VALUES ({placeholders})"
            f" ON CONFLICT(event_id) DO UPDATE SET {updates}",
            tuple(fields.values()),
        )
        self._conn.commit()

    def acknowledge(
        self, event_id: str, at: dt.datetime | None = None, how: str = "manual"
    ) -> None:
        """NOTIFIED -> ACKNOWLEDGED. The owner has seen it; it is not done."""
        stamp = iso(at or now())
        self._conn.execute(
            "UPDATE events SET acknowledged_at=?, state='acknowledged'"
            " WHERE event_id=? AND acknowledged_at IS NULL",
            (stamp, event_id),
        )
        self._conn.commit()
        self.log(event_id, "acknowledge", "ok", at=stamp, how=how)

    def complete(
        self, event_id: str, at: dt.datetime | None = None, how: str = "manual"
    ) -> None:
        """ACKNOWLEDGED -> COMPLETED. The work itself is finished."""
        stamp = iso(at or now())
        self._conn.execute(
            "UPDATE events SET completed_at=?, state='completed',"
            " acknowledged_at=COALESCE(acknowledged_at, ?) WHERE event_id=?",
            (stamp, stamp, event_id),
        )
        self._conn.commit()
        self.log(event_id, "complete", "ok", at=stamp, how=how)

    def record_escalation(self, event_id: str, level: int) -> None:
        self._conn.execute(
            "UPDATE events SET escalated_at=?, escalation_level=?,"
            " state='escalated' WHERE event_id=? AND completed_at IS NULL",
            (iso(now()), level, event_id),
        )
        self._conn.commit()

    def due_unacknowledged(self, at: dt.datetime | None = None) -> list[sqlite3.Row]:
        """Notified, past the acknowledgement clock, still unacknowledged."""
        cutoff = iso(at or now())
        return list(
            self._conn.execute(
                "SELECT * FROM events WHERE acknowledged_at IS NULL"
                " AND due_at IS NOT NULL AND due_at <= ?"
                " AND state NOT IN ('no_action','exception','completed','suppressed_duplicate')"
                " ORDER BY due_at",
                (cutoff,),
            )
        )

    def due_incomplete(self, at: dt.datetime | None = None) -> list[sqlite3.Row]:
        """Acknowledged, past the completion clock, still not done.

        This is the Riata case: somebody said they had it, and then it sat.
        """
        cutoff = iso(at or now())
        return list(
            self._conn.execute(
                "SELECT * FROM events WHERE acknowledged_at IS NOT NULL"
                " AND completed_at IS NULL AND complete_by IS NOT NULL"
                " AND complete_by <= ? AND state NOT IN"
                " ('no_action','exception','completed','suppressed_duplicate')"
                " ORDER BY complete_by",
                (cutoff,),
            )
        )

    def open_events(self) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM events WHERE state IN"
                " ('notified','acknowledged','escalated') ORDER BY received_at"
            )
        )

    # -- heartbeats / dead-man ---------------------------------------------
    def heartbeat_ok(self, watcher: str, expect_every_minutes: int,
                     detail: str = "") -> None:
        self._conn.execute(
            "INSERT INTO heartbeats(watcher, last_ok_at, last_detail,"
            " expect_every_minutes) VALUES (?,?,?,?)"
            " ON CONFLICT(watcher) DO UPDATE SET last_ok_at=excluded.last_ok_at,"
            " last_detail=excluded.last_detail,"
            " expect_every_minutes=excluded.expect_every_minutes, alerted_at=NULL",
            (watcher, iso(now()), detail, expect_every_minutes),
        )
        self._conn.commit()

    def heartbeat_fail(self, watcher: str, detail: str,
                       expect_every_minutes: int = 60) -> None:
        self._conn.execute(
            "INSERT INTO heartbeats(watcher, last_fail_at, last_detail,"
            " expect_every_minutes) VALUES (?,?,?,?)"
            " ON CONFLICT(watcher) DO UPDATE SET"
            " last_fail_at=excluded.last_fail_at, last_detail=excluded.last_detail",
            (watcher, iso(now()), detail, expect_every_minutes),
        )
        self._conn.commit()

    # -- portal state ------------------------------------------------------
    def portal_snapshot(self, jurisdiction: str, permit: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM portal_state WHERE jurisdiction=? AND permit_number=?",
            (jurisdiction, permit),
        ).fetchone()

    def save_portal_snapshot(self, jurisdiction: str, permit: str, status: str,
                             review_status: str, updated_at: str | None) -> None:
        self._conn.execute(
            "INSERT INTO portal_state(jurisdiction, permit_number, status,"
            " review_status, updated_at, observed_at) VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(jurisdiction, permit_number) DO UPDATE SET"
            " status=excluded.status, review_status=excluded.review_status,"
            " updated_at=excluded.updated_at, observed_at=excluded.observed_at",
            (jurisdiction, permit, status, review_status, updated_at, iso(now())),
        )
        self._conn.commit()

    def heartbeats(self) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT * FROM heartbeats ORDER BY watcher"))

    def mark_heartbeat_alerted(self, watcher: str) -> None:
        """Record that Jay has been told, creating the row if the watcher has
        never checked in -- otherwise a watcher that never started would
        re-alert on every sweep and train everyone to ignore the alerts."""
        self._conn.execute(
            "INSERT INTO heartbeats(watcher, last_detail, alerted_at)"
            " VALUES (?,?,?) ON CONFLICT(watcher) DO UPDATE SET"
            " alerted_at=excluded.alerted_at",
            (watcher, "never run", iso(now())),
        )
        self._conn.commit()

    # -- notifications and exceptions --------------------------------------
    def record_notification(self, event_id: str, notification: Any,
                            decision: str | None = None) -> None:
        """Record a delivery attempt. `decision` says why it is not a send.

        Callers that withhold a notification deliberately must pass
        `decision='suppressed'`; without it the row is indistinguishable
        from an SMTP failure, and every "undelivered notifications" query
        reports a choice as an outage.
        """
        self._conn.execute(
            "INSERT INTO notifications(event_id, channel, recipient, subject,"
            " body, sent_at, ok, detail, decision)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                event_id,
                notification.channel,
                notification.recipient,
                notification.subject,
                notification.body,
                iso(notification.sent_at) if notification.sent_at else None,
                1 if notification.ok else 0,
                notification.detail,
                decision or ("sent" if notification.ok else "failed"),
            ),
        )
        self._conn.commit()

    def record_exception(self, exception: Any) -> int:
        cursor = self._conn.execute(
            "INSERT INTO exceptions(event_id, code, detail, created_at)"
            " VALUES (?,?,?,?)",
            (
                exception.event_id,
                exception.code.value,
                exception.detail,
                iso(exception.created_at),
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def open_exceptions(self) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM exceptions WHERE resolved_at IS NULL"
                " ORDER BY created_at"
            )
        )

    def notifications_for(self, event_id: str) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM notifications WHERE event_id=? ORDER BY id",
                (event_id,),
            )
        )

    # -- notification decisions -------------------------------------------
    def identical_statement_sent(
        self, *, channel: str, project_id: str, event_class: str,
        recipient: str, statement_digest: str, occurred_at: dt.datetime,
        window_minutes: int, exclude_event_id: str,
    ) -> sqlite3.Row | None:
        """The row that already told this person this exact thing, or None.

        Only `decision='sent'` rows anchor, so a suppression can never itself
        suppress: N identical copies cannot chain into a single delivery.

        The time term is compared in Python on parsed datetimes rather than
        on ISO strings, and *absolutely* rather than directionally, so the
        answer does not depend on the order ingest happened to read the
        mailbox in -- whichever copy is processed first is the one that sends.
        """
        rows = self._conn.execute(
            "SELECT * FROM notification_ledger WHERE decision='sent'"
            " AND channel=? AND statement_digest=? AND project_id=?"
            " AND event_class=? AND recipient=? AND event_id<>?"
            " ORDER BY occurred_at",
            (channel, statement_digest, project_id, event_class, recipient,
             exclude_event_id),
        ).fetchall()
        limit = dt.timedelta(minutes=window_minutes)
        # Both sides normalized: producers other than Gmail supply
        # `occurred_at`, and one naive value raises TypeError out of the
        # subtraction below -- which `__main__` does not catch, so it kills
        # the run mid-batch rather than failing this one comparison.
        occurred_at = as_utc(occurred_at)
        for row in rows:
            stamp = as_utc(dt.datetime.fromisoformat(row["occurred_at"]))
            if abs(stamp - occurred_at) <= limit:
                return row
        return None

    def record_notification_decision(
        self, *, event_id: str, channel: str, project_id: str,
        event_class: str, recipient: str, statement_digest: str,
        occurred_at: dt.datetime, decision: str,
        suppressed_by: str | None = None,
    ) -> None:
        """Every send and every non-send, as a first-class countable row."""
        self._conn.execute(
            "INSERT INTO notification_ledger(event_id, channel, project_id,"
            " event_class, recipient, statement_digest, occurred_at,"
            " decision, suppressed_by, recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (event_id, channel, project_id, event_class, recipient,
             statement_digest, iso(occurred_at), decision, suppressed_by,
             iso(now())),
        )
        self._conn.commit()

    def suppressed_recent(self, hours: int = 24) -> list[sqlite3.Row]:
        """Withheld sends, for the operator surface.

        A decision nobody is ever shown is bookkeeping, not accountability.
        """
        cutoff = iso(now() - dt.timedelta(hours=hours))
        return list(self._conn.execute(
            "SELECT l.*, e.project_name, e.subject FROM notification_ledger l"
            " LEFT JOIN events e ON e.event_id = l.event_id"
            " WHERE l.decision='suppressed' AND l.recorded_at >= ?"
            " ORDER BY l.recorded_at", (cutoff,)))
