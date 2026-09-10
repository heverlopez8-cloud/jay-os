"""Resolve production credentials from the paths JAY-OS already uses.

Nothing new is invented here. Secrets live where the rest of the system
already keeps them: the orchestrator's own `.env`, and the Hermes agent
store at `%LOCALAPPDATA%\\hermes\\.env`, which is where `NOTION_API_KEY`
and the `EMAIL_*` transport settings are configured today.

Resolution order is process environment, then orchestrator `.env`, then
Hermes `.env`. A missing credential is reported, never worked around --
`require()` raises so the pipeline stops instead of quietly degrading to
simulated writes.
"""
from __future__ import annotations

import os
from pathlib import Path

from .ports import ConnectorError

#: Searched in order; the first file that defines a key wins.
CANDIDATE_ENV_FILES: tuple[Path, ...] = (
    Path(__file__).resolve().parents[1] / ".env",
    Path("/mnt/c/Users/profe/AppData/Local/hermes/.env"),
    Path(os.path.expandvars(r"%LOCALAPPDATA%\hermes\.env"))
    if os.name == "nt" else Path("/nonexistent"),
)


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in values:
            values[key] = value
    return values


_cache: dict[str, str] | None = None


def _all_values() -> dict[str, str]:
    global _cache
    if _cache is None:
        merged: dict[str, str] = {}
        for path in CANDIDATE_ENV_FILES:
            for key, value in _parse_env_file(path).items():
                merged.setdefault(key, value)
        _cache = merged
    return _cache


def get(name: str, default: str | None = None) -> str | None:
    """Process environment first, then the configured JAY-OS env files."""
    from_process = os.getenv(name)
    if from_process:
        return from_process
    return _all_values().get(name, default)


def require(name: str, purpose: str) -> str:
    """Fetch a credential or refuse to continue.

    Failing closed here is the whole point: a pipeline that runs without
    Notion credentials would report success while writing nowhere.
    """
    value = get(name)
    if not value:
        searched = ", ".join(str(p) for p in CANDIDATE_ENV_FILES
                             if p.name == ".env")
        raise ConnectorError(
            f"{name} is not configured, so {purpose} cannot run. "
            f"Searched the process environment and: {searched}. "
            f"Refusing to continue rather than simulate the write."
        )
    return value


def source_of(name: str) -> str:
    """Where a credential came from -- for health reporting, never its value."""
    if os.getenv(name):
        return "process environment"
    for path in CANDIDATE_ENV_FILES:
        if name in _parse_env_file(path):
            return str(path)
    return "not found"
