"""Entity resolution: decide whether this is a thing we already know.

WHY THIS IS THE MOST DANGEROUS MODULE IN THE ENGINE
---------------------------------------------------
Everything else is additive and reversible. Resolution is where the graph can
do real damage: merge two different people into one identity and every edge
attached to either becomes a lie, silently, forever. The failure does not look
like an error -- it looks like a well-connected graph.

So the rule is asymmetric on purpose. Creating a second entity for one real
person is a visible, fixable mess (it shows up as a duplicate candidate and an
isolated node). Merging two real people is an invisible, unfixable one. When
unsure, this module SPLITS and files a CANDIDATE_MERGE for Jay.

THE LADDER
----------
Strongest evidence first, the same shape as `permit_pipeline.matching`:

  1. exact entity id                -> 1.00  certain
  2. canonical key                  -> 0.99  certain
  3. known alias                    -> 0.97  certain
  4. shared association (APN/project) -> 0.90  strong, still auto-linkable
  5. contact information (email)    -> 0.96  certain for PERSON
  6. fuzzy name similarity          -> 0.70-0.88  CANDIDATE ONLY, never merged
  7. contextual co-occurrence       -> 0.60-0.75  CANDIDATE ONLY

Steps 6 and 7 are deliberately capped below the production auto-link gate, so
no amount of string similarity can merge identities on its own.
"""
from __future__ import annotations

import dataclasses
import difflib

from ._reuse import gates
from .contract import EntityStatus, EntityType
from .store import GraphStore

#: Fuzzy matching can never exceed this, whatever the string says.
#: Sits below AUTO_LINK_CONFIDENCE so it can only ever propose, never merge.
FUZZY_CEILING = 0.88

#: Below this, two NAMES are not even worth PROPOSING as the same thing.
#: Deliberately low: Jay's worked example is "Bob Smith" / "Robert Smith" at
#: 0.71, which must surface as a candidate merge. A noisy candidate costs one
#: glance; a missed duplicate costs a split identity nobody notices.
FUZZY_FLOOR = 0.70

#: ...but only for things humans type inconsistently. PROJECTs, PROPERTYs and
#: PERMITs carry a strong natural key (parcel number, permit number, address
#: key) and their names are structured, so near-misses there are usually real
#: distinctions -- "1728 W SHERMAN ST - PHASE 1" and "- PHASE 2" score 0.97
#: similar and are two different projects. Applying the name floor to them
#: produced 111 candidate merges out of 274 entities on the first real run:
#: a review queue that large is one nobody reads, which is worse than none.
STRUCTURED_FUZZY_FLOOR = 0.985

#: Types whose names are human-entered and inconsistent.
NAME_LIKE_TYPES = frozenset({
    EntityType.PERSON, EntityType.CLIENT, EntityType.COMPANY})


def fuzzy_floor_for(entity_type: EntityType) -> float:
    """How similar two names must be before a duplicate is even proposed."""
    return (FUZZY_FLOOR if entity_type in NAME_LIKE_TYPES
            else STRUCTURED_FUZZY_FLOOR)

#: The association path is stricter. Shared association plus name agreement is
#: allowed to auto-link, so the name half of that evidence must be strong --
#: 0.70 similarity plus a shared parcel is not an identity.
ASSOCIATION_NAME_FLOOR = 0.82


@dataclasses.dataclass(frozen=True)
class Resolution:
    """What resolution decided, and why."""

    entity_id: str | None
    confidence: float
    basis: str
    is_new: bool
    merge_candidate_for: str | None = None

    @property
    def resolved(self) -> bool:
        return self.entity_id is not None and not self.is_new


def _similar(left: str, right: str) -> float:
    return difflib.SequenceMatcher(
        None, " ".join(left.upper().split()), " ".join(right.upper().split())).ratio()


