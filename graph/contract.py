"""The graph's frozen vocabulary. Code, reviewed like code -- not config.

WHY THIS IS A MODULE AND NOT A CONFIG FILE
------------------------------------------
`email_sentinel/contract.py` already made this argument and it holds here:
adding a relationship type changes what JAY-OS believes can be true about the
world. That is a reviewed change. Thresholds and mailboxes are deployment
facts; the vocabulary is not.

WHY RELATIONSHIPS ARE DOMAIN-CONSTRAINED
----------------------------------------
Jay's instruction was explicit: do NOT create generic links everywhere. A
graph where anything may relate to anything degenerates into the thing it was
built to replace -- a pile of notes that all mention each other. So every
predicate declares which (subject_type, object_type) pairs it accepts, and
`validate_relationship` refuses the rest. A PERMIT cannot be SUBMITTED_TO a
PERSON. An INVOICE cannot be LOCATED_AT a DECISION.

That refusal is the feature. It is cheaper to reject a true-but-untyped edge
than to let one wrong edge teach a lesson engine a wrong pattern.

THE GATES ARE BORROWED, NOT REDEFINED
-------------------------------------
Confidence thresholds come from `email_sentinel.contract` via `_reuse.gates()`.
This layer has no authority to soften a production gate, so it does not own a
copy of one.
"""
from __future__ import annotations

import enum

from ._reuse import gates


class EntityType(str, enum.Enum):
    """Everything JAY-OS is allowed to consider a thing.

    Production must never extend this at runtime. `parse_entity_type` refuses
    any value not listed, which stops an adapter inventing `PROJECT_V2`.
    """

    PERSON = "PERSON"
    CLIENT = "CLIENT"
    COMPANY = "COMPANY"
    PROJECT = "PROJECT"
    PROPERTY = "PROPERTY"
    JURISDICTION = "JURISDICTION"
    MEETING = "MEETING"
    CALL = "CALL"
    EMAIL = "EMAIL"
    DOCUMENT = "DOCUMENT"
    CONTRACT = "CONTRACT"
    PERMIT = "PERMIT"
    REVIEW_COMMENT = "REVIEW_COMMENT"
    TASK = "TASK"
    DECISION = "DECISION"
    INVOICE = "INVOICE"
    PAYMENT = "PAYMENT"
    AGENT = "AGENT"
    WORKFLOW = "WORKFLOW"
    LESSON = "LESSON"
    OUTCOME = "OUTCOME"


class RelationType(str, enum.Enum):
    """Typed, directional predicates. Direction carries meaning.

    PROJECT --SUBMITTED_TO--> JURISDICTION is a fact.
    PROJECT <-> Scottsdale is a rumour.
    """

    OWNS = "OWNS"
    WORKS_ON = "WORKS_ON"
    CLIENT_OF = "CLIENT_OF"
    PART_OF = "PART_OF"
    LOCATED_AT = "LOCATED_AT"
    LOCATED_IN = "LOCATED_IN"
    SUBMITTED_TO = "SUBMITTED_TO"
    REVIEWED_BY = "REVIEWED_BY"
    REQUIRES = "REQUIRES"
    DEPENDS_ON = "DEPENDS_ON"
    BLOCKED_BY = "BLOCKED_BY"
    CREATED_FROM = "CREATED_FROM"
    MENTIONED_IN = "MENTIONED_IN"
    ASSIGNED_TO = "ASSIGNED_TO"
    DECIDED_BY = "DECIDED_BY"
    GENERATED = "GENERATED"
    SUPERSEDES = "SUPERSEDES"
    RESULTED_IN = "RESULTED_IN"
    PAID_BY = "PAID_BY"
    PAID_TO = "PAID_TO"
    RELATED_TO = "RELATED_TO"
    LEARNED_FROM = "LEARNED_FROM"


class EntityStatus(str, enum.Enum):
    """Lifecycle of a canonical entity."""

    ACTIVE = "ACTIVE"
    PROVISIONAL = "PROVISIONAL"     # created from inference, not yet confirmed
    MERGE_CANDIDATE = "MERGE_CANDIDATE"
    SUPERSEDED = "SUPERSEDED"       # merged away; row is kept, never deleted


class Disposition(str, enum.Enum):
    """What may be DONE with a relationship, keyed off the production gates.

    Mirrors `email_sentinel.contract.MatchDisposition` deliberately: one
    vocabulary of 'confident' across JAY-OS.
    """

    ASSERTED = "ASSERTED"    # from a source of truth, confidence 1.0
    LINKED = "LINKED"        # >= AUTO_LINK_CONFIDENCE
    PROPOSED = "PROPOSED"    # >= PROPOSE_CONFIDENCE, needs a human
    WEAK = "WEAK"            # below the propose gate; recorded, never acted on


