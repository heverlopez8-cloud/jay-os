"""Propose permit numbers for projects that are missing one.

Strictly a proposal. Nothing here writes to Notion: every candidate is
returned with the evidence that produced it -- which message, from which
jurisdiction address, on which date -- so a human can approve or reject it
on sight rather than trusting a score.

Evidence is ranked by how much it proves. A permit number that a
jurisdiction wrote in a message about that address is far stronger than one
scraped from a page body, and both are stronger than one that appears once
with no corroboration.
"""
from __future__ import annotations

import collections
import dataclasses
import re
from typing import Iterable

# `_PREFIX_BLOCKLIST` is imported, not copied. The two files held
# character-for-character identical copies, so adding a prefix to one
# would leave the harvest proposals a human approves disagreeing with
# what the live matcher extracts from the same corpus.
from .matching import (
    _PREFIX_BLOCKLIST,
    address_key,
    extract_permit_numbers,
    normalize_permit,
)
from .model import ProjectRecord
from .sources import is_jurisdiction

#: What each prefix means in the jurisdictions PCD works in. A project
#: usually has several related numbers -- the building permit, its site
#: plan, its grading review -- and only one of them belongs in "Permit
#: Number". Labelling them lets a human pick without opening the portal.
PERMIT_KINDS: dict[str, str] = {
    "CTR": "Phoenix plot plan / residential construction",
    "SCSR": "Phoenix site plan review",
    "CGD": "Phoenix grading & drainage",
    "CSL": "Phoenix civil / streets",
    "RVSN": "Phoenix revision to a standard plan",
    "SPR": "Phoenix standard plan",
    "RPDR": "Phoenix residential plan review",
    "PRDV": "Phoenix pre-development",
    "DEDI": "Phoenix dedication",
    "PAPP": "Phoenix pre-application",
    "BLDR": "Scottsdale / county building permit",
    "RACC": "Gilbert residential accessory",
    "COMM": "Gilbert commercial",
    "MRAPP": "Maricopa County application",
    "RESD": "Maricopa County residential",
    "KIVA": "Phoenix legacy KIVA record",
    "AMND": "Phoenix amendment",
}


def permit_kind(permit: str) -> str:
    head = re.match(r"[A-Za-z]+", permit.strip())
    return PERMIT_KINDS.get(head.group(0).upper(), "") if head else ""

#: A permit number is either a known jurisdiction prefix, or a hyphenated
#: letter-digit code. Requiring one of those is what keeps street addresses
#: and phone numbers out of a proposal a human is meant to trust.
_HYPHENATED = re.compile(r"^[A-Z]{2,6}-\d{2,10}(?:-\d{1,8})?$", re.IGNORECASE)


@dataclasses.dataclass
class Evidence:
    permit: str
    source: str          # "jurisdiction_email" | "internal_email" | "notion_page"
    sender: str
    date: str
    subject: str

    @property
    def weight(self) -> int:
        return {"jurisdiction_email": 10,
                "notion_page": 4,
                "internal_email": 2}.get(self.source, 1)


@dataclasses.dataclass
class Proposal:
    project: ProjectRecord
    permit: str
    score: int
    evidence: list[Evidence]

    @property
    def confidence(self) -> str:
        """Only a jurisdiction writing the number counts as strong."""
        jurisdictional = [e for e in self.evidence
                          if e.source == "jurisdiction_email"]
        if len(jurisdictional) >= 2:
            return "high"
        if jurisdictional:
            return "medium"
        return "low"


def _plausible(permit: str) -> bool:
    candidate = " ".join(permit.split())
    normalized = normalize_permit(candidate)
    if len(normalized) < 7:
        return False

    head = re.match(r"[A-Za-z]+", candidate)
    prefix = head.group(0).upper() if head else ""
    if prefix in _PREFIX_BLOCKLIST:
        return False

    if prefix and prefix in PERMIT_KINDS:
        return True
    if _HYPHENATED.match(candidate):
        return True
    # A bare number is only a permit if it is long enough not to be a zip
    # code, a year, or a street number.
    return normalized.isdigit() and len(normalized) >= 8