def resolve(store: GraphStore, entity_type: EntityType, name: str, *,
            key: str | None = None, external_id: str | None = None,
            email: str | None = None,
            associations: tuple[str, ...] = ()) -> Resolution:
    """Walk the ladder. Returns a Resolution; does not write the entity.

    `associations` are other entity ids this thing is known to touch (its
    project, its property). Two candidates sharing an association are far more
    likely the same thing than two that merely share a similar name.
    """
    auto_link, propose, _ = gates()
    natural_key = key or name

    # 1. exact id -------------------------------------------------------------
    if external_id:
        from .ids import entity_id as make_id
        candidate = make_id(entity_type, natural_key, external_id)
        if store.entity(candidate) is not None:
            return Resolution(candidate, 1.0, "exact entity id", False)

    # 2. canonical key --------------------------------------------------------
    row = store.find_by_key(entity_type, natural_key)
    if row is not None:
        return Resolution(row["entity_id"], 0.99, "canonical key", False)

    # 3. alias ----------------------------------------------------------------
    row = store.find_by_alias(entity_type, natural_key)
    if row is not None:
        return Resolution(row["entity_id"], 0.97, "known alias", False)

    # 5. contact information (checked before fuzzy: an email is an identity) --
    if email and entity_type in (EntityType.PERSON, EntityType.CLIENT):
        row = store.find_by_key(entity_type, email.lower())
        if row is not None:
            return Resolution(row["entity_id"], 0.96, "contact email", False)
        for existing in store.entities(entity_type):
            import json
            attrs = json.loads(existing["attributes"] or "{}")
            if str(attrs.get("email", "")).lower() == email.lower():
                return Resolution(existing["entity_id"], 0.96,
                                  "contact email in attributes", False)

    pool = [r for r in store.entities(entity_type)
            if r["status"] != EntityStatus.SUPERSEDED.value]

    # 4. shared association ---------------------------------------------------
    if associations:
        linked: dict[str, int] = {}
        for other in associations:
            for edge in store.relationships_to(other) + store.relationships_from(other):
                for end in (edge["source_entity"], edge["target_entity"]):
                    candidate_row = store.entity(end)
                    if (candidate_row is not None
                            and candidate_row["entity_type"] == entity_type.value
                            and end != other):
                        linked[end] = linked.get(end, 0) + 1
        for candidate, _hits in sorted(linked.items(), key=lambda kv: -kv[1]):
            candidate_row = store.entity(candidate)
            if candidate_row is None:
                continue
            score = _similar(candidate_row["canonical_name"], name)
            if score >= ASSOCIATION_NAME_FLOOR:
                # Name agreement PLUS a shared association is strong enough to
                # link, because the association is independent evidence.
                return Resolution(candidate, max(propose, min(0.90, score)),
                                  "shared association and name agreement", False)

    # 6. fuzzy name -----------------------------------------------------------
    best, best_score = None, 0.0
    for candidate_row in pool:
        score = _similar(candidate_row["canonical_name"], name)
        if score > best_score:
            best, best_score = candidate_row, score
    if best is not None and best_score >= fuzzy_floor_for(entity_type):
        capped = min(FUZZY_CEILING, best_score)
        # Never merges. Proposes, and the caller files a candidate merge.
        return Resolution(None, capped,
                          f"fuzzy name similarity {best_score:.2f} (not merged)",
                          True, merge_candidate_for=best["entity_id"])

    # 7. contextual -----------------------------------------------------------
    return Resolution(None, 0.0, "no existing entity matched", True)


def resolve_or_create(store: GraphStore, entity_type: EntityType, name: str, *,
                      source: str, source_ref: str,
                      key: str | None = None, external_id: str | None = None,
                      email: str | None = None,
                      associations: tuple[str, ...] = (),
                      attributes: dict | None = None,
                      observed_at: str | None = None) -> tuple[str, Resolution]:
    """Resolve, and create the entity when resolution says it is new.

    When resolution found a near-miss it records a CANDIDATE_MERGE and still
    creates the separate entity. Splitting is recoverable; merging is not.
    """
    decision = resolve(store, entity_type, name, key=key, external_id=external_id,
                       email=email, associations=associations)
    if decision.resolved and decision.entity_id:
        if " ".join(name.upper().split()) != " ".join(
                (store.entity(decision.entity_id)["canonical_name"] or "").upper().split()):
            store.add_alias(decision.entity_id, name, source)
        store.add_source_ref(decision.entity_id, source, source_ref,
                             observed_at or source_ref)
        return decision.entity_id, decision

    merged_attrs = dict(attributes or {})
    if email:
        merged_attrs.setdefault("email", email.lower())
    created = store.upsert_entity(
        entity_type, name, key=key, external_id=external_id, source=source,
        source_ref=source_ref, observed_at=observed_at,
        confidence=1.0 if not decision.merge_candidate_for else 0.9,
        status=EntityStatus.ACTIVE if not decision.merge_candidate_for
        else EntityStatus.MERGE_CANDIDATE,
        attributes=merged_attrs)
    if decision.merge_candidate_for:
        store.add_candidate_merge(created, decision.merge_candidate_for,
                                  decision.confidence, decision.basis)
    return created, decision
