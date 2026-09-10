"""Borrow the production extractors and confidence gates. Never copy them.

JAY-OS already knows how to pull a permit number, an APN, an address key and
a jurisdiction out of text (`email_sentinel.extraction`, itself an adapter
onto `permit_pipeline.matching`), and it already owns the numbers that decide
when a match may be acted on (`email_sentinel.contract`).

Re-implementing either here would create a second source of truth that drifts.
The permit regex in particular carries scar tissue -- a forwarded email once
produced permit "PLLC 27107" at 0.99 confidence -- and a fresh copy would not.

So this module locates the installed sentinel and imports it. If it cannot,
extraction-dependent steps RAISE. They do not fall back to a weaker local
guess, because a quietly degraded extractor is indistinguishable from a quiet
week, and the gates are production truth that this layer has no authority to
soften.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SENTINEL_SRC = _REPO / "apps/email-sentinel/src"
_ORCHESTRATOR = _REPO / "orchestrator"


class ReuseUnavailable(RuntimeError):
    """The production extractors could not be imported."""


def _ensure_path() -> None:
    for candidate in (_SENTINEL_SRC, _ORCHESTRATOR):
        text = str(candidate)
        if candidate.exists() and text not in sys.path:
            sys.path.insert(0, text)


def extraction():
    """`email_sentinel.extraction` -- permit numbers, APNs, address keys."""
    _ensure_path()
    try:
        from email_sentinel import extraction as module
    except Exception as error:  # pragma: no cover - environment dependent
        raise ReuseUnavailable(
            f"cannot import email_sentinel.extraction from {_SENTINEL_SRC}. "
            f"The graph engine will not substitute a weaker extractor: "
            f"{error}") from error
    return module


def gates():
    """Production confidence gates. Imported, never redefined.

    Returns (auto_link, propose, min_classification). The graph engine uses
    the same thresholds as the matcher so one vocabulary of 'confident'
    exists across JAY-OS.
    """
    _ensure_path()
    try:
        from email_sentinel.contract import (
            AUTO_LINK_CONFIDENCE, MIN_CLASSIFICATION_CONFIDENCE,
            PROPOSE_CONFIDENCE)
    except Exception as error:  # pragma: no cover - environment dependent
        raise ReuseUnavailable(
            f"cannot import the production confidence gates: {error}. "
            f"Refusing to invent local thresholds.") from error
    return (AUTO_LINK_CONFIDENCE, PROPOSE_CONFIDENCE,
            MIN_CLASSIFICATION_CONFIDENCE)


def projects_loader():
    """`email_sentinel.projects.load` -- the existing project catalogue."""
    _ensure_path()
    try:
        from email_sentinel.projects import load
    except Exception as error:  # pragma: no cover - environment dependent
        raise ReuseUnavailable(f"cannot import the project loader: {error}") from error
    return load


def available() -> bool:
    """True when the production modules can be imported here."""
    try:
        extraction()
        gates()
    except ReuseUnavailable:
        return False
    return True