class LessonStatus(str, enum.Enum):
    """A lesson's journey. The engine may only produce the first two."""

    OBSERVED = "OBSERVED"
    CANDIDATE = "CANDIDATE"
    VALIDATING = "VALIDATING"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"


#: Statuses the automated engine is permitted to write. Anything beyond this
#: is a governance act requiring Jay, enforced in `store.set_lesson_status`.
ENGINE_WRITABLE_LESSON_STATUSES = frozenset(
    {LessonStatus.OBSERVED, LessonStatus.CANDIDATE})

#: Promotion to a live production rule. Never automatic, by construction.
OWNER_ONLY_LESSON_STATUSES = frozenset(
    {LessonStatus.VALIDATING, LessonStatus.APPROVED, LessonStatus.ACTIVE,
     LessonStatus.DEPRECATED})


E = EntityType
R = RelationType

#: Which (subject, object) type pairs each predicate accepts.
#: An empty set would mean "anything", so there are none.
RELATION_DOMAINS: dict[RelationType, frozenset[tuple[EntityType, EntityType]]] = {
    R.OWNS: frozenset({
        (E.PERSON, E.PROPERTY), (E.COMPANY, E.PROPERTY),
        (E.CLIENT, E.PROPERTY), (E.PERSON, E.COMPANY)}),
    R.WORKS_ON: frozenset({
        (E.PERSON, E.PROJECT), (E.COMPANY, E.PROJECT),
        (E.AGENT, E.PROJECT), (E.PERSON, E.TASK), (E.AGENT, E.TASK),
        # A registered contractor works on a PERMIT, not just a project --
        # portal exports name the contractor per permit, which is the only
        # level at which that fact is actually asserted.
        (E.COMPANY, E.PERMIT), (E.PERSON, E.PERMIT)}),
    R.CLIENT_OF: frozenset({
        (E.CLIENT, E.COMPANY), (E.PERSON, E.COMPANY)}),
    R.PART_OF: frozenset({
        (E.TASK, E.PROJECT), (E.DOCUMENT, E.PERMIT),
        (E.REVIEW_COMMENT, E.PERMIT), (E.PERMIT, E.PROJECT),
        (E.PAYMENT, E.INVOICE), (E.PROPERTY, E.PROJECT),
        # A permit technician is staff of the jurisdiction. Needed so a named
        # reviewer attaches to the town rather than floating as an isolated node.
        (E.PERSON, E.JURISDICTION)}),
    R.LOCATED_AT: frozenset({
        (E.PROJECT, E.PROPERTY), (E.PERMIT, E.PROPERTY)}),
    R.LOCATED_IN: frozenset({
        (E.PROPERTY, E.JURISDICTION), (E.PROJECT, E.JURISDICTION)}),
    R.SUBMITTED_TO: frozenset({
        (E.PERMIT, E.JURISDICTION), (E.PROJECT, E.JURISDICTION),
        (E.DOCUMENT, E.JURISDICTION)}),
    R.REVIEWED_BY: frozenset({
        (E.PERMIT, E.PERSON), (E.DOCUMENT, E.PERSON),
        (E.PERMIT, E.JURISDICTION)}),
    R.REQUIRES: frozenset({
        (E.PERMIT, E.DOCUMENT), (E.TASK, E.DOCUMENT),
        (E.PROJECT, E.PERMIT), (E.REVIEW_COMMENT, E.DOCUMENT)}),
    R.DEPENDS_ON: frozenset({
        (E.TASK, E.TASK), (E.PERMIT, E.PERMIT),
        (E.PROJECT, E.PROJECT), (E.WORKFLOW, E.WORKFLOW)}),
    R.BLOCKED_BY: frozenset({
        (E.PERMIT, E.REVIEW_COMMENT), (E.TASK, E.REVIEW_COMMENT),
        (E.PROJECT, E.REVIEW_COMMENT), (E.TASK, E.TASK),
        (E.PERMIT, E.DOCUMENT)}),
    R.CREATED_FROM: frozenset({
        (E.DOCUMENT, E.EMAIL), (E.DOCUMENT, E.MEETING),
        (E.TASK, E.EMAIL), (E.TASK, E.REVIEW_COMMENT),
        (E.DECISION, E.MEETING), (E.DOCUMENT, E.DOCUMENT),
        (E.REVIEW_COMMENT, E.EMAIL), (E.REVIEW_COMMENT, E.DOCUMENT),
        # A decision usually traces to the comment or email that forced it.
        (E.DECISION, E.REVIEW_COMMENT), (E.DECISION, E.EMAIL)}),
    R.MENTIONED_IN: frozenset({
        (E.PROJECT, E.EMAIL), (E.PERMIT, E.EMAIL), (E.PERSON, E.EMAIL),
        (E.PROPERTY, E.EMAIL), (E.PROJECT, E.MEETING), (E.PERSON, E.MEETING),
        (E.PROJECT, E.CALL), (E.PERSON, E.CALL), (E.PROJECT, E.DOCUMENT),
        (E.PERMIT, E.DOCUMENT), (E.PROPERTY, E.DOCUMENT),
        (E.JURISDICTION, E.EMAIL), (E.JURISDICTION, E.DOCUMENT)}),
    R.ASSIGNED_TO: frozenset({
        (E.TASK, E.PERSON), (E.TASK, E.AGENT),
        (E.REVIEW_COMMENT, E.PERSON)}),
    R.DECIDED_BY: frozenset({
        (E.DECISION, E.PERSON), (E.DECISION, E.AGENT)}),
    R.GENERATED: frozenset({
        (E.AGENT, E.DOCUMENT), (E.WORKFLOW, E.DOCUMENT),
        (E.AGENT, E.TASK), (E.WORKFLOW, E.TASK),
        (E.AGENT, E.LESSON), (E.MEETING, E.TASK)}),
    R.SUPERSEDES: frozenset({
        (E.DOCUMENT, E.DOCUMENT), (E.PERMIT, E.PERMIT),
        (E.CONTRACT, E.CONTRACT), (E.DECISION, E.DECISION),
        (E.LESSON, E.LESSON)}),
    R.RESULTED_IN: frozenset({
        (E.DECISION, E.OUTCOME), (E.TASK, E.OUTCOME),
        (E.PERMIT, E.OUTCOME), (E.REVIEW_COMMENT, E.OUTCOME),
        (E.WORKFLOW, E.OUTCOME), (E.MEETING, E.DECISION)}),
    R.PAID_BY: frozenset({
        (E.INVOICE, E.CLIENT), (E.INVOICE, E.PERSON),
        (E.INVOICE, E.COMPANY)}),
    R.PAID_TO: frozenset({
        (E.PAYMENT, E.COMPANY), (E.PAYMENT, E.JURISDICTION),
        (E.PAYMENT, E.PERSON)}),
    R.RELATED_TO: frozenset({
        (E.PROJECT, E.PROJECT), (E.PROPERTY, E.PROPERTY),
        (E.PERSON, E.PERSON), (E.COMPANY, E.COMPANY),
        (E.PERMIT, E.PERMIT)}),
    R.LEARNED_FROM: frozenset({
        (E.LESSON, E.OUTCOME), (E.LESSON, E.DECISION),
        (E.LESSON, E.REVIEW_COMMENT), (E.LESSON, E.EMAIL)}),
}


