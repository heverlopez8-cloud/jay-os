# Plan: Expanding What Hermes Can Do

Owner: Jay (Hever V. Lopez) · Started 2026-09-09

## Principle

Expand Hermes by **widening its legitimate read/write areas**, not by pushing it to
read from its own config directory. Hermes declined that, correctly — an agent that
can self-load binding instructions from a hidden config path is a weak spot, not a
feature. Keep that refusal intact. Everything below works with it.

Access grows in phases. Each phase gets used for real work before the next one opens.

---

## Where Hermes stands today

| Capability | State |
|---|---|
| Own config dir (`%LOCALAPPDATA%\hermes\`) | Reads credentials; **refuses** to read context/instructions from it |
| Vault — `01_INBOX`, workspace | Read/write |
| Notion "Hermes PCD" integration | **Read-only** — 403 on writes |
| Gmail OAuth token | Held (`google_token.json`) |
| SMTP (`EMAIL_*`) | Held, used for notifications |
| `jay-os` repo on Linux (192.168.0.48) | No direct access |

---

## Phase 1 — Standing context (do now, no new credentials)

Goal: Hermes knows who Jay is and how to write, without a re-paste every session.

1. Create `_context/` (or use `01_INBOX`) in the vault.
2. Place `jay-context.md` there.
3. Tell Hermes: read it at the start of any task involving Jay's voice or permit work.

Done when: Hermes summarizes the tone rules back correctly without being re-fed them.

**Risk: low.** No credentials, no write scope, reversible by deleting one file.

## Phase 2 — Read access to project truth

Goal: stop hand-feeding Hermes project details it could look up.

1. Confirm Hermes can query the Notion **Projects** database read-only (already has it).
2. Decide how it sees permit working files (`permits/<PERMIT#>/` narratives, cover emails):
   either sync that folder into the vault, or expose the repo read-only over the LAN.

Done when: given a permit number, Hermes returns address, APN, stage, and what's on file
without being told.

**Risk: low.** Read-only. Worst case is stale data.

## Phase 3 — Narrow Notion write access

Goal: Hermes maintains project records instead of reporting what should change.

1. In Notion, grant the "Hermes PCD" integration write capability — **scoped to the
   Projects database only**, not workspace-wide.
2. Allowed writes: update stage, append permit numbers, add a dated activity note.
3. Not allowed: create or delete projects, touch other databases.

Done when: a deficiency notice arrives and the project stage updates itself correctly.

**Risk: medium.** Bad writes are recoverable via Notion page history, but keep the scope
tight — one database, named fields.

## Phase 4 — Email drafting, not sending

Goal: Hermes produces the reply; Jay still hits send.

1. Hermes writes to **Gmail drafts** only. No send scope.
2. Drafts follow the Phase 1 tone rules and the section 7 checklist in `jay-context.md`.
3. Jay reviews, edits, sends.

Done when: several drafts go out with little or no editing.

**Risk: medium.** Contained as long as send authority stays with Jay.

## Phase 5 — Autonomy on low-risk loops

Only after Phase 4 has a track record.

- Triage incoming jurisdiction mail: detect deficiency notices, classify what's being
  asked, match to project, draft a reply, notify Jay.
- Maintain the permits folder — create `permits/<PERMIT#>/` scaffolding for new records.
- Still **no auto-send to any jurisdiction or client.**

**Risk: higher.** Gate on Phase 4 results, not on a date.

---

## Standing guardrails — all phases

1. **`secrets/` is never read, printed, or transmitted.** No exceptions.
2. **No outbound email to a jurisdiction or client without Jay's explicit send.**
   Permit correspondence is a public record; a wrong send costs a review cycle.
3. **Never invent specs.** Conductor sizes, tonnages, calculated loads, code editions,
   utility provider — `[__]` placeholder and flag it. A wrong number in a municipal
   submittal is worse than a visible blank.
4. **Notion writes stay scoped** to named databases and fields.
5. **Hermes keeps its config-directory refusal.** Context lives in the vault, where it
   is visible and reviewable, not in a credential store.
6. **Each phase earns the next** through real use, not elapsed time.

---

## Open decisions for Jay

1. **Where in the vault** should `jay-context.md` live — `01_INBOX`, or a dedicated
   `_context/` folder? (Recommend `_context/`: inbox implies triage-then-file, and this
   file is meant to stay put.)
2. **How should Hermes see the permits folder** — sync into the vault, or read-only
   network access to the repo?
3. **How far to go this round?** Recommend Phases 1 and 2 now, sit on them a couple
   weeks of real permit work, then decide on Phase 3.
