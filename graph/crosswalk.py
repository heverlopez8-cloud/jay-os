"""Reconciling this engine's IDs with the vault's DATA_DICTIONARY.md scheme.

WHY THIS EXISTS
----------------
Two ID systems now provably exist for overlapping things:
  - This engine: PROJECT-<key>, PERSON-<key>, etc. -- deterministic, hash-based.
  - The vault's `00_CORE/DATA_DICTIONARY.md`: PRJ-YYYY-NNNN, PER-NNNN,
    ORG-NNNN, PROP-XX-NNNN, DEC-YYYY-NNNN, MTG-YYYY-NNNN, LES-NNNN --
    sequential, assigned once, by a human-governed process.

WHY THIS IS A CROSSWALK, NOT A RENAME
--------------------------------------
DATA_DICTIONARY.md states its own rule directly:

    "Do not assign new IDs to information that already has an established
    identifier elsewhere (e.g. a Notion page ID) if doing so would create
    confusion -- record a recommended migration separately instead of
    renaming in place."

Every PROJECT entity in this graph already has an established identifier:
a Notion page ID (see `pipeline.py::_handle_project`, `external_id=...`).
Renaming 427 real projects to PRJ-YYYY-NNNN would be exactly the renaming
this rule forbids. So this module never changes an existing entity_id. It
records a SEPARATE, additional fact: "this graph entity corresponds to
that vault ID" -- reversible, additive, and exactly what the dictionary
asked for instead of a rename.

WHAT THIS DOES NOT DO
----------------------
It does not invent vault IDs. It does not write into the vault. It does not
assign a PER-/DEC-/etc. number on the vault's behalf -- assignment is "once,
in order of creation," which is the vault's own act, not this engine's. This
module only has somewhere to WRITE the correspondence once a human (or a
future adapter reading real vault frontmatter) supplies it.
"""
from __future__ import annotations

from .contract import EntityType

#: Entity types with a direct, defined counterpart in DATA_DICTIONARY.md's
#: ID table. Populated only for types that map cleanly onto one vault
#: prefix -- guessing a mapping for an ambiguous type is worse than leaving
#: it unmapped.
VAULT_PREFIX = {
    EntityType.PROJECT: "PRJ",     # PRJ-YYYY-NNNN
    EntityType.PERSON: "PER",      # PER-NNNN
    EntityType.COMPANY: "ORG",     # ORG-NNNN ("organization" in the dictionary)
    EntityType.PROPERTY: "PROP",   # PROP-XX-NNNN (XX = state code)
    EntityType.MEETING: "MTG",     # MTG-YYYY-NNNN
    EntityType.DECISION: "DEC",    # DEC-YYYY-NNNN
    EntityType.LESSON: "LES",      # LES-NNNN
}

#: Entity types this engine needed that DATA_DICTIONARY.md does not define
#: at all -- they are PCD/business-operational concepts (a permit, an
#: invoice, a jurisdiction) that the vault's general life/business dictionary
#: was never scoped to cover. Not a gap to silently patch: per the vault's
#: own precedent (`BUS-`, `ENT-`, `AST-`, `OBL-` were all proposed in a
#: business README, then adopted by Jay, then registered by him in the
#: dictionary) new prefixes are PROPOSED, not minted by an agent. See
#: `VAULT-INTEGRATION-PROPOSAL.md` for the actual proposal text.
UNMAPPED_TYPES = frozenset(
    t for t in EntityType if t not in VAULT_PREFIX
)


def vault_prefix_for(entity_type: EntityType) -> str | None:
    """The DATA_DICTIONARY.md prefix this type corresponds to, or None."""
    return VAULT_PREFIX.get(entity_type)


def has_established_identifier(attributes: dict) -> bool:
    """True when this entity already has an identifier from elsewhere.

    Mirrors the dictionary's own exemption: a Notion page ID (or, by the
    same logic, a permit number, an APN, an email address) is already an
    established identifier. Entities carrying one are candidates for a
    crosswalk record, never for a vault ID assignment.
    """
    return bool(
        attributes.get("notion_page_id") or attributes.get("jayos_project_id")
        or attributes.get("apn") or attributes.get("email"))
