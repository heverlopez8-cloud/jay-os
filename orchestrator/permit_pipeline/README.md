# Permit / review event pipeline

Jay is not the message bus.

An operational event enters, and this package takes it all the way to an
owned, dated, notified, audited piece of work — or to an exception that a
human is told about. There is no third outcome.

```
EVENT -> IDENTIFY PROJECT -> CLASSIFY -> UPDATE NOTION -> NEXT ACTION
      -> ASSIGN ONE OWNER -> NOTIFY -> ESCALATE -> CLIENT COMMS -> LOG
```

## What actually failed

Traced 2026-09-03 against live Gmail and the live Notion `🏡 Projects`
database. All three failures are the same missing component, seen from
three different angles.

### 637 N Riata St, Gilbert — permit RACC-2026-00041

| when | what happened |
|---|---|
| 2026-04-15 | Gilbert returned the plans for corrections. |
| 2026-04-15 → 06-12 | Nothing. No owner, no task, no notification. |
| 2026-06-12 | Jay emailed Jason Stanley at Gilbert to ask the status. |
| 2026-06-15 | Gilbert replied: *"This project was returned for corrections on 4/15."* |
| 2026-06-26 | Resubmitted — **72 days after the kickback.** |
| 2026-07-06 | Kicked back again. |
| 2026-08-11 | The **client** called the town herself and emailed Jay: *"this is costing us $300 per day."* |

Gilbert does send machine-readable notices — `energov.noreply@gilbertaz.gov`,
subject "Returned for corrections and requires your attention" (the same
notice arrived for COMM-2026-00141 on 2026-05-19). It landed in an inbox
nothing was reading.

**Chain failure point: step 2.** The event was delivered and never detected.

### 21833 W Roosevelt Ave, Wittmann — Maricopa County, BLDR2502290 / BLDR2503407

Both lots sit at `3 · Review & Redlines / REDLINES 01` with an owner set
and this `Required Next Action`:

> "Work the redline corrections (see Rework Reason). When resubmit-ready,
> coordinator sets Stage 4 · Submitted to City."

That is a stage-entry template, not an event. It carries no redline
content, no due date and no notification; `Automation Status` reads
`"OK: entered 🔴 Redlines (Guillermo) on 2026-08-28"`, which records a
stage change, not an ingested jurisdiction event.

**Chain failure point: steps 5–8.** Owner assigned, but no SLA, no
notification, and no actual event content. A parked record.

### 7714 E Onyx Ct, Scottsdale — BLDR-00567-2026 / BLDR-00568-2026

The second review failure appeared **only in the Scottsdale Civic Access
portal on 9/2**. No email was ever sent. Jay learned about it from the
client, whose note is now pasted into the Notion record by hand:

> "🚨 CLIENT ESCALATION (9/2, Jennifer Ebner): city portal shows a SECOND
> review failure on 9/2 … Client has stated she will engage another
> architect if meaningful progress does not resume immediately."

**Chain failure point: step 1.** The event never entered the business at
all, because nothing watches the portal.

### The common root cause

**Correction (verified 2026-09-03 against the live n8n instance).** An
earlier draft of this document said there was no ingest path at all. That
was true when these failures happened but is not true today: the n8n
workflow *Project Update Capture (Email to Notion)* has been active since
**2026-08-23**, watches both `jay@` and `info@`, matches mail to projects
with an LLM, appends a status log and logs unmatched mail to an exception
table.

What it does not do is the part that failed: it tags Eric on everything
rather than routing by event type, sets no SLA, has no acknowledgement
state, never escalates, and cannot see a portal. The Scottsdale second
rejection on 9/2 happened *while it was running*, and Jay still heard it
from the client first.

So the gap is not ingest. It is **ownership, clocks and escalation** —
plus a portal blind spot. This package supplies exactly those and reuses
the existing transports rather than replacing them.

Also corrected: Scottsdale review notices *are* emailed — to
`info@professionalcadesign.com`, not `jay@`. The project record for 7714
E Onyx Ct states plainly: *"All city notifications go to info@."*

The Notion schema was never the gap. It already has `Current Owner`,
`Required Next Action`, `Follow-Up Date`, `Waiting On`, `Blocker Owner`
and `Automation Status`. Nothing was filling them from real events.

Over the trailing 120 days, at least **52 inbound jurisdiction messages**
arrived with no ownership routing on them.

## The fix

| module | responsibility |
|---|---|
| `sources.py` | Decide what is an operational event; drop vendor noise |
| `matching.py` | Identify the project by permit number, APN, then address |
| `classify.py` | Classify from real jurisdiction wording, with confidence |
| `routing.py` | Exactly one owner, one next action, one SLA clock |
| `ports.py` | Notion and notification boundaries; typed connector failures |
| `pipeline.py` | The chain, and every way it is allowed to stop |
| `audit.py` | Append-only trail: every step, every decision, every timestamp |

### Fail closed, never silent

