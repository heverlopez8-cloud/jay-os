"""Classify what a jurisdiction, client or consultant actually said.

The patterns here are taken from real PCD correspondence, not invented:
Gilbert EnerGov ("returned for corrections"), Phoenix SHAPE/ProjectDox
("Corrections Required"), plans-examiner prose ("plans are still
incorrect"), and the client escalation on 637 N Riata ("costing us $300
per day").

Classification never guesses. Below `MIN_CONFIDENCE` the caller raises a
CLASSIFICATION_UNCERTAIN exception rather than acting on a coin flip.
"""
from __future__ import annotations

import re

from .model import Classification, EventClass

#: Below this, a human decides. A wrong auto-classification is worse than
#: an exception, because it produces confident, invisible mishandling.
MIN_CONFIDENCE = 0.60

# Ordered most-specific first: an email that says both "corrections" and
# "fees" is a corrections event that happens to mention money.
_RULES: tuple[tuple[EventClass, float, tuple[str, ...]], ...] = (
    (
        EventClass.ACKNOWLEDGEMENT,
        0.95,
        (
            r"^\s*automatic reply",
            r"^\s*auto(?:mated)? response",
            r"\bout of (?:the )?office\b",
            r"\bhas been received\b.*\bresponse time\b",
            r"\bthank you\b.*\byour (?:request|submittal) .*\breceived\b",
            r"\bdo not reply to this (?:message|email)\b.*\breceipt\b",
            # Phoenix SHAPE, verbatim 2026-09-05: "We have received your
            # application CTR-102609537. We will check it for completeness."
            # A receipt, not work. Four of these reached Jay as exceptions.
            r"\bwe have received your application\b",
            r"\bconfirmation of submission\b",
        ),
    ),
    (
        EventClass.CLIENT_ESCALATION,
        0.9,
        (
            r"\bcosting us\b",
            r"\bper day\b.*\b(?:cost|delay)",
            r"\bunacceptable\b",
            r"\banother (?:architect|designer|engineer)\b",
            r"\bi called (?:in(?:to)?|the)\b.*\b(?:city|town|county)\b",
            r"\bescalat(?:e|ion|ing)\b",
            r"\bhow much longer\b",
        ),
    ),
    (
        EventClass.CORRECTIONS_REQUIRED,
        0.95,
        (
            r"\breturned for corrections?\b",
            r"\bcorrections? required\b",
            r"\brequires? your attention\b",
            r"\bkicked back\b",
            r"\bredline",
            r"\bresubmit(?:tal)?\b.*\brequired\b",
            r"\bplans? (?:are|is) still incorrect\b",
            r"\bdenied\b",
            r"\brejected\b",
            r"\bdeficienc(?:y|ies)\b",
            r"\bcomment sheet\b",
            r"\bnot approved\b",
            r"\breview comments\b",
            # Real Phoenix wording, 3715 N 12th Pl, 2026-08-21: "I am going
            # to place both of these CTR's in a correction status".
            r"\bcorrection status\b",
            # Phoenix, 1205 E Garfield 2026-08-11: "will be going into a
            # correction cycle"; CTR-102604560 2026-05-07: "I'll put it in
            # Admin corrections."
            r"\bcorrection cycle\b",
            r"\badmin(?:istrative)? corrections?\b",
            r"\bstill (?:is )?not corrected\b",
            r"\bfix it and resubmit\b",
            r"\bresubmit for a second\b",
            r"\bplac(?:e|ing|ed) .{0,40}in (?:a )?correction\b",
            r"\breturned? .{0,30}for correction\b",
            r"\bfailed (?:the )?review\b",
            r"\breview failed\b",
            r"\bincorrect\b.{0,60}\b(?:plan|sheet|submittal)\b",
            # Maricopa County Permit Center, verbatim 2026-09-05: "needs
            # additional information. Your permit will not move forward in the
            # process until you resubmit the document(s) requested." Its
            # sibling "Resubmittal Required for a Substantive Review" already
            # classified; this wording did not, and it is the same cycle.
            #
            # The resubmittal consequence is REQUIRED. Bare "needs additional
            # information" is ubiquitous receipt boilerplate -- "If the plans
            # examiner needs additional information, you will be contacted" --
            # so on its own it invented a redline round in the permanent
            # Notion record from a routine status email. It also shadowed
            # INFO_REQUEST's own "need additional information" rule outright,
            # because CORRECTIONS_REQUIRED is evaluated first and `classify`
            # breaks on the first matching family, moving genuine reviewer
            # questions off Eric/48h onto Jay/24h.
            r"(?s)\bneeds? additional information\b"
            r".{0,120}\b(?:resubmit|will not move forward)\b",
            r"\bwill not move forward\b.{0,80}\bresubmit\b",
        ),
    ),
    (
        EventClass.FEE_DUE,
        0.85,
        (
            r"\bfees? (?:are )?due\b",
            r"\bpayment (?:is )?required\b",
            r"\bready to (?:pay|issue).*\bfee",
            r"\bmeter fee\b",
            r"\binvoice attached\b.*\bpermit\b",
            r"\bamount owed\b",
            r"\bpay(?:ment)? before (?:issuance|acceptance)\b",
            # Phoenix SHAPE, verbatim 2026-09-05, CTR-102608988: "the permit is
            # ready for purchase ... Permit Status: Approved Pending Payment
            # Amount Due*: $8,983.20". Two of these sat in Jay's exception
            # queue -- money, unrouted, with no clock on it.
            r"\bapproved pending payment\b",
            r"\bready for purchase\b",
            # A *nonzero* figure. Bare "amount due" matched the "Amount Due:
            # $0.00" line that issuance notices print, and since FEE_DUE is
            # evaluated before APPROVED and `classify` breaks on the first
            # matching family, an already-issued permit classified as money
            # owed: `_STAGE_FOR_CLASS` has no FEE_DUE entry so it never
            # reached "5 - Approved", and the client draft demanded payment
            # for a permit in hand. The real Phoenix notice this rule was
            # added for is already carried by "approved pending payment" and
            # "ready for purchase" above.
            r"\bamount due\b\D{0,10}[1-9][\d,]*",
            # Maricopa: "An invoice has been created in reference to permit
            # number BLDR2503385 ... Your Invoice Balance Due is: $1,206.07".
            r"\ban invoice has been created\b",
            r"\binvoice balance due\b",
            # Phoenix pre-log: "The plan review process will not begin until
            # the plan review fee has been paid." A fee gate stalling review.
            r"\breview (?:process )?will not begin until\b",
            r"\bdoes not begin until\b.{0,60}\bfees? (?:are|is) paid\b",
        ),
    ),
    (
        EventClass.APPROVED,
        0.9,
        (
            r"\bpermit (?:has been )?issued\b",
            r"\bplans? (?:have been )?approved\b",
            r"\breview (?:is )?complete.*\bapproved\b",
            r"\bready to issue\b",
            r"\bapproved for (?:permit|construction)\b",
            r"\bno further corrections\b",
            r"\bpermit .*now available\b",
            # Gilbert EnerGov, RACC-2026-00041 2026-08-27, verbatim subject:
            # "Town of Gilbert Plan Review Approved". The generic "plans
            # approved" rule misses it because "Review" sits between the two
            # words, so the sibling "Your Permit is Now Available" classified
            # and this one did not -- same permit, same day.
            r"\bplan review approved\b",
            r"\bplan review for\b.{0,40}\bhas been approved\b",
            # Phoenix ProjectDox, verbatim 2026-09-05: "Your approved plans are
            # ready to download for Project 2306900-SCSR." The approval only
            # counts once someone collects the stamped set.
            r"\bapproved plans are ready\b",
            r"\bplans? (?:are )?ready for download\b",
            # Phoenix via consultant forward, CPR-262500682 and CGD-262700384,
            # 2026-08-24/25: "Your Permit CPR-262500682 is Approved".
            r"\bpermit\b.{0,40}\bis approved\b",
        ),
    ),
    (
        EventClass.INSPECTION,
        0.8,
        (
            r"\binspection (?:scheduled|result|report)\b",
            r"\bschedule (?:your |an )?inspection\b",
            r"\binspector will\b",
            r"\bpassed inspection\b",
            r"\bfailed inspection\b",
            # Phoenix SHAPE, INS-00810059 on CTR-102511042, 2026-08-27:
            # "Inspection Completed". The scheduled notice classified and the
            # result notice did not, so the outcome was the half we lost.
            r"\binspection completed\b",
            r"\binspection resulted\b",
        ),
    ),
    (
        EventClass.INFO_REQUEST,
        0.75,
        (
            r"\bplease (?:provide|advise|clarify|confirm|submit)\b",
            r"\bneed (?:clarification|additional information)\b",
            r"\bcan you (?:provide|clarify|confirm)\b",
            r"\b(?:i'?d|would) like to discuss\b",
            r"\brequest for (?:information|additional)\b",
            r"\bwe require\b",
            # Floodplain/inspection prerequisites read as requests for action:
            # real Phoenix wording, 2115 & 2117 W Sherman, 2026-08-31.
            r"\bplease (?:also )?provide\b",
            r"\bneeds? to show compliance\b",
            r"\bprior to pouring\b",
            r"\bmust be\b.{0,40}\babove the base flood elevation\b",
        ),
    ),
)


