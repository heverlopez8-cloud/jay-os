"""The authoritative watcher registry.

`JAYOS_PERMIT_WATCHERS` stopped health alerting about watchers nobody had
scheduled, but it introduced the opposite failure: schedule a watcher,
forget the variable, and health silently ignores a process that is
genuinely dead. An environment variable that has to be remembered is not a
safety mechanism.

So the checked-in manifest below is the source of truth. `scheduled_task`
names the Windows task that runs a watcher; `enabled` says whether it is
meant to be running right now. Health expects exactly the watchers that
are both scheduled and enabled, and `reconcile()` compares that against
what the scheduler actually holds, so drift is reported rather than
assumed away.
"""
from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class Watcher:
    name: str
    expect_every_minutes: int
    #: Windows Task Scheduler task that runs it, or None if nothing runs it.
    scheduled_task: str | None
    #: Whether that task is currently meant to be enabled.
    enabled: bool
    purpose: str


MANIFEST: tuple[Watcher, ...] = (
    Watcher("sla_sweep", 120, "JAY-OS Permit Sweep", False,
            "escalates the acknowledgement and completion clocks"),
    Watcher("notion_write", 1440, "JAY-OS Permit Sweep", False,
            "proves the Notion write path and the Projects database"),
    Watcher("notification_transport", 1440, "JAY-OS Permit Sweep", False,
            "proves the owner-notification transport"),
    Watcher("gmail_ingest", 30, None, False,
            "reads jurisdiction mail into the pipeline"),
    Watcher("portal_poll_scottsdale", 180, None, False,
            "reads Civic Access review outcomes"),
    Watcher("ack_poll", 120, None, False,
            "promotes NOTIFIED to ACKNOWLEDGED and COMPLETED"),
    # The dead-man needs its own pulse. Nothing local can alert on this one
    # -- a monitor cannot report its own death -- so it is stamped for the
    # n8n "JAY-OS Backup - missing heartbeat check" and for human review.
    Watcher("health_self", 240, "JAY-OS Permit Health", False,
            "the dead-man check itself; watched externally, not locally"),
)

BY_NAME: dict[str, Watcher] = {w.name: w for w in MANIFEST}


def expected_to_run() -> list[str]:
    """Watchers health should alert about: scheduled *and* enabled."""
    return [w.name for w in MANIFEST if w.scheduled_task and w.enabled]


def scheduled_tasks() -> dict[str, list[str]]:
    """Windows task name -> the watchers it is responsible for."""
    tasks: dict[str, list[str]] = {}
    for watcher in MANIFEST:
        if watcher.scheduled_task:
            tasks.setdefault(watcher.scheduled_task, []).append(watcher.name)
    return tasks


def reconcile(task_states: dict[str, bool]) -> list[str]:
    """Compare the manifest against what the scheduler actually holds.

    `task_states` maps Windows task name -> enabled. Returns a list of
    human-readable drift findings; empty means the manifest is honest.

    This is the check that catches the failure the env var created: a task
    running in the scheduler that the manifest thinks is off, so nothing
    would ever report it dead.
    """
    findings: list[str] = []
    for task, names in scheduled_tasks().items():
        actual = task_states.get(task)
        declared = any(BY_NAME[n].enabled for n in names)
        if actual is None:
            if declared:
                findings.append(
                    f"{task!r} is declared enabled in the manifest but is not "
                    f"registered with the scheduler; {', '.join(names)} would "
                    f"never be reported dead"
                )
            continue
        if actual and not declared:
            findings.append(
                f"{task!r} is ENABLED in the scheduler but the manifest marks "
                f"{', '.join(names)} disabled; health is ignoring a running "
                f"watcher"
            )
        elif declared and not actual:
            findings.append(
                f"{task!r} is DISABLED in the scheduler but the manifest marks "
                f"{', '.join(names)} enabled; health will alert about a "
                f"watcher nobody is running"
            )
    for task in task_states:
        if task not in scheduled_tasks() and task.startswith("JAY-OS Permit"):
            findings.append(
                f"{task!r} is registered with the scheduler but appears in no "
                f"manifest entry; whatever it runs is unmonitored"
            )
    return findings


def override_from_env(configured: str | None) -> list[str] | None:
    """Honour JAYOS_PERMIT_WATCHERS if set, but validate it against the manifest."""
    if not configured:
        return None
    names = [name.strip() for name in configured.split(",") if name.strip()]
    unknown = [name for name in names if name not in BY_NAME]
    if unknown:
        raise ValueError(
            f"unknown watcher(s) in JAYOS_PERMIT_WATCHERS: {unknown}. "
            f"Known watchers: {sorted(BY_NAME)}"
        )
    return names
