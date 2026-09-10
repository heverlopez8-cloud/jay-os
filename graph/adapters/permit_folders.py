"""Local permit working folders: `permits/<PERMIT#>/`.

Each folder is a permit we are actively working, and the files in it are the
documents produced for it -- narratives, cover letters, build scripts. The
folder NAME is the permit number, which is why this adapter can assert a
document/permit relationship rather than infer one.

Reads only. The engine never moves, renames or rewrites a working file.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

from .base import SourceRecord, utc_stamp

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "permits"

#: Extensions worth treating as project documents. Build scripts and caches
#: are work-in-progress, not deliverables, and would only add isolated nodes.
DOCUMENT_SUFFIXES = {".pdf", ".docx", ".doc", ".txt", ".md", ".csv", ".xlsx"}

_SKIP_DIRS = {"__pycache__", ".git", ".ipynb_checkpoints"}


class PermitFolders:
    """Adapter over local permit working directories."""

    name = "permit_folders"

    def __init__(self, root: str | Path = DEFAULT_ROOT) -> None:
        self.root = Path(root)

    def records(self) -> Iterator[SourceRecord]:
        if not self.root.exists():
            return
        for folder in sorted(p for p in self.root.iterdir() if p.is_dir()):
            if folder.name in _SKIP_DIRS:
                continue
            yield SourceRecord(
                kind="permit_folder",
                source=self.name,
                source_ref=str(folder.relative_to(self.root.parent)),
                observed_at=utc_stamp(None),
                payload={"permit_number": folder.name, "path": str(folder)})
            for item in sorted(folder.iterdir()):
                if (not item.is_file() or item.name.startswith(".")
                        or item.suffix.lower() not in DOCUMENT_SUFFIXES):
                    continue
                stat = item.stat()
                yield SourceRecord(
                    kind="document",
                    source=self.name,
                    source_ref=str(item.relative_to(self.root.parent)),
                    observed_at=utc_stamp(
                        __import__("datetime").datetime.fromtimestamp(
                            stat.st_mtime, __import__("datetime").timezone.utc)),
                    payload={
                        "permit_number": folder.name,
                        "filename": item.name,
                        "path": str(item),
                        "bytes": stat.st_size,
                    })
