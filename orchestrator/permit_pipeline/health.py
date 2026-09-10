"""Dead-man check for the watchers themselves.

Every guarantee in this package assumes something is still running. A
watcher that dies quietly rebuilds the exact failure this project exists
to remove -- silence that looks like calm. So each watcher stamps a
heartbeat, and anything that stops stamping raises an alert to Jay.
"""
from __future__ import annotations

import datetime as dt

from .audit import AuditStore
from .model import Notification, UTC, now
from .ports import ConnectorError
from .routing import JAY

from .watchers import BY_NAME, expected_to_run, override_from_env

#: Cadence catalogue, derived from the checked-in watcher manifest.
EXPECTED: dict[str, int] = {
    name: watcher.expect_every_minutes for name, watcher in BY_NAME.items()
}


def active_watchers() -> list[str]:
    """The watchers health should alert about.

    The checked-in manifest decides this, so scheduling a watcher and
    forgetting an environment variable cannot leave a dead process
    unmonitored. `JAYOS_PERMIT_WATCHERS` remains as an override for
    one-off runs, and is validated against the manifest when present.
    """
    from . import config

    override = override_from_env(config.get("JAYOS_PERMIT_WATCHERS"))
    return override if override is not None else expected_to_run()


def record(store: AuditStore, watcher: str, ok: bool, detail: str = "") -> None:
    expect = EXPECTED.get(watcher, 60)
    if ok:
        store.heartbeat_ok(watcher, expect, detail)
    else:
        store.heartbeat_fail(watcher, detail, expect)


def report(store: AuditStore, at: dt.datetime | None = None,
           watchers: list[str] | None = None) -> list[dict]:
    """Current state of the watchers that are expected to be running."""
    moment = at or now()
    rows = {row["watcher"]: row for row in store.heartbeats()}
    names = watchers if watchers is not None else list(EXPECTED)
    out: list[dict] = []
    for watcher in names:
        expect = EXPECTED[watcher]
        row = rows.get(watcher)
        last_ok = _parse(row["last_ok_at"]) if row else None
        last_fail = _parse(row["last_fail_at"]) if row else None
        overdue_minutes = (
            (moment - last_ok).total_seconds() / 60 if last_ok else None
        )
        # Never having run is as bad as having stopped: either way nothing
        # is watching, and nobody has been told.
        stale = last_ok is None or overdue_minutes > expect
        # A watcher whose most recent outcome was a failure is not healthy,
        # however recently it last succeeded.
        failing = last_fail is not None and (last_ok is None or last_fail > last_ok)
        out.append({
            "watcher": watcher,
            "healthy": not (stale or failing),
            "last_ok_at": row["last_ok_at"] if row else None,
            "last_fail_at": row["last_fail_at"] if row else None,
            "detail": row["last_detail"] if row else "never run",
            "expect_every_minutes": expect,
            "minutes_since_ok": round(overdue_minutes, 1)
            if overdue_minutes is not None else None,
            "alerted_at": row["alerted_at"] if row else None,
        })
    return out


def sweep(store: AuditStore, notifier, at: dt.datetime | None = None,
          watchers: list[str] | None = None) -> list[str]:
    """Alert Jay about every expected watcher that has gone quiet."""
    alerted: list[str] = []
    stale = [entry for entry in report(store, at, watchers)
             if not entry["healthy"]]
    for entry in stale:
        if entry["alerted_at"]:
            continue  # already told; re-arms when the watcher recovers
        notification = Notification(
            channel="health",
            recipient=JAY.email,
            subject=f"[JAY-OS WATCHER DOWN] {entry['watcher']}",
            body=(
                f"A JAY-OS watcher has stopped checking in. While it is down, "
                f"events in its path are not being detected.\n\n"
                f"Watcher:        {entry['watcher']}\n"
                f"Expected every: {entry['expect_every_minutes']} minutes\n"
                f"Last success:   {entry['last_ok_at'] or 'never'}\n"
                f"Last failure:   {entry['last_fail_at'] or 'none recorded'}\n"
                f"Detail:         {entry['detail']}\n\n"
                f"Until this is running again, treat that channel as unwatched."
            ),
        )
        try:
            notifier.send(notification)
            store.record_notification("HEALTH", notification)
        except ConnectorError as error:
            store.log("HEALTH", "health_alert", "failed",
                      watcher=entry["watcher"], error=str(error))
            continue
        store.mark_heartbeat_alerted(entry["watcher"])
        store.log("HEALTH", "health_alert", "ok", watcher=entry["watcher"])
        alerted.append(entry["watcher"])
    return alerted


def _parse(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