| condition | result |
|---|---|
| No project matched | `PROJECT_NOT_MATCHED` → exception → Jay alerted |
| Several projects match | `PROJECT_AMBIGUOUS` → exception → Jay alerted |
| Classification below 0.60 | `CLASSIFICATION_UNCERTAIN` → exception → Jay alerted |
| No routing rule or stage owner | `OWNER_UNDETERMINED` → exception → Jay alerted |
| Notion write fails | `NOTION_WRITE_FAILED` → exception → **not released** |
| Notification fails | `NOTIFICATION_FAILED` → exception → **not released** |
| The alert itself fails | Written to disk anyway; `status` surfaces it |

`7714 E ONYX CT` is the case that matters most: MAIN HOUSE and CASITA
share one address and differ only by permit number, so an address-only
event is reported as ambiguous rather than resolved by picking one.

## Running it

```bash
python -m permit_pipeline ingest --messages inbox.json --projects projects.json \
                                 --clients clients.json
python -m permit_pipeline sweep    # escalate anything past its SLA
python -m permit_pipeline status   # open exceptions + unacknowledged work
python -m permit_pipeline ack EVT-...
```

Without `NOTION_API_KEY` the CLI says so on stderr and runs dry rather
than pretending to write. `sweep` belongs on a schedule — it is what
turns a missed acknowledgement into an escalation instead of a silence.

## Ownership rules

| event | owner | SLA |
|---|---|---|
| Corrections required | Guillermo (drafter) | 24h |
| Info request | Eric (coordinator) | 48h |
| Fees due | Jay | 24h |
| Approved / inspection | Eric | 48h |
| Client escalation | Jay | 4h |

Anything else falls back to the project's stage owner, and if that cannot
be determined the event becomes an exception. There is no "team" owner
and no round-robin: shared ownership is how Riata sat for 72 days.

## Credentials

Nothing new was provisioned. `config.py` resolves secrets from the paths
JAY-OS already uses, in order: the process environment, this repository's
`.env`, then the Hermes store at `%LOCALAPPDATA%\hermes\.env`, which is
where `NOTION_API_KEY` and the `EMAIL_*` SMTP settings already live.

A missing credential raises `ConnectorError` and stops the run. There is
no silent downgrade to simulated writes; `--dry-run` is the only way to
get those and it announces itself on stderr.

## The Notion write path

The Notion connection this repository can reach directly (`NOTION_API_KEY`,
integration "Hermes PCD") is **read-only** — it answers `403
restricted_resource` on page updates and comments. Rather than provision a
second secret, writes go through an n8n bridge that performs them under the
write-capable Notion credential production automations already use.

* Workflow: **JAY-OS Permit Pipeline — Notion Write Bridge** (`jumW0bJb8luKoA2S`, active)
* Credential reused: `Notion account 2` — the same one *Queue Router* writes with
* Auth: shared secret header; a wrong secret gets `403 {"ok":false}`
* Config: `JAYOS_NOTION_BRIDGE_URL` / `JAYOS_NOTION_BRIDGE_SECRET` in the
  gitignored `.env`

The preflight proves the *Projects database*, not just the credential.
`/v1/users/me` shows the bridge answers and the token is valid, but says
nothing about whether that credential can see the database we write to or
whether its columns still match. So `check(database_id)` also retrieves the
schema, verifies all ten properties this pipeline sets (name *and* type),
and runs a one-row query. All reads; nothing is modified. A renamed or
retyped column now fails the preflight instead of silently dropping the
field it could not set.

`factory.notion_writer()` prefers the bridge, falls back to the direct API
if a write-capable token is ever configured, and raises if neither exists.

The client reads the bridge strictly. The bridge can answer HTTP 200 with
`ok:false`, so a write counts only when `ok` is true **and** Notion echoed
an object back. Anything else raises and the pipeline fails closed — a 200
that never reached Notion is exactly the kind of false success this whole
package exists to prevent.

## Decision: `Automation Enabled` is a billing switch, not a permit switch

Option C. This pipeline neither reads nor sets `Automation Enabled`.

The property is checked by seven n8n workflows, and every one of them is
billing or contract automation: *Send Onboarding Link*, *Deposit Paid to
Project Setup*, *CD Complete to 40pct Invoice*, *40pct Paid to Submission
Unlock*, *Approved to Final Invoice*, *Final Paid to Release*, and *Guard:
Billing Status*. Ticking it on permit projects to enable permit tracking
would start sending onboarding links, contracts and invoices to real
clients. Respecting it would instead leave permits unwatched because a
billing flag was left unchecked — the exact silent fall-through this work
exists to remove.

So permit handling is gated on its own terms: a project is in scope when
it has a resolvable permit identity. Exclusions are explicit (`ZZ-TEST`
project type for production runs, and closed stages), never implied by an
unrelated toggle.

## Acknowledgement is a separate state from notification

