"""The Phase 4 pipeline, end to end.

    NEW INFORMATION -> INGEST -> ENTITY EXTRACTION -> ENTITY RESOLUTION
      -> RELATION EXTRACTION -> PROVENANCE CHECK -> CANONICAL ENTITY UPDATE
      -> BACKLINK / GRAPH UPDATE -> CANDIDATE LESSON GENERATION

WHY ONE BAD RECORD MUST NOT ABORT AN INGEST
-------------------------------------------
A 451-project catalogue will contain a row with a malformed APN or a project
with no address. If one of those raises out of the loop, the other 450 never
land, and the failure mode is "the graph silently stopped updating" -- exactly
the class of failure `email_sentinel` exists to prevent. So every record is
processed inside a boundary: a refusal is RECORDED against the record and the
run continues. Refusals are reported, never swallowed.

WHAT IS ASSERTED VERSUS INFERRED
--------------------------------
Asserted (confidence 1.0, Disposition.ASSERTED) only when a source of truth
said it directly: Notion lists permit B26-2089 under this project; the folder
`permits/B26-2089/` contains this file; the portal's own City column names the
city. Everything else -- which of our projects a portal row belongs to, who
owns a property, which jurisdiction an address implies -- is INFERRED, carries
a sub-1.0 confidence, and is gated by the production thresholds. Inference
never becomes production truth here.
"""
from __future__ import annotations

import dataclasses
from typing import Any

from ._reuse import extraction, gates
from .adapters.base import Source, SourceRecord
from .contract import (Disposition, EntityType as E, RelationType as R,
                       VocabularyError)
from .places import (MUNICIPAL_ASSERTED_CONFIDENCE, MUNICIPAL_CONFIDENCE,
                     canonical_jurisdiction, jurisdiction_from_address)
from .resolve import resolve_or_create
from .store import GraphStore, StoreError

#: PCD itself. Clients are CLIENT_OF this company; it is the graph's root org.
OWNING_COMPANY = "Professional CAD Design LLC"

#: A portal row matched to one of our projects by parcel number. An APN is a
#: county-issued identifier, so agreement is strong -- above the auto-link gate.
APN_MATCH_CONFIDENCE = 0.96

#: Matched by street address only. Addresses are reused across cities and
#: mistyped in portals, so this proposes and waits for a human.
ADDRESS_MATCH_CONFIDENCE = 0.93

#: A client email listed against a project makes them our client.
CLIENT_CONFIDENCE = 0.95

#: A client associated with a project probably owns the property. Probably.
CLIENT_OWNS_CONFIDENCE = 0.80


@dataclasses.dataclass
class IngestReport:
    """What one ingest run did. Printed, and written to the event ledger."""

    source: str
    records: int = 0
    entities_created: int = 0
    entities_resolved: int = 0
    relationships_created: int = 0
    relationships_existing: int = 0
    candidate_merges: int = 0
    refusals: list[str] = dataclasses.field(default_factory=list)
    event_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        data["refusals"] = len(self.refusals)
        # Keep a bounded sample of the actual reasons in the event ledger, so
        # `lessons.mine_repeated_refusals` can notice the same one recurring.
        data["refusal_samples"] = self.refusals[:20]
        return data


