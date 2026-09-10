"""Canonical entity pages, and careful backlinks into existing notes.

TWO DIFFERENT TRUST LEVELS, TWO DIFFERENT BEHAVIOURS
---------------------------------------------------
`write_pages` generates files the engine OWNS, under `graph/pages/`. Those are
derived artifacts: safe to delete, safe to regenerate, nobody hand-edits them.

`inject_backlinks` touches notes JAY HAS WRITTEN. Different rules entirely:
  * It only ever replaces the text between two managed markers. Everything
    outside them is never read for editing and never rewritten.
  * It is dry-run by default. Writing into someone's vault is not something a
    build step should do because it can.
  * It refuses to link WEAK edges. A note flooded with speculative links is
    worse than a note with none -- it trains the reader to ignore the links.

WHY A MARKED BLOCK AND NOT APPENDED LINES
-----------------------------------------
Idempotency. Appending "related: X" on every run grows the note forever. A
delimited block is computed fresh and swapped, so run one and run fifty produce
identical files -- which is the actual requirement, not merely a nice property.
"""
from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

from .contract import Disposition, EntityType, actionable
from .store import GraphStore

BEGIN = "<!-- jayos-graph:begin -->"
END = "<!-- jayos-graph:end -->"

_BLOCK = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.S)
_FRONTMATTER = re.compile(r"^---\n(.*?)\n---\n", re.S)


@dataclasses.dataclass
class PageReport:
    written: int = 0
    unchanged: int = 0
    skipped: int = 0
    paths: list[str] = dataclasses.field(default_factory=list)


def _yaml_list(values: list[str]) -> str:
    if not values:
        return "[]"
    return "\n" + "\n".join(f"  - {value}" for value in values)


def render_entity_page(store: GraphStore, entity_id: str) -> str:
    """One canonical page: frontmatter, facts, relationships, backlinks.

    Provenance is printed on every edge. A page a human cannot audit is just
    a prettier guess.
    """
    row = store.entity(entity_id)
    if row is None:
        raise KeyError(entity_id)
    attributes = json.loads(row["attributes"] or "{}")
    aliases = store.aliases(entity_id)
    refs = store.source_refs(entity_id)

    lines = [
        "---",
        f"entity_id: {row['entity_id']}",
        f"entity_type: {row['entity_type']}",
        f"canonical_name: {json.dumps(row['canonical_name'])}",
        f"aliases: {_yaml_list(aliases)}",
        f"created_at: {row['created_at']}",
        f"updated_at: {row['updated_at']}",
        f"confidence: {row['confidence']}",
        f"status: {row['status']}",
        f"source_refs: {_yaml_list(sorted({f'{r['source']}:{r['source_ref']}' for r in refs}))}",
        "generated_by: jayos-graph-engine",
        "---",
        "",
        f"# {row['canonical_name']}",
        "",
        f"`{row['entity_type']}` · `{row['entity_id']}`",
        "",
    ]

    if attributes:
        lines += ["## Facts", ""]
        for field in sorted(attributes):
            value = attributes[field]
            if value not in (None, "", [], {}):
                lines.append(f"- **{field}**: {value}")
        lines.append("")

    outbound = store.relationships_from(entity_id)
    if outbound:
        lines += ["## Relationships", ""]
        for edge in outbound:
            other = store.entity(edge["target_entity"])
            label = other["canonical_name"] if other else edge["target_entity"]
            lines.append(
                f"- **{edge['relationship_type']}** → [[{edge['target_entity']}|{label}]]  \n"
                f"  `{edge['disposition']}` confidence {edge['confidence']} · "
                f"via `{edge['extraction_method']}` · source `{edge['source_reference']}` · "
                f"observed {edge['observed_at']}")
        lines.append("")

    inbound = store.relationships_to(entity_id)
    if inbound:
        lines += ["## Backlinks", ""]
        for edge in inbound:
            other = store.entity(edge["source_entity"])
            label = other["canonical_name"] if other else edge["source_entity"]
            lines.append(
                f"- [[{edge['source_entity']}|{label}]] **{edge['relationship_type']}** → this  \n"
                f"  `{edge['disposition']}` confidence {edge['confidence']} · "
                f"via `{edge['extraction_method']}` · source `{edge['source_reference']}`")
        lines.append("")

    merges = [m for m in store.candidate_merges()
              if entity_id in (m["left_entity"], m["right_entity"])]
    if merges:
        lines += ["## Candidate merges (unresolved — needs Jay)", ""]
        for merge in merges:
            other = (merge["right_entity"] if merge["left_entity"] == entity_id
                     else merge["left_entity"])
            lines.append(
                f"- possible duplicate of [[{other}]] — confidence "
                f"{merge['confidence']:.2f} — {merge['basis']}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_pages(store: GraphStore, out_dir: str | Path,
                entity_type: EntityType | None = None) -> PageReport:
    """Generate engine-owned pages. Unchanged files are left untouched."""
    root = Path(out_dir)
    report = PageReport()
    for row in store.entities(entity_type):
        folder = root / row["entity_type"]
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{row['entity_id']}.md"
        content = render_entity_page(store, row["entity_id"])
        if target.exists() and target.read_text(encoding="utf-8") == content:
            report.unchanged += 1
            continue
        target.write_text(content, encoding="utf-8")
        report.written += 1
        report.paths.append(str(target))
    return report


