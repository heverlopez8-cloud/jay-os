"""Jurisdiction permit-portal CSV exports.

A portal export is the jurisdiction's own record: permit number, status, fees,
parcel, registered contractor, owner. Those fields are ASSERTED facts about the
permit. Which of OUR projects a row belongs to is an INFERENCE, and is left to
the pipeline's resolution step rather than decided here.

Column names follow the City of Phoenix export seen on 2026-09-09; unknown
columns are ignored rather than guessed at.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterator

from .base import SourceRecord, utc_stamp


class PortalCsv:
    """Adapter over one downloaded permit export."""

    name = "portal_csv"

    def __init__(self, path: str | Path, jurisdiction_hint: str = "") -> None:
        self.path = Path(path)
        self.jurisdiction_hint = jurisdiction_hint

    def records(self) -> Iterator[SourceRecord]:
        if not self.path.exists():
            raise FileNotFoundError(
                f"no portal export at {self.path}. A missing export is an "
                f"error, not an empty one -- zero rows and an unreadable file "
                f"look identical downstream.")
        with open(self.path, newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                permit = (row.get("Permit Number") or "").strip()
                if not permit:
                    continue
                street = " ".join(part for part in (
                    row.get("Prefix Direction"), row.get("Street Name"),
                    row.get("Suffix Street Type")) if part)
                yield SourceRecord(
                    kind="portal_permit",
                    source=self.name,
                    source_ref=f"{self.path.name}#{permit}",
                    observed_at=utc_stamp(row.get("Issued Start Date")),
                    payload={
                        "permit_number": permit,
                        "permit_name": (row.get("Permit Name") or "").strip(),
                        "work_type": (row.get("Permit Work Type") or "").strip(),
                        "status": (row.get("Status") or "").strip(),
                        "number": (row.get("Number") or "").strip(),
                        "street": street.strip(),
                        "city": (row.get("City") or "").strip(),
                        "zip": (row.get("Zip/Postal Code") or "").strip(),
                        "apn": (row.get("Parcel Number") or "").strip(),
                        "contractor": (row.get("Registered Contractor Name") or "").strip(),
                        "roc_license": (row.get("ROC License #") or "").strip(),
                        "owner_name": (row.get("Owner Name") or "").strip(),
                        "total_fees": (row.get("Total Fees") or "").strip(),
                        "total_payments": (row.get("Total Payments") or "").strip(),
                        "expiration_date": (row.get("Expiration Date") or "").strip(),
                        "completion_date": (row.get("Completion Date") or "").strip(),
                        "jurisdiction_hint": self.jurisdiction_hint,
                    })