class GraphPipeline:
    """Drives source records into the graph under the governance rules."""

    def __init__(self, store: GraphStore, actor: str = "graph-engine") -> None:
        self.store = store
        self.actor = actor
        self._report = IngestReport(source="")
        self._extract = extraction()
        self._auto_link, self._propose, _ = gates()

    # ------------------------------------------------------------------ helpers

    def _ensure(self, entity_type: E, name: str, *, source: str, source_ref: str,
                observed_at: str, key: str | None = None,
                external_id: str | None = None, email: str | None = None,
                attributes: dict[str, Any] | None = None,
                associations: tuple[str, ...] = ()) -> str:
        before = self.store.count("entities")
        eid, decision = resolve_or_create(
            self.store, entity_type, name, source=source, source_ref=source_ref,
            key=key, external_id=external_id, email=email,
            associations=associations, attributes=attributes,
            observed_at=observed_at)
        if self.store.count("entities") > before:
            self._report.entities_created += 1
        else:
            self._report.entities_resolved += 1
        if decision.merge_candidate_for:
            self._report.candidate_merges += 1
        return eid

    def _edge(self, subject: str, predicate: R, obj: str, *,
              source_reference: str, confidence: float, method: str,
              observed_at: str, asserted: bool = False,
              provenance: dict[str, Any] | None = None) -> Disposition | None:
        """Create one typed edge, or record why it was refused."""
        try:
            _rel_id, disposition, created = self.store.relate(
                subject, predicate, obj, source_reference=source_reference,
                confidence=confidence, extraction_method=method,
                observed_at=observed_at, asserted=asserted,
                provenance=provenance)
        except (VocabularyError, StoreError) as error:
            self._report.refusals.append(
                f"{subject} --{predicate.value}--> {obj}: {error}")
            return None
        if created:
            self._report.relationships_created += 1
        else:
            self._report.relationships_existing += 1
        return disposition

    def _normalized_apn(self, raw: str) -> str:
        """Normalise via the PRODUCTION extractor, or treat as absent."""
        if not raw:
            return ""
        found = self._extract.apns(str(raw))
        return found[0] if found else ""

    def _owning_company(self, source: str, source_ref: str, observed_at: str) -> str:
        return self._ensure(E.COMPANY, OWNING_COMPANY, source=source,
                            source_ref=source_ref, observed_at=observed_at,
                            attributes={"role": "owner_of_record"})

    def _jurisdiction(self, name: str, *, source: str, source_ref: str,
                      observed_at: str) -> str | None:
        canonical = canonical_jurisdiction(name)
        if not canonical:
            return None
        return self._ensure(E.JURISDICTION, canonical, source=source,
                            source_ref=source_ref, observed_at=observed_at)

    def _property(self, *, apn: str, address: str, source: str, source_ref: str,
                  observed_at: str) -> str | None:
        """One property entity, keyed on APN when we have one.

        Keying on APN is what makes a Notion project and a portal row collapse
        onto the SAME property instead of becoming two. When there is no APN the
        production address key is used, which is weaker but consistent.
        """
        normalized = self._normalized_apn(apn)
        address_key = self._extract.address_key(address) if address else ""
        key = normalized or address_key
        if not key:
            return None
        return self._ensure(
            E.PROPERTY, address or normalized or key, key=key,
            source=source, source_ref=source_ref, observed_at=observed_at,
            attributes={"apn": normalized, "address": address,
                        "address_key": address_key})

    # ----------------------------------------------------------------- handlers

    def _handle_project(self, record: SourceRecord) -> None:
        data, ref, seen = record.payload, record.source_ref, record.observed_at
        src = record.source
        project = self._ensure(
            E.PROJECT, data.get("name") or data["project_id"],
            key=data.get("address") or data.get("name") or data["project_id"],
            external_id=data.get("notion_page_id") or data.get("project_id"),
            source=src, source_ref=ref, observed_at=seen,
            attributes={"stage": data.get("stage", ""),
                        "drive_url": data.get("drive_url", ""),
                        "notion_page_id": data.get("notion_page_id", ""),
                        "jayos_project_id": data.get("project_id", "")})

        prop = self._property(apn=data.get("apn", ""), address=data.get("address", ""),
                              source=src, source_ref=ref, observed_at=seen)
        if prop:
            # Notion records the address against the project: asserted.
            self._edge(project, R.LOCATED_AT, prop, source_reference=ref,
                       confidence=1.0, method="asserted:notion_projects_catalogue",
                       observed_at=seen, asserted=True)

        city = jurisdiction_from_address(data.get("address", ""))
        jurisdiction = self._jurisdiction(city, source=src, source_ref=ref,
                                          observed_at=seen) if city else None
        if jurisdiction and prop:
            self._edge(prop, R.LOCATED_IN, jurisdiction, source_reference=ref,
                       confidence=MUNICIPAL_CONFIDENCE,
                       method="inferred:city_token_in_address", observed_at=seen,
                       provenance={"city_token": city})

        for permit_number in data.get("permit_numbers", []):
            permit = self._ensure(E.PERMIT, str(permit_number).strip().upper(),
                                  source=src, source_ref=ref, observed_at=seen)
            self._edge(permit, R.PART_OF, project, source_reference=ref,
                       confidence=1.0, method="asserted:notion_projects_catalogue",
                       observed_at=seen, asserted=True)
            self._edge(project, R.REQUIRES, permit, source_reference=ref,
                       confidence=1.0, method="asserted:notion_projects_catalogue",
                       observed_at=seen, asserted=True)
            if jurisdiction:
                self._edge(permit, R.SUBMITTED_TO, jurisdiction, source_reference=ref,
                           confidence=MUNICIPAL_CONFIDENCE,
                           method="inferred:project_jurisdiction", observed_at=seen)

        if data.get("client_emails"):
            company = self._owning_company(src, ref, seen)
            for email in data["client_emails"]:
                client = self._ensure(
                    E.CLIENT, email, key=email, email=email, source=src,
                    source_ref=ref, observed_at=seen, associations=(project,))
                self._edge(client, R.CLIENT_OF, company, source_reference=ref,
                           confidence=CLIENT_CONFIDENCE,
                           method="inferred:client_email_on_project",
                           observed_at=seen)
                if prop:
                    self._edge(client, R.OWNS, prop, source_reference=ref,
                               confidence=CLIENT_OWNS_CONFIDENCE,
                               method="inferred:client_of_project_at_property",
                               observed_at=seen)

    def _handle_permit_folder(self, record: SourceRecord) -> None:
        data, ref, seen = record.payload, record.source_ref, record.observed_at
        self._ensure(E.PERMIT, data["permit_number"], source=record.source,
                     source_ref=ref, observed_at=seen,
                     attributes={"working_folder": data.get("path", "")})

    def _handle_document(self, record: SourceRecord) -> None:
        data, ref, seen = record.payload, record.source_ref, record.observed_at
        permit = self._ensure(E.PERMIT, data["permit_number"], source=record.source,
                              source_ref=ref, observed_at=seen)
        document = self._ensure(
            E.DOCUMENT, data["filename"], key=ref, source=record.source,
            source_ref=ref, observed_at=seen,
            attributes={"path": data.get("path", ""), "bytes": data.get("bytes", 0)})
        # The file is inside the permit's own folder. Structural, not inferred.
        self._edge(document, R.PART_OF, permit, source_reference=ref,
                   confidence=1.0, method="asserted:permit_working_folder",
                   observed_at=seen, asserted=True)

    def _handle_portal_permit(self, record: SourceRecord) -> None:
        data, ref, seen = record.payload, record.source_ref, record.observed_at
        src = record.source
        permit = self._ensure(
            E.PERMIT, data["permit_number"], source=src, source_ref=ref,
            observed_at=seen,
            attributes={"status": data.get("status", ""),
                        "work_type": data.get("work_type", ""),
                        "permit_name": data.get("permit_name", ""),
                        "total_fees": data.get("total_fees", ""),
                        "total_payments": data.get("total_payments", ""),
                        "expiration_date": data.get("expiration_date", ""),
                        "completion_date": data.get("completion_date", "")})

        address = " ".join(part for part in (data.get("number"), data.get("street"))
                           if part).strip()
        prop = self._property(apn=data.get("apn", ""), address=address,
                              source=src, source_ref=ref, observed_at=seen)
        if prop:
            self._edge(permit, R.LOCATED_AT, prop, source_reference=ref,
                       confidence=1.0, method="asserted:portal_export_row",
                       observed_at=seen, asserted=True)

        city = (data.get("city") or data.get("jurisdiction_hint") or "")
        jurisdiction = self._jurisdiction(city, source=src, source_ref=ref,
                                          observed_at=seen) if city else None
        if jurisdiction:
            # The portal's own City column is the jurisdiction's record.
            self._edge(permit, R.SUBMITTED_TO, jurisdiction, source_reference=ref,
                       confidence=MUNICIPAL_ASSERTED_CONFIDENCE,
                       method="asserted:portal_city_column", observed_at=seen,
                       asserted=True)
            if prop:
                self._edge(prop, R.LOCATED_IN, jurisdiction, source_reference=ref,
                           confidence=MUNICIPAL_ASSERTED_CONFIDENCE,
                           method="asserted:portal_city_column", observed_at=seen,
                           asserted=True)

        if data.get("contractor"):
            contractor = self._ensure(
                E.COMPANY, data["contractor"], source=src, source_ref=ref,
                observed_at=seen,
                attributes={"roc_license": data.get("roc_license", "")})
            self._edge(contractor, R.WORKS_ON, permit, source_reference=ref,
                       confidence=1.0, method="asserted:portal_contractor_column",
                       observed_at=seen, asserted=True)

        if data.get("owner_name") and prop:
            owner = self._ensure(E.PERSON, data["owner_name"], source=src,
                                 source_ref=ref, observed_at=seen)
            self._edge(owner, R.OWNS, prop, source_reference=ref, confidence=1.0,
                       method="asserted:portal_owner_column", observed_at=seen,
                       asserted=True)

        self._link_portal_permit_to_project(permit, prop, data, ref, seen)

    def _link_portal_permit_to_project(self, permit: str, prop: str | None,
                                       data: dict[str, Any], ref: str,
                                       seen: str) -> None:
        """Is this one of OUR permits? An inference, and labelled as one."""
        if not prop:
            return
        for edge in self.store.relationships_to(prop):
            if edge["relationship_type"] != R.LOCATED_AT.value:
                continue
            subject = self.store.entity(edge["source_entity"])
            if subject is None or subject["entity_type"] != E.PROJECT.value:
                continue
            matched_on_apn = bool(self._normalized_apn(data.get("apn", "")))
            self._edge(
                permit, R.PART_OF, subject["entity_id"], source_reference=ref,
                confidence=APN_MATCH_CONFIDENCE if matched_on_apn
                else ADDRESS_MATCH_CONFIDENCE,
                method="inferred:portal_row_shares_property_with_project",
                observed_at=seen,
                provenance={"matched_on": "apn" if matched_on_apn else "address",
                            "property": prop})

    def _handle_review_comment(self, record: SourceRecord) -> None:
        """A jurisdiction comment that blocks a permit, and who wrote it."""
        data, ref, seen = record.payload, record.source_ref, record.observed_at
        src = record.source
        permit = self._ensure(E.PERMIT, data["permit_number"], source=src,
                              source_ref=ref, observed_at=seen)
        comment = self._ensure(
            E.REVIEW_COMMENT, data.get("summary") or data["comment_id"],
            key=data["comment_id"], source=src, source_ref=ref, observed_at=seen,
            attributes={"text": data.get("text", ""),
                        "received_at": data.get("received_at", "")})
        self._edge(comment, R.PART_OF, permit, source_reference=ref,
                   confidence=1.0, method="asserted:review_comment_on_permit",
                   observed_at=seen, asserted=True)
        self._edge(permit, R.BLOCKED_BY, comment, source_reference=ref,
                   confidence=1.0, method="asserted:comment_blocks_permit",
                   observed_at=seen, asserted=True)
        if data.get("reviewer_name"):
            reviewer = self._ensure(
                E.PERSON, data["reviewer_name"], source=src, source_ref=ref,
                observed_at=seen, email=data.get("reviewer_email") or None,
                attributes={"role": data.get("reviewer_role", "")})
            self._edge(permit, R.REVIEWED_BY, reviewer, source_reference=ref,
                       confidence=1.0, method="asserted:named_reviewer",
                       observed_at=seen, asserted=True)
            jurisdiction = self._jurisdiction(
                data.get("jurisdiction", ""), source=src, source_ref=ref,
                observed_at=seen) if data.get("jurisdiction") else None
            if jurisdiction:
                self._edge(reviewer, R.PART_OF, jurisdiction, source_reference=ref,
                           confidence=1.0, method="asserted:reviewer_email_domain",
                           observed_at=seen, asserted=True)

    def _handle_decision(self, record: SourceRecord) -> None:
        """A decision, who made it, and what forced it."""
        data, ref, seen = record.payload, record.source_ref, record.observed_at
        src = record.source
        decision = self._ensure(
            E.DECISION, data.get("summary") or data["decision_id"],
            key=data["decision_id"], source=src, source_ref=ref, observed_at=seen,
            attributes={"rationale": data.get("rationale", "")})
        if data.get("decided_by"):
            actor = self._ensure(E.PERSON, data["decided_by"], source=src,
                                 source_ref=ref, observed_at=seen)
            self._edge(decision, R.DECIDED_BY, actor, source_reference=ref,
                       confidence=1.0, method="asserted:decision_record",
                       observed_at=seen, asserted=True)
        if data.get("prompted_by_comment"):
            comment = self.store.find_by_key(E.REVIEW_COMMENT,
                                             data["prompted_by_comment"])
            if comment:
                self._edge(decision, R.CREATED_FROM, comment["entity_id"],
                           source_reference=ref, confidence=1.0,
                           method="asserted:decision_record", observed_at=seen,
                           asserted=True)

    def _handle_outcome(self, record: SourceRecord) -> None:
        data, ref, seen = record.payload, record.source_ref, record.observed_at
        outcome = self._ensure(
            E.OUTCOME, data.get("summary") or data["outcome_id"],
            key=data["outcome_id"], source=record.source, source_ref=ref,
            observed_at=seen, attributes={"result": data.get("result", "")})
        if data.get("from_decision"):
            decision = self.store.find_by_key(E.DECISION, data["from_decision"])
            if decision:
                self._edge(decision["entity_id"], R.RESULTED_IN, outcome,
                           source_reference=ref, confidence=1.0,
                           method="asserted:outcome_record", observed_at=seen,
                           asserted=True)

    def _handle_task(self, record: SourceRecord) -> None:
        data, ref, seen = record.payload, record.source_ref, record.observed_at
        src = record.source
        task = self._ensure(E.TASK, data.get("summary") or data["task_id"],
                            key=data["task_id"], source=src, source_ref=ref,
                            observed_at=seen,
                            attributes={"status": data.get("status", "")})
        if data.get("assignee"):
            person = self._ensure(E.PERSON, data["assignee"], source=src,
                                  source_ref=ref, observed_at=seen)
            self._edge(task, R.ASSIGNED_TO, person, source_reference=ref,
                       confidence=1.0, method="asserted:task_record",
                       observed_at=seen, asserted=True)
        if data.get("blocked_by_comment"):
            comment = self.store.find_by_key(E.REVIEW_COMMENT,
                                             data["blocked_by_comment"])
            if comment:
                self._edge(task, R.BLOCKED_BY, comment["entity_id"],
                           source_reference=ref, confidence=1.0,
                           method="asserted:task_record", observed_at=seen,
                           asserted=True)
        if data.get("project_external_id"):
            for project in self.store.entities(E.PROJECT):
                attrs = __import__("json").loads(project["attributes"] or "{}")
                if attrs.get("jayos_project_id") == data["project_external_id"]:
                    self._edge(task, R.PART_OF, project["entity_id"],
                               source_reference=ref, confidence=1.0,
                               method="asserted:task_record", observed_at=seen,
                               asserted=True)
                    break

    def _handle_meeting(self, record: SourceRecord) -> None:
        data, ref, seen = record.payload, record.source_ref, record.observed_at
        meeting = self._ensure(
            E.MEETING, data.get("title") or data["meeting_id"],
            key=data["meeting_id"], source=record.source, source_ref=ref,
            observed_at=seen, attributes={"attendees": data.get("attendees", "")})
        if data.get("produced_decision"):
            decision = self.store.find_by_key(E.DECISION, data["produced_decision"])
            if decision:
                self._edge(meeting, R.RESULTED_IN, decision["entity_id"],
                           source_reference=ref, confidence=1.0,
                           method="asserted:meeting_notes", observed_at=seen,
                           asserted=True)

    def _project_by_external_id(self, external_id: str) -> str | None:
        """Find a project already in the graph by its Notion page id.

        Matched-email rows carry a Notion project_id from the live ledger; if
        that project has not been ingested from `projects_json` yet, there is
        nothing to attach to, so the caller refuses rather than guessing.
        """
        for row in self.store.entities(E.PROJECT):
            attrs = __import__("json").loads(row["attributes"] or "{}")
            if attrs.get("jayos_project_id") == external_id:
                return row["entity_id"]
        return None

    def _handle_known_contact(self, record: SourceRecord) -> None:
        """A name Jay hand-classified in `sentinel.toml`.

        Being on `known_clients` IS the assertion -- the sentinel reads that
        exact list to decide real mail routing in production, so CLIENT_OF is
        recorded at confidence 1.0 regardless of whether any email has been
        captured yet. The property/project link is a SEPARATE, weaker claim:
        it comes from parsing the free-text comment Jay left next to the
        address ("Jennifer Ebner, 7714 E Onyx Ct"), using the same production
        address/APN extractor the rest of the pipeline uses. That parse is
        inferred, gated below the auto-link threshold for an address match and
        at-or-above it only for an APN match, same tiers as `portal_csv`.
        """
        data, ref, seen = record.payload, record.source_ref, record.observed_at
        src = record.source
        name = data["comment"].split(",")[0].strip() if data["comment"] else data["email"]
        etype = E.CLIENT if data["role"] == "client" else E.PERSON
        entity = self._ensure(etype, name, key=data["email"], email=data["email"],
                              source=src, source_ref=ref, observed_at=seen,
                              attributes={"role": data["role"], "note": data["comment"]})

        if etype is E.CLIENT:
            company = self._owning_company(src, ref, seen)
            self._edge(entity, R.CLIENT_OF, company, source_reference=ref,
                       confidence=1.0, method="asserted:sentinel_known_clients_list",
                       observed_at=seen, asserted=True)

        if not data["comment"]:
            return
        apn = self._normalized_apn(data["comment"])
        address_key = self._extract.address_key(data["comment"])
        prop = self.store.find_by_key(E.PROPERTY, apn) if apn else None
        confidence = APN_MATCH_CONFIDENCE if prop else None
        if prop is None and address_key:
            prop = self.store.find_by_key(E.PROPERTY, address_key)
            confidence = ADDRESS_MATCH_CONFIDENCE if prop else None
        if prop is None:
            return

        method = "inferred:known_contact_comment_" + ("apn" if apn and confidence == APN_MATCH_CONFIDENCE else "address")
        if etype is E.CLIENT:
            # A client OWNS the property. WORKS_ON is for people/firms that do
            # work on the project -- a homeowner is not a legal endpoint for
            # it, and the contract refuses that edge, so it is not attempted.
            self._edge(entity, R.OWNS, prop["entity_id"], source_reference=ref,
                       confidence=confidence, method=method, observed_at=seen)
        else:
            for edge in self.store.relationships_to(prop["entity_id"]):
                if (edge["relationship_type"] == R.LOCATED_AT.value
                        and self.store.entity(edge["source_entity"])["entity_type"] == E.PROJECT.value):
                    self._edge(entity, R.WORKS_ON, edge["source_entity"], source_reference=ref,
                               confidence=confidence, method=method, observed_at=seen)

    def _handle_matched_email(self, record: SourceRecord) -> None:
        """A sender the PRODUCTION matcher already linked to a project.

        `match_confidence` comes straight from the sentinel's own matcher,
        which already cleared AUTO_LINK_CONFIDENCE for these rows (disposition
        'linked' is filtered in by the adapter) -- so this is asserted
        evidence of correspondence, not a graph-engine inference. What role
        the sender plays (client vs. consultant) still comes from the known
        list when available; a sender in neither list is recorded as a PERSON
        who emailed about the project, nothing stronger is claimed.
        """
        data, ref, seen = record.payload, record.source_ref, record.observed_at
        src = record.source
        project = self._project_by_external_id(data["project_external_id"])
        if project is None:
            self._report.refusals.append(
                f"{ref}: project {data['project_external_id']} not in the graph "
                f"yet (ingest projects_json first)")
            return
        email = data["sender"]
        known = self.store.find_by_key(E.CLIENT, email) or self.store.find_by_key(E.PERSON, email)
        confidence = float(data["match_confidence"])

        if known is not None and known["entity_type"] == E.CLIENT.value:
            client = known["entity_id"]
            self.store.add_source_ref(client, src, ref, seen)
            company = self._owning_company(src, ref, seen)
            self._edge(client, R.CLIENT_OF, company, source_reference=ref,
                       confidence=confidence, method="asserted:sentinel_matcher_linked",
                       observed_at=seen, asserted=True)
            for edge in self.store.relationships_from(project):
                if edge["relationship_type"] == R.LOCATED_AT.value:
                    self._edge(client, R.OWNS, edge["target_entity"],
                               source_reference=ref, confidence=confidence,
                               method="asserted:sentinel_matcher_linked",
                               observed_at=seen, asserted=True)
        elif known is not None:  # known consultant
            person = known["entity_id"]
            self.store.add_source_ref(person, src, ref, seen)
            self._edge(person, R.WORKS_ON, project, source_reference=ref,
                       confidence=confidence, method="asserted:sentinel_matcher_linked",
                       observed_at=seen, asserted=True)
        else:
            person = self._ensure(E.PERSON, email, key=email, email=email,
                                  source=src, source_ref=ref, observed_at=seen,
                                  attributes={"role": "unclassified_contact"})
            self._edge(person, R.WORKS_ON, project, source_reference=ref,
                       confidence=min(confidence, self._propose),
                       method="inferred:matched_email_unclassified_sender",
                       observed_at=seen)

    # -------------------------------------------------------------------- drive

    _HANDLERS = {
        "project": "_handle_project",
        "permit_folder": "_handle_permit_folder",
        "document": "_handle_document",
        "portal_permit": "_handle_portal_permit",
        "review_comment": "_handle_review_comment",
        "decision": "_handle_decision",
        "outcome": "_handle_outcome",
        "task": "_handle_task",
        "meeting": "_handle_meeting",
        "known_contact": "_handle_known_contact",
        "matched_email": "_handle_matched_email",
    }

    def ingest(self, source: Source) -> IngestReport:
        """Run one source into the graph. Never aborts on a single bad record."""
        self._report = IngestReport(source=getattr(source, "name", str(source)))
        for record in source.records():
            self._report.records += 1
            handler_name = self._HANDLERS.get(record.kind)
            if handler_name is None:
                self._report.refusals.append(
                    f"{record.source_ref}: no handler for record kind "
                    f"{record.kind!r}")
                continue
            try:
                getattr(self, handler_name)(record)
            except Exception as error:  # noqa: BLE001 - boundary is the point
                self._report.refusals.append(f"{record.source_ref}: {error}")
        self._report.event_id = self.store.record_event(
            event_type="graph_ingest",
            source=self._report.source,
            actor=self.actor,
            action=f"ingest {self._report.source}",
            result=("ok" if not self._report.refusals
                    else f"ok with {len(self._report.refusals)} refusals"),
            provenance=self._report.as_dict())
        return self._report
