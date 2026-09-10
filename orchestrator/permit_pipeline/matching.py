"""Identify which project an event belongs to -- or refuse to guess.

Match order is strongest-evidence-first: permit number, then APN, then
street address. Address alone is deliberately weak: 7714 E Onyx Ct is two
live projects (MAIN HOUSE / CASITA) that differ only by permit number, so
an address-only hit against several records is reported as ambiguous
rather than resolved by picking one.
"""
from __future__ import annotations

import re

from .model import Match, ProjectRecord

#: Below this the caller raises PROJECT_NOT_MATCHED.
MIN_CONFIDENCE = 0.70

_PERMIT_PATTERNS = (
    r"\b[A-Z]{2,6}[-\s]?\d{2,4}[-\s]?\d{3,6}\b",   # RACC-2026-00041, BLDR2502290
    r"\b\d{4,8}[-\s]?[A-Z]{3,6}\b",                 # 2503512-SCSR
    r"\b[A-Z]{3,6}[-\s]?\d{6,9}\b",                 # CTR-102605788, SCSR 2602467
)

_STREET_SUFFIXES = {
    "ST", "STREET", "AVE", "AVENUE", "DR", "DRIVE", "RD", "ROAD", "LN",
    "LANE", "CT", "COURT", "PL", "PLACE", "WAY", "BLVD", "CIR", "CIRCLE",
    "TER", "TRL", "PKWY", "HWY",
}
_DIRECTIONALS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}
_NOISE_WORDS = {
    "MAIN", "HOUSE", "CASITA", "ADU", "LOT", "UNIT", "AZ", "ARIZONA",
    "PHOENIX", "SCOTTSDALE", "GILBERT", "MESA", "TEMPE", "CHANDLER",
    "GLENDALE", "PEORIA", "SURPRISE", "WITTMANN", "AVONDALE", "BUCKEYE",
    "GOODYEAR", "TUCSON", "MARANA",
}


def normalize_permit(value: str) -> str:
    """RACC-2026-00041, RACC 2026 00041 and racc202600041 are one permit."""
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def permit_keys(value: str) -> set[str]:
    """Every form a permit number is written in, as comparable keys.

    Phoenix writes "SCSR 2602283" in correspondence while the same permit is
    recorded as "2602283-SCSR" in Notion. Those are one permit, and matching
    on the literal string alone silently misses every message about it.

    Only a simple prefix/suffix swap is folded together -- one run of letters
    and one run of digits. Numbers with several digit groups are left alone,
    because reordering those would merge genuinely different permits.
    """
    strict = normalize_permit(value)
    keys = {strict}
    parts = re.findall(r"[A-Z]+|[0-9]+", strict)
    if len(parts) == 2:
        letters = [p for p in parts if p.isalpha()]
        digits = [p for p in parts if p.isdigit()]
        if len(letters) == 1 and len(digits) == 1:
            keys.add(letters[0] + digits[0])
    return keys


#: Word-parts that appear in addresses, phone numbers and email signatures
#: and get mistaken for permit prefixes. This list lived only in `harvest`,
#: where it guarded human-reviewed proposals -- so the *matching* path had no
#: guard at all. Shadow run 2026-09-05 proved the cost: a law-firm signature
#: in a forwarded email produced permit "PLLC 27107" and a 0.99-confidence
#: assignment to 1848 E Yale. Extraction is the right place for it, because
#: everything downstream trusts what this function returns.
_PREFIX_BLOCKLIST = frozenset({
    "AZ", "PLLC", "LLC", "INC", "PC", "TEAM", "CELL", "WWW", "PHX", "STE",
    "SUITE", "APT", "UNIT", "FLOOR", "PO", "FAX", "TEL", "PH", "MOBILE",
    "OFFICE", "EXT", "ZIP", "USA", "ST", "AVE", "RD", "DR", "LN", "CT",
})


def extract_permit_numbers(text: str) -> tuple[str, ...]:
    """Permit numbers as written, e.g. 'RACC-2026-00041'.

    The readable form is what goes in front of a person -- they have to type
    it into a jurisdiction portal. Comparison always goes through
    `normalize_permit`, so formatting differences never affect matching.
    """
    upper = text.upper()
    found: list[str] = []
    seen: set[str] = set()
    for pattern in _PERMIT_PATTERNS:
        for hit in re.findall(pattern, upper):
            display = " ".join(hit.split())
            normalized = normalize_permit(display)
            # Bare years, zips and phone fragments are not permit numbers.
            if len(normalized) < 6:
                continue
            leading = re.match(r"[A-Z]+", normalized)
            if leading and leading.group(0) in _PREFIX_BLOCKLIST:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            found.append(display)
    return tuple(found)


