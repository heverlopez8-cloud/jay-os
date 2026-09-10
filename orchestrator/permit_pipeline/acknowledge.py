"""Distinguish NOTIFIED from ACKNOWLEDGED from COMPLETED.

A notification is not an acknowledgement, and an acknowledgement is not
finished work. 637 N Riata was, in effect, permanently "notified": the
information existed and nobody had picked it up. The escalation clocks
only mean something if the system can tell these three states apart.

Acknowledgement is detected from the project record itself -- the owner
replying on the Notion page they were @mentioned on -- so it needs only
read access, and it cannot be faked by the pipeline notifying itself.
"""
from __future__ import annotations

import datetime as dt

from .audit import AuditStore
from .model import UTC
from .ports import ConnectorError

#: Stages that mean the redline work is out the door.
COMPLETION_STAGES = frozenset({
    "4 · Submitted to City", "5 · Approved", "6 · Construction", "7 · Closed",
})


class NotionAcknowledgementWatcher:
    """Reads Notion and promotes event state when a human actually acts."""

    def __init__(self, store: AuditStore, reader, owner_ids: dict[str, str]) -> None:
        self.store = store
        self.reader = reader
        #: owner key -> Notion user id, so a comment can be attributed.
        self.owner_ids = owner_ids

    def poll(self) -> dict[str, list[str]]:
        """Promote NOTIFIED -> ACKNOWLEDGED -> COMPLETED where earned."""
        promoted: dict[str, list[str]] = {"acknowledged": [], "completed": []}

        for row in self.store.open_events():
            page_id = row["project_id"]
            if not page_id:
                continue
            owner_id = self.owner_ids.get(row["owner_key"] or "")

            if row["acknowledged_at"] is None and owner_id:
                try:
                    comments = self.reader.comments(page_id)
                except ConnectorError as error:
                    self.store.log(row["event_id"], "ack_poll", "failed",
                                   error=str(error))
                    continue
                notified_at = _parse(row["received_at"])
                for comment in comments:
                    if comment.get("author_id") != owner_id:
                        continue
                    created = _parse(comment.get("created_at", ""))
                    if created and notified_at and created >= notified_at:
                        self.store.acknowledge(
                            row["event_id"], created, how="notion_comment_reply"
                        )
                        promoted["acknowledged"].append(row["event_id"])
                        break

            if row["completed_at"] is None:
                try:
                    stage = self.reader.stage(page_id)
                except ConnectorError as error:
                    self.store.log(row["event_id"], "complete_poll", "failed",
                                   error=str(error))
                    continue
                if stage in COMPLETION_STAGES:
                    self.store.complete(row["event_id"], how=f"stage:{stage}")
                    promoted["completed"].append(row["event_id"])

        return promoted


def _parse(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
