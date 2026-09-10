---
title: Proposal — reconcile the JAY-OS Graph Engine (PCD) with graphify and DATA_DICTIONARY.md
type: proposal
status: awaiting-jay
created: 2026-09-10
author: Claude (Builder session, jayserver)
addressed-to: Jay
---

# Proposal: one graph, two extraction pipelines

## What exists today

Two independent graph systems both describe parts of JAY-OS, built without
awareness of each other:

1. **graphify** (`C:\JAY-OS\graphify-out\`) — semantic extraction over vault
   notes. 689 nodes, 1,086 edges, 46 hyperedges in its most recent scanned
   build (2026-08-28). Its own `VALIDATION_REPORT.md` verdict on that build:
   **"BLOCKING ISSUES FOUND — do not merge without review"** (44 dangling
   edges, 3 duplicate node IDs, 5 off-rubric confidence scores, 8 referenced
   source files missing from disk). It also predates the "vault-only"
   cleanup adopted 2026-08-30 and still includes `05_KNOWLEDGE/Unani` —
   health/personal content `AI_CONTEXT.md` says a PCD business session
   must never receive.

2. **The JAY-OS Graph Engine** (`/home/jayserver/jay-os/graph/`, this
   session's build) — deterministic extraction over PCD's structured
   business data: the Notion Projects catalogue, permit working folders, the
   Email Sentinel's production ledger, and `sentinel.toml`'s hand-classified
   client/consultant list. 1,308 entities, 2,027 relationships, all with
   full provenance. Tested (98 tests), piloted, and running a nightly
   re-ingest via cron. Does not read or write vault notes.

Neither has written a relationship into any vault note. graphify's own
dashboard confirms this for itself; the Graph Engine's backlink writer is
dry-run only and has never been pointed at the real vault with `--write`.

## What this session already reconciled, without asking

Two structural mismatches were fixed locally, since they were safe,
reversible, and needed no owner decision:

- **ID scheme.** `DATA_DICTIONARY.md` states its own rule: *"Do not assign
  new IDs to information that already has an established identifier
  elsewhere... record a recommended migration separately instead of
  renaming in place."* Every Graph Engine entity that already has one
  (a Notion page ID, an APN, a permit number, an email) was **not**
  renamed. Instead, `graph/crosswalk.py` and a new `vault_crosswalk` table
  record "this Graph Engine entity corresponds to that vault ID" as a
  separate, additive fact — reversible, and exactly what the dictionary
  asked for instead of renaming in place.
- **Output schema.** `graph/graphify_export.py` renders the whole Graph
  Engine dataset in graphify's own JSON shape (`nodes`/`edges`/`hyperedges`,
  the `EXTRACTED`/`INFERRED` confidence categories graphify's validator
  checks against). Run `python -m graph graphify-export` to produce
  `graph/graphify_compatible_export.json`. It writes only that one local
  file — never into `graphify-out/`.

## What needs your decision

**1. New ID prefixes for PCD-specific concepts.** `DATA_DICTIONARY.md` has
no entry for a permit, an invoice, a jurisdiction, a review comment, a
contract, or an agent — concepts the vault's general life/business
dictionary was never scoped to cover. Following the same path `BUS-`,
`ENT-`, `AST-`, `OBL-` took (proposed in a business README, adopted by you,
then registered in the dictionary), the Graph Engine proposes:

| Prefix | For |
|---|---|
| `PMT-` | Permit — though permit numbers like `B26-2089` are already unique and human-meaningful; worth discussing whether they need a synthetic prefix at all, or should just be cited by their own number |
| `JUR-` | Jurisdiction |
| `RVC-` | Review comment |
| `INV-` | Invoice |
| `PAY-` | Payment |
| `CON-` | Contract |

Not proposed for `CLIENT`, `TASK`, `DOCUMENT`, `AGENT`, `WORKFLOW`, `CALL`,
`EMAIL`, `OUTCOME` — these map reasonably onto roles the dictionary already
has a home for (a client is a `PER-`/`ORG-` playing a `CLIENT_OF` role; a
task, document, or call has no obvious vault-note equivalent and probably
shouldn't get one invented for it).

**2. Whether graphify should be rebuilt clean before any real merge.**
Its current output carries its own "do not merge" verdict and out-of-scope
content. The Graph Engine will not import it as-is, and has not. A clean,
vault-only, Unani-excluded rebuild would be the natural trigger for pulling
`graph/graphify_compatible_export.json` in as one input alongside
graphify's own extraction — at that point genuinely "one graph," combining
semantic notes-graph coverage with the deterministic business-data coverage
this session built.

**3. Review, per `EXECUTION-CHAIN.md`.** This work happened in the manual
Architect→Builder→Reviewer loop (ChatGPT drafted the original mission,
Claude built it). It has not yet gone to ChatGPT as Reviewer. Recommend
that happen — with this proposal attached — before any further step:
before graphify is rebuilt on this basis, before any new prefix is
registered in `DATA_DICTIONARY.md`, and before the Graph Engine's backlink
writer is ever pointed at the real vault with `--write`.

## What this proposal does NOT ask for

- No permission to write into the vault. The Graph Engine's backlink
  writer stays dry-run-only regardless of this proposal's outcome, until a
  separate, explicit `--write` decision.
- No permission to modify or rebuild `graphify-out/`. That system's own
  tooling, cadence, and validation are untouched.
- No claim that the Graph Engine's business-data model should replace or
  govern the vault's general-purpose one. They describe different things.

## Files this proposal references

- `graph/crosswalk.py` — the ID reconciliation module and its rationale
- `graph/graphify_export.py` — the schema-compatible export
- `graph/ARCHITECTURE.md` — the Graph Engine's own architecture doc
- `graphify-out/VALIDATION_REPORT.md` — the blocking-issues verdict cited above
- `05_KNOWLEDGE/AI Systems/Graphify Dashboard.md` — confirms graphify has
  written nothing into vault notes to date