def normalize_apn(value: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", value.upper())


def extract_apns(text: str) -> tuple[str, ...]:
    hits = re.findall(r"\b\d{3}[-\s]\d{2}[-\s]\d{3}[A-Z]?\b", text.upper())
    return tuple(dict.fromkeys(normalize_apn(h) for h in hits))


def address_key(value: str) -> str:
    """Reduce an address to 'number + directional + street name'.

    '637 N RIATA ST GILBERT 85234', '637 N Riata st - please advise' and
    '637 N RIATA ST GILBERT 85234 CASITA' all reduce to '637N RIATA'.
    The street suffix terminates the name, which is what stops trailing
    prose in an email body from polluting the key.
    """
    upper = re.sub(r"[^A-Z0-9\s]", " ", value.upper())
    tokens = upper.split()
    number = ""
    directional = ""
    name: list[str] = []
    suffix_seen = False

    for token in tokens:
        if not number:
            # Street numbers are short; long digit runs are permit numbers.
            if token.isdigit() and len(token) <= 6:
                number = token
            continue
        if not name and not directional and token in _DIRECTIONALS:
            directional = token
            continue
        if token in _STREET_SUFFIXES:
            if name:
                suffix_seen = True
                break
            continue
        if token in _NOISE_WORDS or token.isdigit():
            # Restart if we hit noise before finding any street name at all.
            if not name:
                number = ""
                directional = ""
            continue
        name.append(token)
        if len(name) >= 2:
            break

    if not number or not name:
        return ""
    if not suffix_seen and len(name) > 1:
        # No suffix to anchor on: trust only a single-token street name.
        name = name[:1]
    return f"{number}{directional} {' '.join(name)}"


def _project_permits(project: ProjectRecord) -> set[str]:
    """Every permit number on a project, normalized for comparison."""
    permits: set[str] = set()
    for raw in project.permit_numbers:
        for found in extract_permit_numbers(raw):
            permits.update(permit_keys(found))
        normalized = normalize_permit(raw)
        if len(normalized) >= 6:
            permits.update(permit_keys(raw))
    return permits


def _addresses_in(text: str) -> set[str]:
    keys = {address_key(text)}
    for line in text.splitlines():
        keys.add(address_key(line))
    keys.discard("")
    return keys


def match_project(text: str, projects: list[ProjectRecord]) -> Match:
    """Return the single project this event belongs to, or an honest miss."""
    permits: set[str] = set()
    for found in extract_permit_numbers(text):
        permits.update(permit_keys(found))
    addresses = _addresses_in(text)
    addressed = [p for p in projects
                 if address_key(p.address or p.name) in addresses]

    if permits:
        hits = [p for p in projects if _project_permits(p) & permits]
        if len(hits) > 1:
            # Several projects own a number in this message. Some numbers are
            # shared -- a standard plan belongs to every house built from it --
            # so the project whose address is also written here wins.
            corroborated = [p for p in hits if p in addressed]
            if len(corroborated) == 1:
                return Match(corroborated[0], 0.97, "permit_number+address")
            return Match(
                None, 0.0, "permit_number_ambiguous",
                tuple(p.name for p in hits),
            )
        if len(hits) == 1:
            # A lone permit match is still not enough when the message is
            # plainly about several properties at once. A row of infill lots
            # gets discussed in one thread, and a standard-plan number shared
            # across them would otherwise hand the whole thread to whichever
            # record happens to own that number.
            distinct = {address_key(p.address or p.name) for p in addressed}
            distinct.discard("")
            if len(distinct) > 1:
                names = [hits[0].name] + [p.name for p in addressed
                                          if p.page_id != hits[0].page_id]
                return Match(None, 0.0, "multiple_properties_in_message",
                             tuple(dict.fromkeys(names)))
            # The message is about a different property than the permit's
            # owner: that is a shared number capturing someone else's mail.
            others = [p for p in addressed if p.page_id != hits[0].page_id]
            if hits[0] not in addressed and len(others) == 1:
                return Match(others[0], 0.9, "address_over_shared_permit")
            return Match(hits[0], 0.99, "permit_number")

    apns = set(extract_apns(text))
    if apns:
        hits = [
            p for p in projects
            if p.apn and normalize_apn(p.apn) in apns
        ]
        if len(hits) == 1:
            return Match(hits[0], 0.9, "apn")
        if len(hits) > 1:
            return Match(None, 0.0, "apn_ambiguous", tuple(p.name for p in hits))

    keys = {address_key(text)}
    for line in text.splitlines():
        key = address_key(line)
        if key:
            keys.add(key)
    keys.discard("")
    if keys:
        hits = [
            p for p in projects
            if address_key(p.address or p.name) in keys
        ]
        if len(hits) == 1:
            return Match(hits[0], 0.8, "address")
        if len(hits) > 1:
            # Same address, different scopes (MAIN HOUSE vs CASITA).
            # A permit number is required to tell them apart.
            return Match(
                None, 0.0, "address_ambiguous",
                tuple(p.name for p in hits),
            )

    return Match(None, 0.0, "no_signal")


def is_confident(match: Match) -> bool:
    return match.project is not None and match.confidence >= MIN_CONFIDENCE
