"""Which jurisdiction an address sits in.

JAY-OS addresses arrive as one flat string -- "18882 E VALLEJO ST QUEEN CREEK
85142" -- with no comma to separate city from street. Parsing that by position
is guesswork, so this matches against a curated list of the jurisdictions PCD
actually works in, longest name first so "QUEEN CREEK" wins over "QUEEN".

A city token found this way is an INFERENCE, not an assertion. It is recorded
below the auto-link gate so it can never silently become production truth --
`MUNICIPAL_CONFIDENCE` is deliberately 0.93.

Unknown cities return None. The engine would rather leave a property with no
jurisdiction (visible as an isolated-ish node) than attach it to a guess.
"""
from __future__ import annotations

import re

#: Confidence for a jurisdiction derived from a city token in an address.
#: Below AUTO_LINK_CONFIDENCE (0.95) on purpose: strong, but still an inference.
MUNICIPAL_CONFIDENCE = 0.93

#: Confidence when the source names the city in its own dedicated field
#: (a portal export's City column). That is the jurisdiction's own record.
MUNICIPAL_ASSERTED_CONFIDENCE = 1.0

#: Jurisdictions seen in PCD's book of work plus immediate neighbours.
#: Extending this is a reviewed change, like any other vocabulary.
ARIZONA_JURISDICTIONS = (
    "QUEEN CREEK", "APACHE JUNCTION", "SAN TAN VALLEY", "CASA GRANDE",
    "FOUNTAIN HILLS", "PARADISE VALLEY", "LITCHFIELD PARK", "CAVE CREEK",
    "QUEEN VALLEY", "GOLD CANYON", "SUN CITY WEST", "SUN CITY",
    "NEW RIVER", "EL MIRAGE", "CAREFREE", "WICKENBURG", "GOODYEAR",
    "AVONDALE", "TOLLESON", "SURPRISE", "GLENDALE", "CHANDLER",
    "SCOTTSDALE", "BUCKEYE", "PEORIA", "GILBERT", "TEMPE", "MESA",
    "PHOENIX", "MARICOPA", "FLORENCE", "COOLIDGE", "LAVEEN", "ANTHEM",
    "ELOY",
)

_BY_LENGTH = tuple(sorted(ARIZONA_JURISDICTIONS, key=len, reverse=True))


def jurisdiction_from_address(address: str) -> str | None:
    """Return the canonical jurisdiction name, or None when unsure.

    Matches on word boundaries so "MESA" does not fire inside "MESAVERDE".
    """
    if not address:
        return None
    text = " ".join(str(address).upper().split())
    for city in _BY_LENGTH:
        if re.search(rf"\b{re.escape(city)}\b", text):
            return city.title()
    return None


def canonical_jurisdiction(name: str) -> str:
    """Normalise a jurisdiction name from any source to one spelling."""
    text = " ".join(str(name or "").upper().split())
    text = re.sub(r"^(TOWN|CITY|COUNTY)\s+OF\s+", "", text)
    text = re.sub(r",?\s*(AZ|ARIZONA)$", "", text).strip()
    return text.title() if text else ""
