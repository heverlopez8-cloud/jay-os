"""Jurisdiction portal ingest, for review outcomes that never reach email.

7714 E Onyx Ct failed review a second time on 2026-09-02 and the only
place that fact existed was the Scottsdale Civic Access portal. No email
pipeline can catch that, so the portal has to be watched directly.

Two things live here and they are deliberately separate:

* `detect_transition` -- pure state-change logic. It compares the last
  observed status to the current one and decides whether the change is
  material. This is what stops a poller re-reporting the same page.
* `CivicAccessClient` -- the Tyler EnerGov Civic Access reader.

A detected transition is turned into a normalized `RawEvent` and handed to
the *same* `PermitPipeline` that email events go through. There is no
second copy of the business rules.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import urllib.error
import urllib.parse
import urllib.request

from .audit import AuditStore
from .model import Channel, Exception_, ExceptionCode, RawEvent, iso, now
from .ports import ConnectorError

#: Portal status -> the wording a jurisdiction would have emailed. Feeding
#: the shared classifier real phrasing keeps one implementation of "what
#: does this event mean", rather than a parallel portal-only ruleset.
STATUS_PHRASING: dict[str, str] = {
    "rejected": "Plan review completed with a result of Rejected. "
                "The plans are returned for corrections.",
    "not approved": "Plan review completed: not approved. "
                    "The plans are returned for corrections.",
    "review failed": "Review Failed Submittal Notification. "
                     "The plans are returned for corrections.",
    "corrections": "Your plans have been reviewed and are being returned "
                   "for corrections.",
    "revisions required": "Revisions required. The plans are being returned "
                          "for corrections.",
    "resubmittal required": "A resubmittal is required for this permit "
                            "application.",
    "approved": "Plan review complete. The plans have been approved.",
    "issued": "The permit has been issued.",
    "ready to issue": "Review complete and approved; the permit is ready to "
                      "issue and fees are due.",
    "fees due": "Fees are due on this permit before it can be issued.",
    "inspection scheduled": "An inspection has been scheduled for this permit.",
}

#: Statuses that never need a human. Everything else is treated as material
#: so an unrecognised status is surfaced rather than swallowed.
NON_MATERIAL = frozenset({
    "", "in review", "under review", "submitted", "received", "accepted",
    "in progress", "pending", "assigned",
})


@dataclasses.dataclass(frozen=True)
class PortalSnapshot:
    jurisdiction: str
    permit_number: str
    status: str
    review_status: str = ""
    updated_at: str | None = None
    record_url: str = ""

    @property
    def signature(self) -> str:
        return f"{self.status.strip().lower()}|{self.review_status.strip().lower()}"


@dataclasses.dataclass(frozen=True)
class Transition:
    snapshot: PortalSnapshot
    previous: str
    current: str
    material: bool
    reason: str


def detect_transition(
    previous_status: str | None,
    previous_review: str | None,
    snapshot: PortalSnapshot,
) -> Transition:
    """Decide whether the portal has actually changed in a way that matters."""
    was = f"{(previous_status or '').strip().lower()}|" \
          f"{(previous_review or '').strip().lower()}"
    now_sig = snapshot.signature

    if previous_status is None and previous_review is None:
        # First sight of a permit is a baseline, not an event -- otherwise
        # enrolling a project would fire an alert for every existing status.
        return Transition(snapshot, was, now_sig, False, "first_observation")

    if was == now_sig:
        return Transition(snapshot, was, now_sig, False, "unchanged")

    current_terms = now_sig.replace("|", " ")
    if all(term not in current_terms for term in _MATERIAL_TERMS):
        if snapshot.status.strip().lower() in NON_MATERIAL and \
                snapshot.review_status.strip().lower() in NON_MATERIAL:
            return Transition(snapshot, was, now_sig, False,
                              "changed_to_non_actionable_status")
    return Transition(snapshot, was, now_sig, True, "material_status_change")


_MATERIAL_TERMS = tuple(STATUS_PHRASING.keys())


def phrasing_for(snapshot: PortalSnapshot) -> str:
    """Best matching jurisdiction wording for this status."""
    haystack = f"{snapshot.status} {snapshot.review_status}".lower()
    for key, phrase in STATUS_PHRASING.items():
        if key in haystack:
            return phrase
    # Unknown status: describe it plainly and let the classifier's confidence
    # floor route it to a human instead of guessing.
    return (f"Portal status changed to '{snapshot.status}'"
            f"{' / ' + snapshot.review_status if snapshot.review_status else ''}.")


def to_raw_event(transition: Transition, observed_at: dt.datetime | None = None) -> RawEvent:
    """Normalize a portal transition into the same event shape as email."""
    snapshot = transition.snapshot
    moment = observed_at or now()
    return RawEvent(
        source=Channel.JURISDICTION_PORTAL,
        # Includes the TRANSITION and the portal's own timestamp, not just the
        # destination state. Keying on the destination alone made a return to
        # a prior state collide with the first visit: Rejected -> In Review ->
        # Rejected produced one external_id, so `store.seen` logged the second
        # rejection as "duplicate_ignored" and released it. That is exactly the
        # 7714 E Onyx second review failure -- the event this pipeline exists
        # to catch -- being eaten by its own idempotency.
        #
        # The fallback keeps the FULL observation timestamp. Truncated to
        # `[:10]` it was a calendar date, so when the portal omits
        # `LastUpdatedDate` -- `_to_snapshot` reads `entry.get(...)` with no
        # default, so None is a live possibility -- a second Rejected on the
        # same day rebuilt the first one's external_id and `store.seen`
        # swallowed that one too. A poll that re-detects a single transition
        # is still covered: the statement digest gate suppresses it
        # downstream, before the Notion write.
        external_id=(f"{snapshot.jurisdiction}:{snapshot.permit_number}:"
                     f"{transition.previous}->{snapshot.signature}"
                     f"@{snapshot.updated_at or iso(moment)}"),
        received_at=moment,
        subject=(f"{snapshot.jurisdiction} portal: {snapshot.permit_number} "
                 f"status changed to {snapshot.status}"),
        body=(
            f"{phrasing_for(snapshot)}\n\n"
            f"Permit {snapshot.permit_number}.\n"
            f"Previous status: {transition.previous.replace('|', ' / ')}\n"
            f"Current status:  {transition.current.replace('|', ' / ')}\n"
            f"{snapshot.record_url}\n"
        ),
        sender=f"portal@{snapshot.jurisdiction.lower().replace(' ', '')}",
    )


class PortalProducer:
    """Polls a portal, detects transitions, feeds the shared pipeline."""

    def __init__(self, store: AuditStore, client, pipeline,
                 jurisdiction: str, watcher: str) -> None:
        self.store = store
        self.client = client
        self.pipeline = pipeline
        self.jurisdiction = jurisdiction
        self.watcher = watcher

    def _advance(self, permit: str, snapshot: PortalSnapshot) -> None:
        """Move the stored baseline forward. Only ever after a safe handoff."""
        self.store.save_portal_snapshot(
            self.jurisdiction, permit, snapshot.status,
            snapshot.review_status, snapshot.updated_at,
        )

    def poll(self, permits: list[str]) -> dict[str, list[str]]:
        from . import health

        result: dict[str, list[str]] = {
            "events": [], "unchanged": [], "errors": [],
        }
        for permit in permits:
            try:
                snapshot = self.client.fetch(permit)
            except ConnectorError as error:
                # A portal we cannot read is a blind spot, and a blind spot
                # nobody knows about is the original failure. Raise it.
                result["errors"].append(f"{permit}: {error}")
                self.store.record_exception(Exception_(
                    f"PORTAL-{self.jurisdiction}-{permit}",
                    ExceptionCode.CONNECTOR_FAILED,
                    f"could not read {self.jurisdiction} portal for {permit}: "
                    f"{error}",
                ))
                self.store.log(f"PORTAL-{permit}", "portal_poll", "failed",
                               error=str(error))
                continue

            row = self.store.portal_snapshot(self.jurisdiction, permit)
            transition = detect_transition(
                row["status"] if row else None,
                row["review_status"] if row else None,
                snapshot,
            )
            if not transition.material:
                # Nothing to hand off, so advancing the baseline loses nothing.
                self._advance(permit, snapshot)
                result["unchanged"].append(permit)
                continue

            # The snapshot is the ONLY record that this transition is still
            # owed. Saving it before the handoff means a failure here erases
            # the event: the next poll compares against the new status, calls
            # it "unchanged", and the transition is never seen again. So the
            # baseline advances only after `process` has returned, so a failed
            # poll leaves the stored status behind and the next sweep detects
            # the same transition again. Delivery becomes at-least-once rather
            # than at-most-once; `store.seen` de-duplicates the replay on
            # `external_id`, and a duplicate alert is the survivable half of
            # that trade.
            raw = to_raw_event(transition)
            try:
                self.pipeline.process(raw)
            except Exception as error:  # noqa: BLE001 -- see below
                # Deliberately broad. `process` already turns ConnectorError
                # into a fail-closed exception row, so anything reaching here
                # is unexpected (a TypeError on a naive datetime, a KeyError
                # in property mapping). Letting it propagate would also skip
                # every permit after this one and the health heartbeat, so a
                # whole portal sweep would vanish on one bad record.
                result["errors"].append(f"{permit}: {error}")
                self.store.record_exception(Exception_(
                    f"PORTAL-{self.jurisdiction}-{permit}",
                    ExceptionCode.CONNECTOR_FAILED,
                    f"{self.jurisdiction} portal transition for {permit} "
                    f"({transition.previous} -> {transition.current}) could "
                    f"not be processed: {type(error).__name__}: {error}. The "
                    f"portal baseline was NOT advanced, so the next poll "
                    f"re-detects this transition.",
                ))
                self.store.log(f"PORTAL-{permit}", "portal_process", "failed",
                               error=str(error), previous=transition.previous,
                               current=transition.current)
                continue

            self._advance(permit, snapshot)
            result["events"].append(raw.event_id)

        health.record(
            self.store, self.watcher, not result["errors"],
            f"{len(permits)} permits, {len(result['events'])} transitions, "
            f"{len(result['errors'])} errors",
        )
        return result


class CivicAccessClient:
    """Tyler EnerGov Civic Access reader (Scottsdale and friends).

    Civic Access sits behind Tyler Portico OIDC; review outcomes for a
    permit are only visible to the account the application was filed
    under. Without that token this raises rather than returning a
    misleadingly empty status.
    """

    def __init__(self, base_url: str, access_token: str | None = None,
                 timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = access_token
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self._token)

    def fetch(self, permit_number: str) -> PortalSnapshot:
        if not self._token:
            raise ConnectorError(
                "Civic Access requires a Tyler Portico OIDC access token for "
                "the account the permit was filed under "
                "(info@professionalcadesign.com). None is configured, so the "
                "portal cannot be read. Refusing to report a status."
            )
        url = (f"{self.base_url}/apps/SelfService/api/energov/search/search")
        payload = {
            "Keyword": permit_number, "ExactMatch": True, "SearchModule": 2,
            "FilterModule": 2, "TabResultType": 2, "PageNumber": 0,
            "PageSize": 10, "SortBy": "relevance", "SortAscending": True,
        }
        request = urllib.request.Request(
            url, method="POST", data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            raise ConnectorError(
                f"Civic Access search {error.code} for {permit_number}: "
                f"{error.read().decode(errors='replace')[:200]}"
            ) from error
        except Exception as error:
            raise ConnectorError(
                f"Civic Access unreachable for {permit_number}: {error}"
            ) from error
        return self._to_snapshot(permit_number, body)

    def _to_snapshot(self, permit_number: str, body: dict) -> PortalSnapshot:
        results = (body.get("Result") or {}).get("EntityResults") or []
        for entry in results:
            if str(entry.get("CaseNumber", "")).upper() == permit_number.upper():
                return PortalSnapshot(
                    jurisdiction="City of Scottsdale",
                    permit_number=permit_number,
                    status=str(entry.get("StatusName") or entry.get("Status") or ""),
                    review_status=str(entry.get("WorkflowStatus") or ""),
                    updated_at=entry.get("LastUpdatedDate"),
                    record_url=f"{self.base_url}/apps/selfservice#/permit/"
                               f"{entry.get('Id', '')}",
                )
        raise ConnectorError(
            f"Civic Access returned no record matching {permit_number}"
        )
