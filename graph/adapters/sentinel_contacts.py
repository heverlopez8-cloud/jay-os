"""Email context from the running Email Sentinel: who is actually a client.

WHY THIS EXISTS
----------------
`projects.json`'s `client_emails` field is empty for all 451 projects -- the
Notion sync never populates it. But the sentinel itself already knows who
clients are, from two places that are each a source of truth in their own
right:

  1. `config/sentinel.toml`'s `known_clients` / `known_consultants` lists.
     Each entry is a HUMAN DECISION -- Jay classified that address by hand,
     with a comment naming the person and usually the project. That is an
     ASSERTED fact about identity and role, not an inference.

  2. The sentinel's own Postgres ledger (`email_events`), which is IN
     PRODUCTION right now (shadow mode: reading and matching real mail,
     not yet acting). Every row where `match_disposition = 'linked'` has
     already cleared `email_sentinel.contract.AUTO_LINK_CONFIDENCE` --
     the production matcher's own evidence ladder decided this sender's
     email was THIS project, at 0.95+ confidence. That is also asserted,
     not a graph-engine guess.

WHY THIS IS NOT "treat every sender as a client"
-------------------------------------------------
A first unfiltered look at the matched senders was mostly noise: city
staff and donotreply@ portal addresses (`donotreplyshapephx@phoenix.gov`,
`noreply@permitcenter.maricopa.gov`), and SaaS notification senders
(`notify@mail.notion.com`, `docs@email.pandadoc.net`). None of those are a
client. This adapter filters jurisdiction senders using the PRODUCTION
jurisdiction detector (`email_sentinel.extraction.jurisdiction_of`) -- the
same one the sentinel itself uses -- and filters known SaaS notification
domains by a short declared list, rather than inventing new detection.

WHAT REMAINS AFTER FILTERING, ON THE DATA SEEN 2026-09-10
-----------------------------------------------------------
Of 93 matched inbound rows and 17 distinct senders: 9 are jurisdiction
staff/portals (filtered), 2 are SaaS notifications (filtered), 2 are in
`known_clients`, 2 are in `known_consultants`. Small and real, not a flood.

READ-ONLY, LIVE PRODUCTION DATABASE
------------------------------------
This shells out to `docker exec ... psql` for a read query against the
sentinel's live Postgres container. It issues SELECT only, never INSERT,
UPDATE or DELETE -- the graph engine has no business writing into a
production ledger it does not own. If the container is not running or the
query fails, this raises rather than returning zero rows.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Iterator

from .base import SourceRecord, utc_stamp

REPO_ROOT = Path(__file__).resolve().parents[2]
SENTINEL_TOML = REPO_ROOT / "apps/email-sentinel/config/sentinel.toml"
SENTINEL_SRC = REPO_ROOT / "apps/email-sentinel/src"
#: `jurisdiction_of` reaches into `permit_pipeline` for its domain list, so
#: that path must be importable too -- the same two paths `_reuse.py` adds.
ORCHESTRATOR = REPO_ROOT / "orchestrator"

#: SaaS/tool senders seen in the ledger that are not people. Declared, not
#: guessed -- extending this is a one-line reviewed change, same policy as
#: `places.ARIZONA_JURISDICTIONS`.
NOTIFICATION_DOMAINS = frozenset({
    "mail.notion.com", "mail.notion.so", "email.pandadoc.net",
    "email.pandadoc.com",
})

#: `docker exec <container> psql ...`. The container name the compose file
#: gives the sentinel's own Postgres. Override for a different deployment.
DEFAULT_CONTAINER = "email-sentinel-postgres-1"
DEFAULT_DB = "sentinel"
DEFAULT_USER = "sentinel"

_COMMENT_RE = re.compile(r'"\s*,?\s*#\s*(.+)$')


def _jurisdiction_of():
    for candidate in (SENTINEL_SRC, ORCHESTRATOR):
        text = str(candidate)
        if candidate.exists() and text not in sys.path:
            sys.path.insert(0, text)
    from email_sentinel.extraction import jurisdiction_of
    return jurisdiction_of


def _is_notification_sender(email: str) -> bool:
    domain = email.rsplit("@", 1)[-1].lower()
    return domain in NOTIFICATION_DOMAINS


def _parse_known_list(toml_text: str, list_name: str) -> list[tuple[str, str]]:
    """Pull `list_name = [...]` out of the TOML by hand, comments intact.

    `tomllib` discards comments, and the comment is exactly the payload this
    adapter needs (the name and project hint Jay wrote next to each address).
    Regex-over-a-known-block is uglier than a proper parser, but it is the
    only way to keep that text without re-authoring the config file's format.
    """
    block = re.search(rf"(?ms)^{re.escape(list_name)}\s*=\s*\[(.*?)\]", toml_text)
    if not block:
        return []
    out = []
    for line in block.group(1).splitlines():
        match = re.match(r'\s*"([^"]+@[^"]+)"\s*,?\s*(?:#\s*(.*))?$', line)
        if match:
            out.append((match.group(1).strip().lower(), (match.group(2) or "").strip()))
    return out


class SentinelContacts:
    """Known client/consultant identities from sentinel.toml. No project link."""

    name = "sentinel_known_contacts"

    def __init__(self, toml_path: Path = SENTINEL_TOML) -> None:
        self.toml_path = toml_path

    def records(self) -> Iterator[SourceRecord]:
        if not self.toml_path.exists():
            raise FileNotFoundError(f"no sentinel config at {self.toml_path}")
        text = self.toml_path.read_text(encoding="utf-8")
        observed = utc_stamp(None)
        for role, list_name in (("client", "known_clients"),
                                ("consultant", "known_consultants")):
            for email, comment in _parse_known_list(text, list_name):
                yield SourceRecord(
                    kind="known_contact", source=self.name,
                    source_ref=f"sentinel.toml#{list_name}:{email}",
                    observed_at=observed,
                    payload={"email": email, "role": role, "comment": comment})


class SentinelMatchedEmails:
    """Real sender-to-project links already computed by the production matcher.

    Only `direction='inbound' AND match_disposition='linked'` rows qualify --
    the sentinel's own contract already decided those at AUTO_LINK_CONFIDENCE.
    Outbound mail and `proposed`/`ambiguous` rows are excluded: outbound only
    proves PCD replied, not who the client is, and `proposed` is a human
    judgement the sentinel itself has not yet made.
    """

    name = "sentinel_matched_emails"

    def __init__(self, container: str = DEFAULT_CONTAINER, db: str = DEFAULT_DB,
                user: str = DEFAULT_USER) -> None:
        self.container = container
        self.db = db
        self.user = user

    def _query(self) -> str:
        sql = ("SELECT sender, project_id, match_confidence, received_at, subject "
              "FROM email_events WHERE project_id IS NOT NULL "
              "AND direction = 'inbound' AND match_disposition = 'linked' "
              "ORDER BY received_at")
        try:
            result = subprocess.run(
                ["docker", "exec", self.container, "psql", "-U", self.user,
                 "-d", self.db, "-t", "-A", "-F", "\x1f", "-c", sql],
                capture_output=True, text=True, timeout=30, check=True)
        except FileNotFoundError as error:
            raise RuntimeError("docker is not available to read the sentinel ledger") from error
        except subprocess.CalledProcessError as error:
            raise RuntimeError(
                f"reading the sentinel ledger failed: {error.stderr.strip()}") from error
        return result.stdout

    def records(self) -> Iterator[SourceRecord]:
        jurisdiction_of = _jurisdiction_of()
        raw = self._query()
        for lineno, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            parts = line.split("\x1f")
            if len(parts) != 5:
                continue
            sender, project_id, confidence, received_at, subject = parts
            sender = sender.strip().lower()
            if not sender or jurisdiction_of(sender) or _is_notification_sender(sender):
                continue  # jurisdiction staff/portal and SaaS senders are not clients
            yield SourceRecord(
                kind="matched_email", source=self.name,
                source_ref=f"email_events#{self.container}:{lineno}:{sender}",
                observed_at=utc_stamp(received_at),
                payload={"sender": sender, "project_external_id": project_id,
                         "match_confidence": float(confidence), "subject": subject})