#: Acknowledgement wording that is a receipt only when nothing else in the
#: message is actionable. Phoenix ProjectDox reuses "Task Completed
#: Notification" as the subject for review-*outcome* mail: the subject names
#: the workflow step, the body carries the result. Sitting in ACKNOWLEDGEMENT
#: -- the first family, 0.95, with an unconditional `break` -- it beat
#: CORRECTIONS_REQUIRED outright, and because ACKNOWLEDGEMENT is in
#: NO_ACTION_CLASSES the review result was released with no owner, no clock
#: and no escalation. That is the Riata silent drop, re-entered as a
#: classification rule. These patterns are consulted only after every
#: actionable family has declined, so a bare "You have just completed the
#: task below and are done with this step" still reads as the receipt it is.
_DEFERRED_ACKNOWLEDGEMENT: tuple[str, ...] = (
    r"\btask completed notification\b",
    r"\byou have just completed the task\b",
)


def classify(text: str) -> Classification:
    """Return the best-supported class, with the phrases that justified it."""
    haystack = text.lower()
    best: Classification | None = None

    for event_class, weight, patterns in _RULES:
        hits = tuple(
            pattern
            for pattern in patterns
            if re.search(pattern, haystack, re.MULTILINE)
        )
        if not hits:
            continue
        # More independent signals -> more confidence, capped just under 1.0
        # so nothing is ever treated as beyond question.
        confidence = min(0.99, weight + 0.02 * (len(hits) - 1))
        if best is None or confidence > best.confidence:
            best = Classification(event_class, confidence, hits)
        # First (most specific) rule family that matches wins ties outright.
        if hits and best.event_class is event_class:
            break

    if best is None:
        deferred = tuple(
            pattern
            for pattern in _DEFERRED_ACKNOWLEDGEMENT
            if re.search(pattern, haystack, re.MULTILINE)
        )
        if deferred:
            return Classification(EventClass.ACKNOWLEDGEMENT, 0.95, deferred)
        return Classification(EventClass.UNKNOWN, 0.0, ())
    return best


def is_confident(classification: Classification) -> bool:
    return (
        classification.event_class is not EventClass.UNKNOWN
        and classification.confidence >= MIN_CONFIDENCE
    )