class VocabularyError(ValueError):
    """Something tried to speak a word the graph does not know."""


def parse_entity_type(value: str) -> EntityType:
    try:
        return EntityType(str(value).strip().upper())
    except ValueError as error:
        raise VocabularyError(
            f"{value!r} is not a known entity type. The graph refuses to "
            f"invent one at runtime; add it to EntityType in a reviewed "
            f"change.") from error


def parse_relation_type(value: str) -> RelationType:
    try:
        return RelationType(str(value).strip().upper())
    except ValueError as error:
        raise VocabularyError(
            f"{value!r} is not a known relationship type.") from error


def validate_relationship(subject_type: EntityType, predicate: RelationType,
                          object_type: EntityType) -> None:
    """Refuse an edge whose endpoints the predicate does not accept.

    This is the guard against 'generic links everywhere'. It raises rather
    than downgrading confidence, because an edge with the wrong TYPE is not a
    low-confidence fact -- it is a category error, and averaging it into the
    graph would teach the lesson engine nonsense.
    """
    allowed = RELATION_DOMAINS.get(predicate)
    if not allowed:
        raise VocabularyError(
            f"{predicate.value} declares no legal endpoints, so nothing may "
            f"use it. Give it a domain in RELATION_DOMAINS.")
    if (subject_type, object_type) not in allowed:
        legal = ", ".join(sorted(f"{s.value}->{o.value}" for s, o in allowed))
        raise VocabularyError(
            f"{subject_type.value} --{predicate.value}--> {object_type.value} "
            f"is not a legal edge. {predicate.value} accepts: {legal}")


def disposition_for(confidence: float, asserted: bool = False) -> Disposition:
    """Map a confidence onto what may be done with it, using the PRODUCTION gates."""
    auto_link, propose, _ = gates()
    if asserted:
        return Disposition.ASSERTED
    if confidence >= auto_link:
        return Disposition.LINKED
    if confidence >= propose:
        return Disposition.PROPOSED
    return Disposition.WEAK


def actionable(disposition: Disposition) -> bool:
    """Only ASSERTED and LINKED edges may drive behaviour without a human."""
    return disposition in (Disposition.ASSERTED, Disposition.LINKED)
