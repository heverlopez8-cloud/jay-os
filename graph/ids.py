"""Deterministic entity IDs that REUSE the identities JAY-OS already has.

THE DUPLICATE-IDENTITY FAILURE THIS PREVENTS
--------------------------------------------
Jay's instruction: "Do not create duplicate identities for the same object."
The cheapest way to break that is to mint a fresh UUID every ingest, so IDs
here are a pure function of (type, canonical key). Re-ingesting the same
project on Tuesday produces the same ID it produced on Monday, which is what
makes the whole pipeline idempotent.

Where JAY-OS already owns an identifier, it wins. A project's Notion page id
is the operational truth and becomes the ID suffix, so the graph and Notion
can always be joined. The graph never invents a parallel numbering for things
that are already numbered.
"""
from __future__ import annotations

import hashlib
import re

from .contract import EntityType

_SLUG_OK = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,46}$")
_NON_SLUG = re.compile(r"[^A-Z0-9]+")


def slugify(value: str) -> str:
    return _NON_SLUG.sub("-", (value or "").upper()).strip("-")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12].upper()


def canonical_key(entity_type: EntityType, raw: str) -> str:
    """Normalise the natural key for a type. Case and spacing never matter."""
    text = (raw or "").strip()
    if not text:
        raise ValueError(f"a {entity_type.value} needs a non-empty canonical key")
    if entity_type is EntityType.PERSON and "@" in text:
        return text.lower()
    return " ".join(text.upper().split())


def entity_id(entity_type: EntityType, key: str,
              external_id: str | None = None) -> str:
    """`TYPE-SUFFIX`, stable forever for the same inputs.

    `external_id` is an identifier JAY-OS already assigned (a Notion page id,
    a Gmail message id). When present it becomes the suffix verbatim-ish, so
    the graph inherits the existing identity instead of shadowing it.
    """
    if external_id:
        suffix = slugify(str(external_id))[:48] or _digest(str(external_id))
        return f"{entity_type.value}-{suffix}"
    normalized = canonical_key(entity_type, key)
    candidate = slugify(normalized)
    if _SLUG_OK.match(candidate) and len(candidate) <= 32:
        return f"{entity_type.value}-{candidate}"
    return f"{entity_type.value}-{_digest(normalized)}"


def event_id(timestamp: str, sequence: int) -> str:
    """`EVT-YYYYMMDD-NNNNN`, the shape Jay specified."""
    day = "".join(ch for ch in timestamp[:10] if ch.isdigit()) or "00000000"
    return f"EVT-{day}-{sequence:05d}"


def lesson_id(sequence: int) -> str:
    return f"LESSON-{sequence:05d}"
