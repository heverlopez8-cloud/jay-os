"""The production contract, end to end.

    EVENT -> IDENTIFY PROJECT -> CLASSIFY -> UPDATE NOTION -> NEXT ACTION
    -> ASSIGN ONE OWNER -> NOTIFY -> ESCALATE -> CLIENT COMMS -> LOG

Every stop is explicit. There is no path through this module that ends in
silence: an event either completes the chain and is released, or it
becomes an exception that is written down and pushed at Jay.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re

from . import classify as classify_mod
from . import matching, routing
from .audit import AuditStore
from .model import (
    NO_ACTION_CLASSES,
    EventState,
    Assignment,
    Channel,
    Classification,
    EventClass,
    Exception_,
    ExceptionCode,
    Match,
    Notification,
    Outcome,
    Person,
    ProjectRecord,
    RawEvent,
    iso,
    now,
)
from .ports import (
    ConnectorError,
    build_project_properties,
)

#: Where a corrections/approval event should leave the project stage.
_STAGE_FOR_CLASS: dict[EventClass, str] = {
    EventClass.CORRECTIONS_REQUIRED: "3 · Review & Redlines",
    EventClass.APPROVED: "5 · Approved",
}

#: How far apart two BYTE-IDENTICAL notices can be and still be one
#: transmission rather than two events.
#:
#: This number is NOT the discriminator -- the digest is. Calibrated on the
#: artifact: the only measured retransmit gap is 16m08s (Phoenix
#: CTR-102608342, 2026-08-24 21:17:15Z and 21:33:23Z); the two 1848 E Yale
#: forwards are 0s apart. Meanwhile the fastest genuinely-distinct
#: same-project, same-class pair in the corpus is 70.4 min (Gilbert
#: RACC-2026-00041, "Plan Review Approved" then "Your Permit is Now
#: Available") -- and its text differs, so it survives this gate even if the
#: window were 31 minutes. A window alone would be a 1.17x margin and unsafe;
#: the conjunction with the digest is what makes it safe.
SAME_STATEMENT_WINDOW_MINUTES = 60

#: A body this short identifies nothing, so the gate refuses to suppress at
#: all. The shortest operational body in the 2026-09-05 corpus is 179
#: characters, so this never fires on real traffic -- it exists because
#: `sources.from_gmail_message` falls back to `snippet` and then to "".
MIN_STATEMENT_CHARS = 40

_WHITESPACE = re.compile(r"\s+")


def _normalize_statement(text: str) -> str:
    return _WHITESPACE.sub(" ", (text or "").replace(" ", " ")).strip()


def statement_digest(raw: RawEvent) -> str:
    """Identity of what was actually SAID -- the whole message, never a quote.

    Reading only the innermost quoted original would give a second-round
    notice that arrives as a reply the FIRST round's identity, and silence
    it. That is precisely the Onyx/Riata failure this gate exists to avoid,
    so the entire utterance is hashed and a covering note is part of the
    statement.

    Deliberately excluded: the Gmail message id (`AuditStore.seen` already
    covers replaying the same message, and is blind to this defect by
    construction), the thread id -- the two 1848 E Yale forwards are one
    statement on two threads, delivered to jay@ and info@ -- and the sender,
    because a notice keeps its identity however many hops it took to arrive.

    Included, by contrast: the attachment filenames. Phoenix issues CTR,
    SCSR and CGD notices for one project in a single batch, and a
    jurisdiction form letter carries the permit number in an attached PDF
    rather than in its templated body -- so two notices about DIFFERENT
    permits were byte-identical here and the second was withheld. That
    withholding is permanent, not merely late: `_suppress_duplicate` leaves
    `due_at` NULL, and both `due_unacknowledged` and `due_incomplete`
    exclude `state='suppressed_duplicate'` outright, so nothing raises it
    again.

    Folding in `matching.extract_permit_numbers(raw.text)` looks like the
    obvious fix here and is not one: `raw.text` IS subject plus body, so the
    permits it finds are a pure function of what this already hashes and add
    no discrimination whatever. Where the number does appear in the text,
    the text alone already separates the two notices. The attachment set is
    the term that carries genuinely new information, and it only ever
    NARROWS identity, so it cannot let a real retransmit slip through: the
    measured 16m08s Phoenix retransmit and the two 1848 E Yale forwards each
    carry one attachment set.

    A residue remains that nothing textual can close -- two form letters
    identical in subject, body AND attachments, naming their permits
    nowhere. Those are held apart only by the 60-minute window.
    """
    attachments = ",".join(sorted(raw.attachments))
    return hashlib.sha256(
        _normalize_statement(
            f"{attachments}\n{raw.subject}\n{raw.body}"
        ).encode("utf-8")
    ).hexdigest()


class PermitPipeline:
    def __init__(
        self,
        store: AuditStore,
        directory,
        notion,
        notifier,
        escalation_contact: Person = routing.JAY,
    ) -> None:
        self.store = store
        self.directory = directory
        self.notion = notion
        self.notifier = notifier
        self.escalation_contact = escalation_contact

    # -- failure path ------------------------------------------------------
    def _fail_closed(
        self, outcome: Outcome, code: ExceptionCode, detail: str
    ) -> Outcome:
        """Record the failure, tell Jay, and never pretend it worked."""
        exception = Exception_(outcome.event_id, code, detail)
        outcome.exception = exception
        self.store.record_exception(exception)
        self.store.log(outcome.event_id, "exception", code.value, detail=detail)

        alert = Notification(
            channel="exception",
            recipient=self.escalation_contact.email,
            subject=f"[JAY-OS EXCEPTION] {code.value} - {outcome.raw.subject[:80]}",
            body=(
                f"JAY-OS could not complete the chain for an operational event "
                f"and has stopped rather than guess.\n\n"
                f"Event:    {outcome.event_id}\n"
                f"Source:   {outcome.raw.source.value}\n"
                f"From:     {outcome.raw.sender}\n"
                f"Subject:  {outcome.raw.subject}\n"
                f"Received: {iso(outcome.raw.received_at)}\n\n"
                f"Failure:  {code.value}\n"
                f"Detail:   {detail}\n\n"
                f"This event has NO owner and NO due date until a human resolves it."
            ),
        )
        try:
            delivered = self.notifier.send(alert)
            outcome.notifications.append(delivered)
            self.store.record_notification(outcome.event_id, delivered)
            self.store.log(outcome.event_id, "notify_exception", "ok",
                           recipient=delivered.recipient)
        except ConnectorError as error:
            # The alert itself failed. Persist it so the sweep retries; the
            # durable record is the last line of defence against silence.
            failed = Notification(
                channel="exception", recipient=self.escalation_contact.email,
                subject=alert.subject, body=alert.body, ok=False,
                detail=str(error),
            )
            outcome.notifications.append(failed)
            self.store.record_notification(outcome.event_id, failed)
            self.store.log(outcome.event_id, "notify_exception", "failed",
                           error=str(error))

        self.store.upsert_event(
            event_id=outcome.event_id,
            source=outcome.raw.source.value,
            external_id=outcome.raw.external_id,
            received_at=iso(outcome.raw.received_at),
            subject=outcome.raw.subject,
            sender=outcome.raw.sender,
            state=EventState.EXCEPTION.value,
        )
        return outcome

    def _suppress_duplicate(
        self, outcome: Outcome, raw: RawEvent, project: ProjectRecord,
        classification: Classification, assignment: Assignment,
        digest: str, anchor,
    ) -> Outcome:
        """Withhold the email. Record everything else, loudly.

        No Notion write, no audit comment, no client draft: the first copy
        already made all three, and a second corrections write would bump the
        redline round and falsify the review cycle. What the owner did NOT
        receive is stored verbatim, so it can be read, counted and sent by
        hand -- "we chose not to send" is a row, never a gap.
        """
        gap = abs(
            (raw.received_at
             - dt.datetime.fromisoformat(anchor["occurred_at"])).total_seconds()
        ) / 60.0
        withheld = Notification(
            channel="owner_suppressed",
            recipient=assignment.owner.email,
            subject=(
                f"[{classification.event_class.value.replace('_', ' ').upper()}] "
                f"{project.name}"
            ),
            body=self._owner_message(project, classification, assignment, raw),
            sent_at=None,
            ok=False,
            detail=(
                f"suppressed: identical statement already delivered to "
                f"{assignment.owner.email} as {anchor['event_id']}, "
                f"{gap:.1f} min away; digest {digest[:16]}"
            ),
        )
        outcome.notifications.append(withheld)
        # `decision='suppressed'`, not just ok=False: this is a choice, not a
        # transport failure, and `_fail_closed`'s "persist it so the sweep
        # retries" logic reads the same table.
        self.store.record_notification(raw.event_id, withheld,
                                       decision="suppressed")
        self.store.record_notification_decision(
            event_id=raw.event_id, channel="owner",
            project_id=project.page_id,
            event_class=classification.event_class.value,
            recipient=assignment.owner.email, statement_digest=digest,
            occurred_at=raw.received_at, decision="suppressed",
            suppressed_by=anchor["event_id"],
        )
        self.store.log(
            raw.event_id, "notify_owner", "suppressed_duplicate",
            of_event=anchor["event_id"], gap_minutes=round(gap, 2),
            statement_digest=digest, recipient=assignment.owner.email,
            project_id=project.page_id,
            event_class=classification.event_class.value,
            window_minutes=SAME_STATEMENT_WINDOW_MINUTES,
        )
        self.store.log(raw.event_id, "notion_update", "skipped_duplicate",
                       of_event=anchor["event_id"], page_id=project.page_id)
        # due_at and complete_by stay NULL: a suppressed copy must never enter
        # an acknowledgement or completion clock, or the duplicate simply
        # moves from Eric's inbox to Jay's escalation queue 24 hours later.
        self.store.upsert_event(
            event_id=raw.event_id, source=raw.source.value,
            external_id=raw.external_id, received_at=iso(raw.received_at),
            subject=raw.subject, sender=raw.sender,
            project_id=project.page_id, project_name=project.name,
            event_class=classification.event_class.value,
            owner_key=assignment.owner.key,
            due_at=None, complete_by=None,
            state=EventState.SUPPRESSED_DUPLICATE.value,
        )
        self.store.log(raw.event_id, "release", "suppressed_duplicate")
        outcome.released = True
        return outcome

    # -- happy path --------------------------------------------------------
    def process(self, raw: RawEvent) -> Outcome:
        outcome = Outcome(event_id=raw.event_id, raw=raw)

        if self.store.seen(raw.event_id):
            self.store.log(raw.event_id, "ingest", "duplicate_ignored")
            outcome.released = True
            return outcome

        self.store.log(
            raw.event_id, "ingest", "ok",
            source=raw.source.value, sender=raw.sender, subject=raw.subject,
        )

        # 1. IDENTIFY PROJECT
        try:
            projects = self.directory.projects()
        except ConnectorError as error:
            return self._fail_closed(
                outcome, ExceptionCode.CONNECTOR_FAILED,
                f"could not read the project directory: {error}",
            )

        match = matching.match_project(raw.text, projects)
        outcome.match = match
        self.store.log(
            raw.event_id, "identify_project",
            "ok" if matching.is_confident(match) else "failed",
            basis=match.basis,
            project=match.project.name if match.project else None,
            confidence=match.confidence,
            candidates=list(match.candidates),
        )
        if not matching.is_confident(match):
            if match.candidates:
                return self._fail_closed(
                    outcome, ExceptionCode.PROJECT_AMBIGUOUS,
                    f"{len(match.candidates)} projects match this event "
                    f"({match.basis}): {', '.join(match.candidates)}. "
                    f"A permit number is needed to tell them apart.",
                )
            return self._fail_closed(
                outcome, ExceptionCode.PROJECT_NOT_MATCHED,
                "no project could be identified from the permit number, "
                "parcel number or address in this event.",
            )
        project = match.project
        assert project is not None

        # 2. CLASSIFY EVENT
        classification = classify_mod.classify(raw.text)
        outcome.classification = classification
        self.store.log(
            raw.event_id, "classify",
            "ok" if classify_mod.is_confident(classification) else "failed",
            event_class=classification.event_class.value,
            confidence=classification.confidence,
            evidence=list(classification.evidence),
        )

        if classification.event_class in NO_ACTION_CLASSES:
            # Receipts and out-of-office replies are real events with no work
            # in them. Logged and closed -- never escalated, never silent.
            self.store.upsert_event(
                event_id=raw.event_id, source=raw.source.value,
                external_id=raw.external_id, received_at=iso(raw.received_at),
                subject=raw.subject, sender=raw.sender,
                project_id=project.page_id, project_name=project.name,
                event_class=classification.event_class.value,
                state=EventState.NO_ACTION.value,
            )
            self.store.log(raw.event_id, "release", "no_action")
            outcome.released = True
            return outcome

        if not classify_mod.is_confident(classification):
            return self._fail_closed(
                outcome, ExceptionCode.CLASSIFICATION_UNCERTAIN,
                f"could not confidently classify this event for "
                f"{project.name} (best guess "
                f"{classification.event_class.value} at "
                f"{classification.confidence:.2f}).",
            )

        # 3. NEXT ACTION + 4. ONE ACCOUNTABLE OWNER + 5. SLA
        permits = matching.extract_permit_numbers(raw.text)
        assignment = routing.assign(
            classification.event_class,
            project,
            raw.received_at,
            jurisdiction=project.jurisdiction or _jurisdiction_from(raw),
            permit=permits[0] if permits else "",
        )
        outcome.assignment = assignment
        if assignment is None:
            self.store.log(raw.event_id, "assign_owner", "failed")
            return self._fail_closed(
                outcome, ExceptionCode.OWNER_UNDETERMINED,
                f"no routing rule and no stage owner for "
                f"{classification.event_class.value} on {project.name} "
                f"(stage={project.stage!r}).",
            )
        self.store.log(
            raw.event_id, "assign_owner", "ok",
            owner=assignment.owner.key, reason=assignment.reason,
            sla_hours=assignment.sla_hours, due_at=iso(assignment.due_at),
        )
        self.store.log(raw.event_id, "next_action", "ok",
                       action=assignment.next_action)

        # 5b. HAVE WE ALREADY DELIVERED THIS EXACT STATEMENT?
        #
        # Two emails about one permit event carry different Gmail message
        # ids, so the `store.seen` check above cannot see them. Identity here
        # is what the jurisdiction actually SAID; the window only bounds how
        # long an identical utterance stays suppressible.
        #
        # This sits before the Notion write and not merely before the send.
        # `_properties_for` advances the redline round from a status read
        # live from Notion, so a duplicate corrections notice would invent a
        # review round in the permanent record.
        digest = statement_digest(raw)
        anchor = None
        # Provenance first, then length. A preview is long enough to pass any
        # floor and still identifies nothing: form letters share their opening
        # paragraph, so two different notices reduced to `snippet` are equal
        # here. Refusing to suppress costs a duplicate email; suppressing
        # wrongly costs the event, permanently.
        if (not raw.body_is_preview
                and len(_normalize_statement(raw.body)) >= MIN_STATEMENT_CHARS):
            anchor = self.store.identical_statement_sent(
                channel="owner",
                project_id=project.page_id,
                event_class=classification.event_class.value,
                recipient=assignment.owner.email,
                statement_digest=digest,
                occurred_at=raw.received_at,
                window_minutes=SAME_STATEMENT_WINDOW_MINUTES,
                exclude_event_id=raw.event_id,
            )
        if anchor is not None:
            return self._suppress_duplicate(
                outcome, raw, project, classification, assignment,
                digest, anchor,
            )

        # 6. UPDATE THE PROJECT RECORD
        self._event_time = raw.received_at
        properties = self._properties_for(project, classification, assignment)
        outcome.notion_updates = properties
        try:
            self.notion.update_project(project.page_id, properties)
        except ConnectorError as error:
            return self._fail_closed(
                outcome, ExceptionCode.NOTION_WRITE_FAILED,
                f"Notion update failed for {project.name}: {error}",
            )
        self.store.log(raw.event_id, "notion_update", "ok",
                       page_id=project.page_id,
                       fields=sorted(properties.keys()))

        # 7. NOTIFY THAT OWNER
        notification = Notification(
            channel="owner",
            recipient=assignment.owner.email,
            subject=(
                f"[{classification.event_class.value.replace('_', ' ').upper()}] "
                f"{project.name}"
            ),
            body=self._owner_message(project, classification, assignment, raw),
        )
        try:
            delivered = self.notifier.send(notification)
        except ConnectorError as error:
            return self._fail_closed(
                outcome, ExceptionCode.NOTIFICATION_FAILED,
                f"could not notify {assignment.owner.name} about "
                f"{project.name}: {error}",
            )
        outcome.notifications.append(delivered)
        self.store.record_notification(raw.event_id, delivered)
        self.store.log(raw.event_id, "notify_owner", "ok",
                       recipient=delivered.recipient)

        # The anchor is written only NOW, after a real delivery. A copy that
        # dies at the Notion write or in SMTP leaves no anchor at all, so the
        # next copy of that statement still gets a real attempt. Suppression
        # can never stand in for a notification that never went out, and an
        # interrupt leaves no claim to orphan.
        self.store.record_notification_decision(
            event_id=raw.event_id, channel="owner",
            project_id=project.page_id,
            event_class=classification.event_class.value,
            recipient=delivered.recipient, statement_digest=digest,
            occurred_at=raw.received_at, decision="sent",
        )

        # A durable, in-context trace on the project record itself.
        try:
            self.notion.comment(
                project.page_id,
                self._audit_comment(classification, assignment),
                mention_user_id=assignment.owner.notion_user_id,
            )
        except ConnectorError as error:
            # The record and the owner are already correct; the comment is
            # supplementary, so note the degradation instead of failing.
            self.store.log(raw.event_id, "notion_comment", "degraded",
                           error=str(error))

        # 8. PREPARE CLIENT COMMUNICATION WHEN REQUIRED
        if classification.event_class in routing.CLIENT_COMMUNICATION_CLASSES:
            outcome.client_message = self._client_draft(
                project, classification, assignment
            )
            self.store.log(raw.event_id, "client_communication", "prepared",
                           draft=outcome.client_message)

        # 9. RELEASE + LOG
        self.store.upsert_event(
            event_id=raw.event_id, source=raw.source.value,
            external_id=raw.external_id, received_at=iso(raw.received_at),
            subject=raw.subject, sender=raw.sender,
            project_id=project.page_id, project_name=project.name,
            event_class=classification.event_class.value,
            owner_key=assignment.owner.key, due_at=iso(assignment.due_at),
            complete_by=iso(assignment.complete_by) if assignment.complete_by
            else None,
            state=EventState.NOTIFIED.value,
        )
        self.store.log(raw.event_id, "release", "ok")
        outcome.released = True
        return outcome

    # -- payload / message construction ------------------------------------
    def _properties_for(
        self,
        project: ProjectRecord,
        classification: Classification,
        assignment: Assignment,
    ) -> dict:
        """Build the write. Dates track when the event happened, not when we
        got round to processing it -- a backfilled event must not look fresh."""
        status: list[str] | None = None
        if classification.event_class is EventClass.CORRECTIONS_REQUIRED:
            status = [routing.next_redline_status(project.status)]
        elif classification.event_class is EventClass.APPROVED:
            status = ["APPROVED"]

        return build_project_properties(
            stage=_STAGE_FOR_CLASS.get(classification.event_class),
            status=status,
            owner_notion_id=assignment.owner.notion_user_id,
            next_action=assignment.next_action,
            waiting_on=assignment.waiting_on,
            follow_up=assignment.due_at,
            last_activity=self._event_time,
            automation_status=(
                f"JAY-OS permit pipeline: {classification.event_class.value} "
                f"-> {assignment.owner.name}, due "
                f"{assignment.due_at:%Y-%m-%d %H:%M} UTC "
                f"({assignment.sla_hours}h SLA)"
            ),
        )

    def _owner_message(
        self,
        project: ProjectRecord,
        classification: Classification,
        assignment: Assignment,
        raw: RawEvent,
    ) -> str:
        # Owner and escalation contact are the same person for
        # CORRECTIONS_REQUIRED -- the class this pipeline was built around --
        # because `escalation_target` returns JAY unconditionally. Telling Jay
        # that his own work "escalates to Hever 'Jay' Lopez automatically" is
        # false on its face, and it hides the thing he most needs to know:
        # there is nobody behind him on this one.
        if assignment.owner.key == self.escalation_contact.key:
            escalation_line = (
                "Nobody is behind you on this one -- you are both the owner "
                "and the escalation contact. It re-raises itself at rising "
                "escalation levels until you acknowledge it."
            )
        else:
            escalation_line = (
                f"If you do not, this escalates to "
                f"{self.escalation_contact.name} automatically."
            )
        return (
            f"You own this. Nobody else does.\n\n"
            f"Project:     {project.name}\n"
            f"Event:       {classification.event_class.value} "
            f"(confidence {classification.confidence:.2f})\n"
            f"Source:      {raw.source.value} from {raw.sender}\n"
            f"Received:    {iso(raw.received_at)}\n"
            f"Subject:     {raw.subject}\n\n"
            f"NEXT ACTION\n{assignment.next_action}\n\n"
            f"Acknowledge by {assignment.due_at:%Y-%m-%d %H:%M} UTC "
            f"({assignment.sla_hours}h). {escalation_line}\n\n"
            f"Event id: {raw.event_id}"
        )

    def _audit_comment(
        self, classification: Classification, assignment: Assignment
    ) -> str:
        return (
            f"JAY-OS: {classification.event_class.value} detected "
            f"({classification.confidence:.2f}). Owner set to "
            f"{assignment.owner.name}; acknowledgement due "
            f"{assignment.due_at:%Y-%m-%d %H:%M} UTC. "
            + ("Re-raises at rising escalation levels if unacknowledged."
               if assignment.owner.key == self.escalation_contact.key
               else f"Escalates to {self.escalation_contact.name} "
                    f"if unacknowledged.")
        )

    def _client_draft(
        self,
        project: ProjectRecord,
        classification: Classification,
        assignment: Assignment,
    ) -> str:
        headline = {
            EventClass.CORRECTIONS_REQUIRED: (
                "the jurisdiction returned our plans for corrections"
            ),
            EventClass.APPROVED: "the permit has been approved",
            EventClass.FEE_DUE: "fees are now due before the permit can issue",
            EventClass.CLIENT_ESCALATION: "here is exactly where your project stands",
        }[classification.event_class]
        return (
            f"Subject: {project.name} - status update\n\n"
            f"Hi -\n\n"
            f"Quick update on {project.name}: {headline}. We picked this up "
            f"automatically the day it happened, and "
            f"{assignment.owner.name} owns it.\n\n"
            f"What happens next: {assignment.next_action}\n\n"
            f"You will hear from us by "
            f"{assignment.due_at:%B %d}. If anything changes before then we "
            f"will tell you rather than wait for you to ask.\n\n"
            f"- Professional CAD Design\n\n"
            f"[DRAFT - prepared by JAY-OS, not sent. Review and send.]"
        )


def _jurisdiction_from(raw: RawEvent) -> str:
    domain = raw.sender.split("@")[-1].lower()
    known = {
        "gilbertaz.gov": "Town of Gilbert",
        "energov.noreply@gilbertaz.gov": "Town of Gilbert",
        "phoenix.gov": "City of Phoenix",
        "scottsdaleaz.gov": "City of Scottsdale",
        "maricopa.gov": "Maricopa County",
        "mail.maricopa.gov": "Maricopa County",
        "mesaaz.gov": "City of Mesa",
    }
    for suffix, name in known.items():
        if domain.endswith(suffix):
            return name
    return ""


def run_sla_sweep(
    store: AuditStore, notifier, at: dt.datetime | None = None,
    escalation_contact: Person = routing.JAY,
) -> list[str]:
    """Escalate on both clocks.

    Unacknowledged work escalates because nobody has picked it up.
    Acknowledged-but-unfinished work escalates because saying "I have it"
    is not the same as doing it -- that distinction is exactly what went
    missing on 637 N Riata.
    """
    moment = at or now()
    escalated: list[str] = []

    overdue = [(row, "unacknowledged") for row in store.due_unacknowledged(moment)]
    overdue += [(row, "incomplete") for row in store.due_incomplete(moment)]

    for row, kind in overdue:
        level = int(row["escalation_level"]) + 1
        target = routing.escalation_target(level)
        deadline = row["due_at"] if kind == "unacknowledged" else row["complete_by"]
        overdue_hours = _hours_between(deadline, moment)
        # `escalation_target` returns JAY unconditionally, so for a class
        # Jay already owns the notice climbs to its own owner. Addressing him
        # in the third person -- "jay has not acknowledged this event" -- read
        # as a report about somebody else and buried the fact that no one else
        # was being told.
        self_escalation = target.key == row["owner_key"]
        if kind == "unacknowledged":
            headline = ("You have not acknowledged this event."
                        if self_escalation
                        else f"{row['owner_key']} has not acknowledged this event.")
            state_line = "State: NOTIFIED (never acknowledged)"
        else:
            headline = (f"{'You' if self_escalation else row['owner_key']} "
                        f"acknowledged this on "
                        f"{str(row['acknowledged_at'])[:19]} and it is still "
                        f"not finished.")
            state_line = "State: ACKNOWLEDGED but not COMPLETED"
        notification = Notification(
            channel="escalation",
            recipient=target.email,
            subject=(
                f"[ESCALATION L{level}] {row['project_name']} - "
                f"{row['event_class']} {kind}"
            ),
            body=(
                f"{headline}\n\n"
                f"Project:  {row['project_name']}\n"
                f"Event:    {row['event_class']}\n"
                f"Subject:  {row['subject']}\n"
                f"{state_line}\n"
                f"Was due:  {deadline} ({overdue_hours:.1f}h overdue)\n"
                f"Event id: {row['event_id']}\n\n"
                f"Escalation level {level}. This repeats until the work is "
                f"acknowledged and completed."
                + (" You are both the owner and the escalation contact, so "
                   "nobody else is being notified about this."
                   if self_escalation else "")
            ),
        )
        try:
            delivered = notifier.send(notification)
            store.record_notification(row["event_id"], delivered)
            store.log(row["event_id"], "escalate", "ok",
                      level=level, kind=kind, recipient=target.email,
                      overdue_hours=overdue_hours)
        except ConnectorError as error:
            failed = Notification(
                channel="escalation", recipient=target.email,
                subject=notification.subject, body=notification.body,
                ok=False, detail=str(error),
            )
            store.record_notification(row["event_id"], failed)
            store.log(row["event_id"], "escalate", "failed", error=str(error))
            store.record_exception(
                Exception_(row["event_id"], ExceptionCode.NOTIFICATION_FAILED,
                           f"escalation could not be delivered: {error}")
            )
        store.record_escalation(row["event_id"], level)
        escalated.append(row["event_id"])

    return escalated


def _hours_between(due_iso: str, moment: dt.datetime) -> float:
    due = dt.datetime.fromisoformat(due_iso)
    return max(0.0, (moment - due).total_seconds() / 3600.0)
