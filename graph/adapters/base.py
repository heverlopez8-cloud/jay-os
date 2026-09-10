"""The adapter contract: what every source must hand the pipeline.

WHY A RECORD AND NOT A DIRECT WRITE
-----------------------------------
An adapter's job is to read its system and describe what it saw. It does NOT
create entities or edges. That separation is what lets the pipeline apply
resolution, provenance checks and governance uniformly -- a connector cannot
smuggle an unattributed edge into the graph, because it has no way to write one.

Every record carries its own `source_ref` and `observed_at`. Provenance starts
at the adapter, not at the store; by the time a fact reaches the store it is
too late to reconstruct where it came from.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any, Iterator, Protocol

UTC = dt.timezone.utc


@dataclasses.dataclass(frozen=True)
class SourceRecord:
    """One observation from one source.

    `kind` is the adapter's own word for what it found ("project", "permit_row",
    "document"); the pipeline maps kinds to entity types. `payload` is raw
    fields -- no graph vocabulary -- so adapters never need to know the
    contract.
    """

    kind: str
    source: str
    source_ref: str
    observed_at: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.source_ref.strip():
            raise ValueError(
                f"a {self.kind} record from {self.source} has no source_ref; "
                f"it could never be traced back and is therefore not evidence")


class Source(Protocol):
    """What the pipeline requires of a connector."""

    name: str

    def records(self) -> Iterator[SourceRecord]:
        ...


class PendingConnector:
    """A declared but unimplemented source.

    Raising here -- loudly, naming itself -- is better than an empty iterator,
    which would look exactly like "that system had nothing new".
    """

    def __init__(self, name: str, note: str = "") -> None:
        self.name = name
        self.note = note

    def records(self) -> Iterator[SourceRecord]:
        raise NotImplementedError(
            f"the {self.name} connector is declared but not implemented in v1. "
            f"{self.note} Returning zero records would be indistinguishable "
            f"from a quiet day, so this raises instead.")


def utc_stamp(value: Any = None) -> str:
    """Best-effort ISO-8601 UTC. Unknown times are never silently 'now'."""
    if isinstance(value, dt.datetime):
        return value.astimezone(UTC).replace(microsecond=0).isoformat()
    if isinstance(value, str) and value.strip():
        text = value.strip()
        for form in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = dt.datetime.strptime(text, form)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                return parsed.astimezone(UTC).replace(microsecond=0).isoformat()
            except ValueError:
                continue
        return text
    return dt.datetime.now(UTC).replace(microsecond=0).isoformat()