`NOTIFIED` -> `ACKNOWLEDGED` -> `COMPLETED`, with a clock on each of the
first two. Acknowledgement is detected from the owner replying on the
Notion page they were @mentioned on, which needs only read access and
cannot be faked by the pipeline notifying itself. Completion is detected
from the project stage moving past redlines.

Both clocks escalate to Jay. The second one is the Riata case: somebody
having seen it is not the same as it being done.

## Watchers are themselves watched

`health.py` keeps a heartbeat per watcher. A watcher that stops checking
in — or has never run, or whose most recent outcome was a failure — is
reported unhealthy and alerted to Jay once, re-arming when it recovers.
A watcher that dies quietly would rebuild the original failure.

## What is scheduled

**Currently nothing — both tasks are registered but DISABLED** (Jay's call,
2026-09-03). Nothing runs on a timer: no escalations, no health alerts, no
mail. The CLI still works on demand.

Re-enable with `schtasks /Change /TN "<name>" /ENABLE`, or by running
`register-permit-tasks.cmd` again. `disable-permit-tasks.cmd` turns them
back off and carries the re-enable commands in its header.

When enabled, registered by `register-permit-tasks.cmd`:

| task | cadence | does |
|---|---|---|
| `JAY-OS Permit Sweep` | hourly | escalates both clocks; preflights the Notion and email transports |
| `JAY-OS Permit Health` | every 3 hours | dead-man check; alerts Jay when a watcher goes quiet |

They are separate on purpose. `sweep` also runs a health sweep, but a
sweep that has died cannot report itself — so `health` runs independently
and is what actually catches a dead sweep.

The sweep's preflight is a real check, not an assumption: it reads
`/v1/users/me` *through* the n8n bridge (exercising the webhook, the
shared secret and the Notion credential without touching a record), and
authenticates to SMTP without sending. Both then stamp their heartbeat.

`verify-permit-tasks.cmd` triggers both now and prints what the Task
Scheduler recorded.

### The watcher registry is checked in, not remembered

`watchers.py` holds the manifest: which watcher each scheduled task runs,
and whether it is meant to be enabled. Health expects exactly the watchers
that are scheduled *and* enabled.

This replaced `JAYOS_PERMIT_WATCHERS` as the source of truth, because an
environment variable somebody has to remember is not a safety mechanism:
schedule a watcher, forget the variable, and health silently ignores a dead
process. The variable still works as a one-off override and is validated
against the manifest when set.

`permit_pipeline watchers` asks Windows what it is actually running and
reports drift in both directions — a task enabled in the scheduler that the
manifest thinks is off (health ignoring a live watcher), and a watcher the
manifest expects that the scheduler does not have. Adding that check
immediately found one: the health task itself had no manifest entry, so
nothing was monitoring the monitor.

## Shadow mode

`permit_pipeline shadow` runs the real matcher and classifier over real
mail and records what it *would* have done — proposed project, confidence,
owner, next action — while writing nothing and notifying nobody. The
guarantee is structural: no connector is constructed, and a test asserts
that opening one raises.

Ambiguous, unmatched and unclassified messages land in review queues rather
than being assigned. `scorecard()` measures precision only over rows a human
has judged, so an unreviewed run reports "not measurable" instead of 100%.

First run over 127 real jurisdiction messages: **9.3% auto-assign**, far
below the gate. Two causes, and they are different problems:

1. **Missing identifiers.** Triage (`permit_pipeline triage`) narrows this
   from "247 projects missing a permit number" to **11 actionable, 5 with
   recent jurisdiction mail** — the rest are closed, dormant, or not yet
   submitted, where a missing number is correct.
2. **Corpus fidelity.** The measurement ran on subjects and snippets. The
   same Phoenix message classifies `unknown (0.00)` on its snippet and
   `corrections_required (0.97)` on its full body. Production ingest reads
   full bodies, so 9.3% is a floor, not the expected rate.

## Activation gate

Live ingest stays off until: active permitting projects carry identifiers;
the database preflight passes (it does); shadow matching clears 95% recall
and 99% precision with everything below threshold going to review; ambiguous
matches queue for a human; duplicates are idempotent (they are); backfill
cannot notify (it cannot); Guillermo and Eric each get one controlled test
notification; the kill switch is verified; and the commits are on the
authorized remote.

## Known gap this trace exposed

Scottsdale's Civic Access portal does **not** email review outcomes. The
7714 E Onyx Ct second review failure existed only as a portal state
change, so no email pipeline — this one included — can catch it.

`Channel.JURISDICTION_PORTAL` exists and is proven end to end
(`test_scottsdale_portal_event_hits_the_casita_not_the_main_house`), so
the chain is ready for portal events. What is still missing is the
**poller** that produces them: something that signs into Civic Access on
the `info@professionalcadesign.com` account, reads review status per
permit, and emits a `RawEvent` when the status changes.

Until that exists, Scottsdale-style failures remain discoverable only by
a human opening the portal. That is a real, un-closed hole and should not
be reported as covered.
