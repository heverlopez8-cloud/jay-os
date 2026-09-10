"""Work out which projects actually need a permit number, and which do not.

487 projects carry 247 without a permit number, but most of those are
closed, dormant, or never going to receive jurisdiction mail. Filling all
of them in by hand would be a week of work for almost no matching gain.

This narrows the list to projects that are simultaneously live, in or near
permitting, and plausibly receiving jurisdiction correspondence -- the only
ones whose missing identifier is actually costing a match today. Read-only.
"""
from __future__ import annotations

import collections
import dataclasses
from typing import Iterable

from .matching import address_key, extract_permit_numbers
from .model import ProjectRecord
from .sources import is_jurisdiction

#: Stages where jurisdiction mail is expected. Intake and design projects
#: have not been submitted yet, so a missing permit number is correct.
PERMITTING_STAGES = frozenset({
    "3 · Review & Redlines", "4 · Submitted to City", "5 · Approved",
    "6 · Construction",
})
DORMANT_STAGES = frozenset({"7 · Closed", "On Hold"})
DONE_STATUSES = frozenset({"DONE", "APPROVED"})


@dataclasses.dataclass
class Candidate:
    project: ProjectRecord
    reasons: list[str]
    recent_messages: int = 0
    example_subject: str = ""

    @property
    def priority(self) -> int:
        """Recent jurisdiction mail is the strongest signal that it matters."""
        score = self.recent_messages * 10
        if self.project.stage in PERMITTING_STAGES:
            score += 5
        if self.project.jurisdiction:
            score += 1
        return score


def _is_live(project: ProjectRecord) -> bool:
    if project.stage in DORMANT_STAGES:
        return False
    if project.status and all(s.upper() in DONE_STATUSES
                              for s in project.status if s):
        return False
    if project.name.upper().startswith(("ZZ-TEST", "ZZ TEST")):
        return False
    return True


def triage(projects: list[ProjectRecord],
           messages: Iterable[dict] | None = None) -> dict:
    """Split the portfolio into the parts that matter for matching."""
    live = [p for p in projects if _is_live(p)]
    permitting = [p for p in live if p.stage in PERMITTING_STAGES]
    with_permit = [p for p in live if p.permit_numbers]
    missing = [p for p in live if not p.permit_numbers]
    missing_in_permitting = [p for p in permitting if not p.permit_numbers]

    # Which live projects are actually being written about by a jurisdiction?
    mentions: collections.Counter = collections.Counter()
    examples: dict[str, str] = {}
    by_address: dict[str, ProjectRecord] = {}
    for project in live:
        key = address_key(project.address or project.name)
        if key:
            by_address.setdefault(key, project)

    for message in messages or []:
        sender = message.get("sender", "")
        if not is_jurisdiction(sender):
            continue
        text = f"{message.get('subject','')}\n{message.get('snippet','') or message.get('plaintextBody','')}"
        hit: ProjectRecord | None = None
        for line in [text] + text.splitlines():
            key = address_key(line)
            if key and key in by_address:
                hit = by_address[key]
                break
        if hit is None:
            continue
        mentions[hit.page_id] += 1
        examples.setdefault(hit.page_id, (message.get("subject") or "")[:80])

    candidates: list[Candidate] = []
    for project in missing:
        reasons: list[str] = []
        if project.stage in PERMITTING_STAGES:
            reasons.append(f"stage {project.stage}")
        if project.jurisdiction:
            reasons.append(f"jurisdiction {project.jurisdiction}")
        count = mentions.get(project.page_id, 0)
        if count:
            reasons.append(f"{count} recent jurisdiction message(s)")
        if not reasons:
            continue
        candidates.append(Candidate(project, reasons, count,
                                    examples.get(project.page_id, "")))

    candidates.sort(key=lambda c: -c.priority)
    return {
        "total": len(projects),
        "live": len(live),
        "permitting": len(permitting),
        "with_permit": len(with_permit),
        "missing_permit": len(missing),
        "missing_in_permitting": len(missing_in_permitting),
        "candidates": candidates,
        "mentioned_without_permit": sum(1 for c in candidates
                                        if c.recent_messages),
    }


def render(result: dict) -> str:
    lines = ["=" * 74,
             "PERMIT-IDENTIFIER ENRICHMENT TRIAGE  (read-only)",
             "=" * 74,
             f"projects in database          : {result['total']}",
             f"  live (not closed/on-hold/done): {result['live']}",
             f"  in permitting stages          : {result['permitting']}",
             f"  live WITH a permit number     : {result['with_permit']}",
             f"  live WITHOUT a permit number  : {result['missing_permit']}",
             f"    of those, in permitting     : {result['missing_in_permitting']}",
             "",
             f"ACTIONABLE LIST: {len(result['candidates'])} projects "
             f"(not {result['missing_permit']})",
             f"  of which have recent jurisdiction mail: "
             f"{result['mentioned_without_permit']}",
             ""]
    if result["candidates"]:
        lines.append("HIGHEST VALUE FIRST")
        for candidate in result["candidates"][:25]:
            lines.append(f"  [{candidate.priority:>3}] "
                         f"{candidate.project.name[:52]}")
            lines.append(f"        {'; '.join(candidate.reasons)}")
            if candidate.example_subject:
                lines.append(f"        e.g. {candidate.example_subject}")
    return "\n".join(lines)
