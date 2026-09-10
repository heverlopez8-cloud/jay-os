# JAY-OS Graph Integration Engine v1 — Architecture

An **integration layer** around JAY-OS. Not a replacement, not a rewrite.
Nothing in `apps/` or `orchestrator/` was modified to make this work.

## Data flow

```
    SOURCES (adapters/)                  REUSED FROM PRODUCTION
    ┌──────────────────────┐             ┌─────────────────────────────────┐
    │ projects_json  LIVE  │             │ email_sentinel.extraction       │
    │ permit_folders LIVE  │──┐       ┌──│   permit numbers, APNs,         │
    │ portal_csv     LIVE  │  │       │  │   address keys, jurisdictions   │
    ├──────────────────────┤  │       │  ├─────────────────────────────────┤
    │ granola    ┐         │  │       │  │ email_sentinel.contract         │
    │ quo        │ declared│  │       ├──│   AUTO_LINK=0.95 PROPOSE=0.75   │
    │ gmail      │ but not │  │       │  ├─────────────────────────────────┤
    │ notion     │ live in │  │       └──│ email_sentinel.projects.load    │
    │ drive      │ v1      │  │          │   451 projects, existing IDs    │
    │ roam       │         │  │          └─────────────────────────────────┘
    │ agent_outputs        │  │                    ▲
    │ city_review_comments │  │                    │ _reuse.py (imports,
    └──────────────────────┘  │                    │  never copies)
                              ▼
                    ┌──────────────────┐
                    │  SourceRecord    │  kind + source_ref + observed_at
                    │  (base.py)       │  — adapters cannot write edges
                    └────────┬─────────┘
                             ▼
    ┌────────────────────────────────────────────────────────────────┐
    │                    pipeline.py                                 │
    │  INGEST → ENTITY EXTRACTION → ENTITY RESOLUTION (resolve.py)    │
    │    → RELATION EXTRACTION → PROVENANCE CHECK                     │
    │    → CANONICAL ENTITY UPDATE → BACKLINK/GRAPH UPDATE            │
    │    → CANDIDATE LESSON GENERATION (lessons.py)                   │
    │  one bad record is REFUSED and recorded; the run continues      │
    └────────────────────────────┬───────────────────────────────────┘
                                 ▼
    ┌────────────────────────────────────────────────────────────────┐
    │              store.py — APPEND-ONLY SQLite                     │
    │  entities · entity_aliases · entity_source_refs                │
    │  relationships (UNIQUE s,p,o,source_ref → idempotent)          │
    │  relationship_retractions · candidate_merges                   │
    │  events · lessons · lesson_status_history · owner_overrides     │
    │  NO delete/drop/truncate/purge method exists                   │
    └───────┬──────────────────────┬─────────────────┬───────────────┘
            ▼                      ▼                 ▼
    ┌───────────────┐   ┌──────────────────┐  ┌──────────────────┐
    │ pages.py      │   │ health.py        │  │ lessons.py       │
    │ graph/pages/  │   │ ISOLATED NODE    │  │ CANDIDATE only — │
    │ engine-owned  │   │ RATE = headline  │  │ cannot promote   │
    ├───────────────┤   └──────────────────┘  └──────────────────┘
    │ inject_back-  │
    │ links() →     │   Jay's notes. Opt-in via frontmatter
    │ marked block  │   entity_id:. DRY RUN BY DEFAULT.
    │ only, idem-   │   Only ASSERTED/LINKED edges. Content
    │ potent        │   outside the markers is never rewritten.
    └───────────────┘
```

## The four refusals that define the engine

| Refusal | Where | Why |
|---|---|---|
| An edge with no source/method/confidence | `store.relate` | An unattributable fact cannot be audited or undone |
| An edge whose endpoint types the predicate rejects | `contract.validate_relationship` | Stops "generic links everywhere" — a category error is not a low-confidence fact |
| A fuzzy name match becoming a merge | `resolve.FUZZY_CEILING = 0.88 < 0.95` | A wrong merge is invisible and permanent; a split is visible and fixable |
| A lesson promoting itself | `store.set_lesson_status` | Production promotion is Jay's authority, enforced not requested |

## Asserted vs inferred

**ASSERTED (1.0)** — a source of truth said it directly: Notion lists this
permit under this project; this file sits in this permit's folder; the portal's
own City column names the city.

**INFERRED (< 0.95)** — the engine worked it out: which project a portal row
belongs to, which jurisdiction an address implies, who owns a property. These
carry `inferred:` extraction methods and are gated by the production
thresholds. They never become production truth on their own.

## What is deliberately NOT here

- No embeddings, vector store or RAG. v1 resolves on identifiers (APN, permit
  number, address key, email), which is both cheaper and auditable. Semantic
  search is a later, separate decision.
- No write path to Notion. The `Hermes PCD` integration is read-only (403 on
  write) and the graph is downstream of Notion, not upstream of it.
- No automatic vault writes. `backlinks` requires `--write`.

## Commands

```
python -m graph ingest --projects 0 --folders          # 0 = no limit
python -m graph ingest --portal export.csv --jurisdiction Phoenix
python -m graph lessons
python -m graph health [--vault PATH]
python -m graph pages
python -m graph backlinks --vault PATH          # dry run
python -m graph backlinks --vault PATH --write   # actually writes
python -m graph show PROJECT-...
python -m graph override --proposal "..." --decision "..."
python -m graph pilot
```