def harvest(targets: list[ProjectRecord],
            messages: Iterable[dict],
            page_text: dict[str, str] | None = None) -> list[Proposal]:
    """Return ranked permit-number proposals, each carrying its evidence."""
    by_key: dict[str, list[ProjectRecord]] = collections.defaultdict(list)
    for project in targets:
        key = address_key(project.address or project.name)
        if key:
            by_key[key].append(project)

    found: dict[tuple[str, str], list[Evidence]] = collections.defaultdict(list)

    for message in messages or []:
        subject = message.get("subject") or ""
        body = message.get("plaintextBody") or message.get("snippet") or ""
        sender = message.get("sender", "")
        text = f"{subject}\n{body}"
        source = ("jurisdiction_email" if is_jurisdiction(sender)
                  else "internal_email")

        # Which target does this message concern? Address only -- these
        # projects have no permit number yet, so that is all there is.
        hits: list[ProjectRecord] = []
        for line in [subject] + body.splitlines()[:40]:
            key = address_key(line)
            if key and key in by_key:
                hits.extend(by_key[key])
                break
        if not hits:
            continue

        permits = [p for p in extract_permit_numbers(text) if _plausible(p)]
        for project in {p.page_id: p for p in hits}.values():
            for permit in permits:
                found[(project.page_id, normalize_permit(permit))].append(
                    Evidence(permit, source, sender,
                             str(message.get("date") or "")[:16],
                             subject[:80]))

    for page_id, text in (page_text or {}).items():
        project = next((p for p in targets if p.page_id == page_id), None)
        if project is None:
            continue
        for permit in extract_permit_numbers(text):
            if not _plausible(permit):
                continue
            found[(page_id, normalize_permit(permit))].append(
                Evidence(permit, "notion_page", "(Notion page body)", "", ""))

    proposals: list[Proposal] = []
    for (page_id, _normalized), evidence in found.items():
        project = next(p for p in targets if p.page_id == page_id)
        display = collections.Counter(e.permit for e in evidence).most_common(1)[0][0]
        proposals.append(Proposal(project, display,
                                  sum(e.weight for e in evidence), evidence))

    proposals.sort(key=lambda p: (p.project.name, -p.score))
    return proposals


def render(targets: list[ProjectRecord], proposals: list[Proposal]) -> str:
    by_project: dict[str, list[Proposal]] = collections.defaultdict(list)
    for proposal in proposals:
        by_project[proposal.project.page_id].append(proposal)

    lines = ["=" * 78,
             "PERMIT NUMBER PROPOSALS  (read-only - nothing written to Notion)",
             "=" * 78, ""]
    covered = 0
    for project in targets:
        entries = by_project.get(project.page_id, [])
        lines.append(f"{project.name}")
        lines.append(f"  stage: {project.stage or '-'}   page: {project.page_id[:8]}")
        if not entries:
            lines.append("  NO CANDIDATE FOUND - needs a portal lookup or Jay's input")
            lines.append("")
            continue
        covered += 1
        for entry in entries[:4]:
            jurisdictional = [e for e in entry.evidence
                              if e.source == "jurisdiction_email"]
            kind = permit_kind(entry.permit)
            lines.append(f"  -> {entry.permit:<24} confidence={entry.confidence}"
                         f"  score={entry.score}"
                         + (f"   [{kind}]" if kind else ""))
            for item in (jurisdictional or entry.evidence)[:2]:
                where = item.sender or item.source
                when = f" {item.date}" if item.date else ""
                lines.append(f"       from {where}{when}")
                if item.subject:
                    lines.append(f"       \"{item.subject}\"")
        lines.append("")
    lines.append(f"{covered} of {len(targets)} projects have at least one candidate.")
    lines.append("")
    lines.append("Approve or correct these, and they go into Notion as one batch.")
    lines.append("Nothing is written until you say so.")
    return "\n".join(lines)