def _managed_block(store: GraphStore, entity_id: str, min_links: int = 1) -> str | None:
    """The block injected into a human note. None when nothing is worth saying.

    Only ASSERTED and LINKED edges qualify. PROPOSED and WEAK edges stay on the
    engine's own page where a human is expected to review them, rather than
    being asserted inside Jay's notes.
    """
    rows = [e for e in (store.relationships_from(entity_id)
                        + store.relationships_to(entity_id))
            if actionable(Disposition(e["disposition"]))]
    if len(rows) < min_links:
        return None
    seen: set[tuple[str, str]] = set()
    lines = [BEGIN,
             "<!-- Generated by the JAY-OS Graph Engine. Edits inside this "
             "block are overwritten; everything outside it is never touched. -->",
             f"> **Graph:** `{entity_id}`", ""]
    for edge in rows:
        outgoing = edge["source_entity"] == entity_id
        other_id = edge["target_entity"] if outgoing else edge["source_entity"]
        if other_id == entity_id:
            continue  # never link a note to itself
        pair = (edge["relationship_type"], other_id)
        if pair in seen:
            continue
        seen.add(pair)
        other = store.entity(other_id)
        label = other["canonical_name"] if other else other_id
        arrow = "→" if outgoing else "←"
        lines.append(f"> - {edge['relationship_type']} {arrow} [[{other_id}|{label}]]")
    lines += ["", END]
    return "\n".join(lines)


def inject_backlinks(store: GraphStore, vault_root: str | Path, *,
                     dry_run: bool = True, min_links: int = 1) -> PageReport:
    """Add/refresh the managed graph block in vault notes that declare an entity_id.

    A note opts IN by carrying `entity_id:` in its frontmatter. The engine does
    not guess which note is about which entity from the filename -- that is how
    you end up linking a note called "Notes" to everything.
    """
    root = Path(vault_root)
    report = PageReport()
    if not root.exists():
        raise FileNotFoundError(
            f"no vault at {root}. Refusing to silently skip the backlink step: "
            f"a missing vault and a vault with nothing to link look the same "
            f"in a report, and only one of them is fine.")
    for note in sorted(root.rglob("*.md")):
        text = note.read_text(encoding="utf-8")
        front = _FRONTMATTER.match(text)
        if not front:
            report.skipped += 1
            continue
        found = re.search(r"^entity_id:\s*(\S+)\s*$", front.group(1), re.M)
        if not found:
            report.skipped += 1
            continue
        entity_id = found.group(1).strip().strip("\"'")
        if store.entity(entity_id) is None:
            report.skipped += 1
            continue
        block = _managed_block(store, entity_id, min_links=min_links)
        if block is None:
            report.skipped += 1
            continue
        if _BLOCK.search(text):
            updated = _BLOCK.sub(lambda _m: block, text, count=1)
        else:
            updated = text.rstrip() + "\n\n" + block + "\n"
        if updated == text:
            report.unchanged += 1
            continue
        if not dry_run:
            note.write_text(updated, encoding="utf-8")
        report.written += 1
        report.paths.append(str(note))
    return report
