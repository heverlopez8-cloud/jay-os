"""Boundaries to the outside world: Notion, notifications, project lookup.

Each port has a real implementation and a recording double. The pipeline
never imports a vendor SDK directly, so the decision logic can be proven
without touching Jay's live workspace -- and so a connector outage becomes
a typed failure the pipeline can fail closed on, rather than a traceback.
"""
from __future__ import annotations

import datetime as dt
import json
import urllib.error
import urllib.request
from typing import Any, Protocol

from .model import Notification, ProjectRecord, now


class ConnectorError(RuntimeError):
    """Any outward call that did not verifiably succeed."""


class ProjectDirectory(Protocol):
    def projects(self) -> list[ProjectRecord]: ...


class NotionPort(Protocol):
    def update_project(self, page_id: str, properties: dict[str, Any]) -> None: ...
    def comment(self, page_id: str, text: str,
                mention_user_id: str | None = None) -> None: ...


class NotifyPort(Protocol):
    def send(self, notification: Notification) -> Notification: ...


# --------------------------------------------------------------------------
# Payload construction -- kept here so the exact Notion write is auditable
# and testable without a network call.
# --------------------------------------------------------------------------
def build_project_properties(
    *,
    stage: str | None = None,
    status: list[str] | None = None,
    owner_notion_id: str | None = None,
    next_action: str | None = None,
    waiting_on: str | None = None,
    waiting_since: dt.datetime | None = None,
    follow_up: dt.datetime | None = None,
    automation_status: str | None = None,
    last_activity: dt.datetime | None = None,
) -> dict[str, Any]:
    """Build a Notion `properties` payload against the live Projects schema."""
    properties: dict[str, Any] = {}
    if stage is not None:
        properties["Stage"] = {"select": {"name": stage}}
    if status is not None:
        properties["Status"] = {
            "multi_select": [{"name": value} for value in status]
        }
    if owner_notion_id is not None:
        # A person property holding exactly one person: one accountable owner.
        properties["Current Owner"] = {"people": [{"object": "user",
                                                   "id": owner_notion_id}]}
    if next_action is not None:
        properties["Required Next Action"] = {
            "rich_text": [{"type": "text", "text": {"content": next_action[:2000]}}]
        }
    if waiting_on is not None:
        properties["Waiting On"] = {"select": {"name": waiting_on}}
    if waiting_since is not None:
        properties["Waiting Since"] = {"date": {"start": waiting_since.date().isoformat()}}
    if follow_up is not None:
        properties["Follow-Up Date"] = {"date": {"start": follow_up.date().isoformat()}}
    if automation_status is not None:
        properties["Automation Status"] = {
            "rich_text": [{"type": "text",
                           "text": {"content": automation_status[:2000]}}]
        }
    if last_activity is not None:
        properties["Last Meaningful Activity"] = {
            "date": {"start": last_activity.date().isoformat()}
        }
    return properties


class HttpNotionPort:
    """Production Notion writer. Requires a NOTION_API_KEY integration token."""

    def __init__(self, token: str, timeout: int = 20) -> None:
        if not token:
            raise ConnectorError("Notion token missing; refusing to run blind")
        self._token = token
        self._timeout = timeout

    def _request(self, method: str, url: str, payload: dict[str, Any]) -> Any:
        request = urllib.request.Request(
            url,
            method=method,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self._token}",
                "Notion-Version": "2022-06-28",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            body = error.read().decode(errors="replace")[:400]
            raise ConnectorError(f"Notion {method} {error.code}: {body}") from error
        except Exception as error:  # network, DNS, timeout
            raise ConnectorError(f"Notion {method} failed: {error}") from error

    def update_project(self, page_id: str, properties: dict[str, Any]) -> None:
        self._request(
            "PATCH",
            f"https://api.notion.com/v1/pages/{page_id}",
            {"properties": properties},
        )

    def comment(self, page_id: str, text: str,
                mention_user_id: str | None = None) -> None:
        """Comment on the project, @mentioning the owner.

        The mention is the part that matters: it is what makes Notion push
        the work at the person who owns it, instead of leaving it sitting
        in a field nobody opens.
        """
        rich_text: list[dict[str, Any]] = []
        if mention_user_id:
            rich_text.append({
                "type": "mention",
                "mention": {"type": "user",
                            "user": {"object": "user", "id": mention_user_id}},
            })
            rich_text.append({"type": "text", "text": {"content": " "}})
        rich_text.append({"type": "text", "text": {"content": text[:2000]}})
        self._request(
            "POST",
            "https://api.notion.com/v1/comments",
            {"parent": {"page_id": page_id}, "rich_text": rich_text},
        )


class RecordingNotionPort:
    """Captures the exact calls the pipeline would make. Used for proof runs."""

    def __init__(self, fail_on: set[str] | None = None) -> None:
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self.comments: list[tuple[str, str]] = []
        self.mentions: list[tuple[str, str | None]] = []
        self._fail_on = fail_on or set()

    def update_project(self, page_id: str, properties: dict[str, Any]) -> None:
        if "update" in self._fail_on:
            raise ConnectorError("simulated Notion outage on update_project")
        self.updates.append((page_id, properties))

    def comment(self, page_id: str, text: str,
                mention_user_id: str | None = None) -> None:
        if "comment" in self._fail_on:
            raise ConnectorError("simulated Notion outage on comment")
        self.comments.append((page_id, text))
        self.mentions.append((page_id, mention_user_id))


class RecordingNotifier:
    """Notification double that proves the message and its recipient."""

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[Notification] = []
        self._fail = fail

    def send(self, notification: Notification) -> Notification:
        if self._fail:
            raise ConnectorError("simulated notification transport failure")
        delivered = Notification(
            channel=notification.channel,
            recipient=notification.recipient,
            subject=notification.subject,
            body=notification.body,
            sent_at=now(),
            ok=True,
            detail="recorded",
        )
        self.sent.append(delivered)
        return delivered


class StaticDirectory:
    def __init__(self, records: list[ProjectRecord]) -> None:
        self._records = records

    def projects(self) -> list[ProjectRecord]:
        return list(self._records)


#: Every property that can carry a jurisdiction identifier. "Permit Number"
#: is the headline field, but PCD records the plot-plan CTR, the minor site
#: plan and the grading review in their own columns, and a jurisdiction is
#: just as likely to write about one of those. Reading only the headline
#: field left 13 projects with no matchable identifier at all.
PERMIT_BEARING_PROPERTIES: tuple[str, ...] = (
    "Permit Number",
    "PLOT PLAN PERMIT CTR",
    "PLOT PLAN PERMIT CTR (1)",
    "MINOR SITE PLAN",
    "G&D/CIVIL PERMIT CPGD",
    "Septic Permit OW (1)",
    "Well Permit",
)


class HttpNotionDirectory:
    """Reads the live Projects database into ProjectRecords.

    Read-only, so it works with a connection that has not been granted
    write access -- which is what lets the historical audit run before any
    production write path is enabled.
    """

    def __init__(self, token: str, database_id: str, timeout: int = 30) -> None:
        if not token:
            raise ConnectorError("Notion token missing; cannot read projects")
        self._token = token
        self._database_id = database_id
        self._timeout = timeout

    def _post(self, url: str, payload: dict[str, Any]) -> Any:
        request = urllib.request.Request(
            url, method="POST", data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {self._token}",
                     "Notion-Version": "2022-06-28",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            raise ConnectorError(
                f"Notion query {error.code}: "
                f"{error.read().decode(errors='replace')[:300]}"
            ) from error
        except Exception as error:
            raise ConnectorError(f"Notion query failed: {error}") from error

    @staticmethod
    def _plain(prop: dict[str, Any] | None) -> str:
        if not prop:
            return ""
        kind = prop.get("type")
        if kind in ("rich_text", "title"):
            return "".join(part.get("plain_text", "") for part in prop.get(kind, []))
        if kind == "select":
            return (prop.get("select") or {}).get("name", "") or ""
        return ""

    def projects(self) -> list[ProjectRecord]:
        records: list[ProjectRecord] = []
        cursor: str | None = None
        while True:
            payload: dict[str, Any] = {"page_size": 100}
            if cursor:
                payload["start_cursor"] = cursor
            body = self._post(
                f"https://api.notion.com/v1/databases/{self._database_id}/query",
                payload,
            )
            for page in body.get("results", []):
                properties = page.get("properties", {})
                name = self._plain(properties.get("Project Name"))
                if not name:
                    continue
                permit_text = "\n".join(
                    self._plain(properties.get(name))
                    for name in PERMIT_BEARING_PROPERTIES
                    if self._plain(properties.get(name))
                )
                status = tuple(
                    option.get("name", "")
                    for option in (properties.get("Status", {}) or {}).get(
                        "multi_select", []) or []
                )
                people = (properties.get("Current Owner", {}) or {}).get("people", [])
                records.append(ProjectRecord(
                    page_id=page["id"],
                    name=name,
                    permit_numbers=tuple(
                        line for line in permit_text.replace("\n", "|").split("|")
                        if line.strip()
                    ),
                    address=self._plain(properties.get("Address")) or name,
                    apn=self._plain(properties.get("Parcel Number"))
                        or self._plain(properties.get("APN")),
                    stage=self._plain(properties.get("Stage")) or None,
                    status=status,
                    current_owner_id=people[0]["id"] if people else None,
                    jurisdiction=self._plain(
                        properties.get("Jurisdiction (Verified)")) or None,
                ))
            if not body.get("has_more"):
                break
            cursor = body.get("next_cursor")
        return records


class HttpNotionReader:
    """Read-side used to detect acknowledgement and completion."""

    def __init__(self, token: str, timeout: int = 30) -> None:
        self._token = token
        self._timeout = timeout

    def _get(self, url: str) -> Any:
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self._token}",
                          "Notion-Version": "2022-06-28"})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            raise ConnectorError(
                f"Notion read {error.code}: "
                f"{error.read().decode(errors='replace')[:200]}") from error
        except Exception as error:
            raise ConnectorError(f"Notion read failed: {error}") from error

    def comments(self, page_id: str) -> list[dict[str, Any]]:
        body = self._get(
            f"https://api.notion.com/v1/comments?block_id={page_id}")
        return [
            {"author_id": (c.get("created_by") or {}).get("id"),
             "created_at": c.get("created_time"),
             "text": "".join(p.get("plain_text", "")
                             for p in c.get("rich_text", []))}
            for c in body.get("results", [])
        ]

    def stage(self, page_id: str) -> str:
        body = self._get(f"https://api.notion.com/v1/pages/{page_id}")
        stage = (body.get("properties", {}).get("Stage") or {}).get("select")
        return (stage or {}).get("name", "") or ""


#: Properties the pipeline writes, and the Notion type each must have.
#: Checked before trusting the write path, so a renamed or retyped column
#: surfaces as a failed preflight instead of a write that silently drops
#: the field it could not set.
REQUIRED_PROJECT_PROPERTIES: dict[str, str] = {
    "Project Name": "title",
    "Permit Number": "rich_text",
    "Stage": "select",
    "Status": "multi_select",
    "Current Owner": "people",
    "Required Next Action": "rich_text",
    "Waiting On": "select",
    "Follow-Up Date": "date",
    "Automation Status": "rich_text",
    "Last Meaningful Activity": "date",
}


def verify_project_schema(properties: dict[str, Any]) -> None:
    """Raise unless the Projects database still matches what we write."""
    missing, mistyped = [], []
    for name, expected in REQUIRED_PROJECT_PROPERTIES.items():
        found = properties.get(name)
        if found is None:
            missing.append(name)
        elif found.get("type") != expected:
            mistyped.append(f"{name} is {found.get('type')}, expected {expected}")
    if missing or mistyped:
        raise ConnectorError(
            "Projects database schema is not compatible with this pipeline. "
            + (f"Missing: {missing}. " if missing else "")
            + (f"Wrong type: {mistyped}." if mistyped else "")
        )


class N8nNotionPort:
    """Writes to Notion through the JAY-OS n8n bridge workflow.

    The Notion connection this repository can reach directly is read-only,
    but n8n already holds a write-capable Notion credential that production
    automations use. Rather than provision a second secret, writes are
    posted to the "JAY-OS Permit Pipeline — Notion Write Bridge" workflow,
    which performs them under that existing credential.

    Success is judged strictly: the bridge answers 200 with `ok:false` for
    some refusals, so a response is only a success when `ok` is true *and*
    Notion echoed an object back. Anything else raises, and the pipeline
    fails closed.
    """

    def __init__(self, webhook_url: str, secret: str, timeout: int = 45) -> None:
        if not webhook_url or not secret:
            raise ConnectorError(
                "n8n Notion bridge needs both JAYOS_NOTION_BRIDGE_URL and "
                "JAYOS_NOTION_BRIDGE_SECRET; refusing to run blind"
            )
        self._url = webhook_url
        self._secret = secret
        self._timeout = timeout

    def _send(self, method: str, url: str, payload: dict[str, Any]) -> Any:
        request = urllib.request.Request(
            self._url, method="POST",
            data=json.dumps({"method": method, "url": url,
                             "payload": payload}).encode(),
            headers={"Content-Type": "application/json",
                     "x-jayos-secret": self._secret},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            raise ConnectorError(
                f"Notion bridge {error.code}: "
                f"{error.read().decode(errors='replace')[:300]}"
            ) from error
        except Exception as error:
            raise ConnectorError(f"Notion bridge unreachable: {error}") from error

        if not isinstance(body, dict) or not body.get("ok"):
            raise ConnectorError(f"Notion bridge refused the write: {body}")
        echoed = body.get("notion")
        if not isinstance(echoed, dict) or not echoed.get("object"):
            raise ConnectorError(
                f"Notion bridge returned no Notion object; treating as failed: "
                f"{str(echoed)[:200]}"
            )
        return echoed

    def check(self, database_id: str | None = None) -> str:
        """Prove the write path really reaches the Projects database.

        `/v1/users/me` only proves the bridge answers and the credential is
        valid; it says nothing about whether that credential can see the
        database we write to, or whether its columns still match. So the
        preflight also retrieves the database schema, verifies the exact
        properties this pipeline sets, and runs a one-row query. All three
        are reads: nothing is created, updated or deleted.
        """
        echoed = self._send("GET", "https://api.notion.com/v1/users/me", {})
        identity = echoed.get("name") or echoed.get("id")
        if not database_id:
            return f"bridge ok, notion bot {identity} (database NOT checked)"

        schema = self._send(
            "GET", f"https://api.notion.com/v1/databases/{database_id}", {})
        verify_project_schema(schema.get("properties", {}))
        listing = self._send(
            "POST",
            f"https://api.notion.com/v1/databases/{database_id}/query",
            {"page_size": 1},
        )
        rows = len(listing.get("results", []))
        return (f"bridge ok, notion bot {identity}, Projects database "
                f"readable, {len(REQUIRED_PROJECT_PROPERTIES)} required "
                f"properties present, query returned {rows} row(s)")

    def update_project(self, page_id: str, properties: dict[str, Any]) -> None:
        self._send("PATCH", f"https://api.notion.com/v1/pages/{page_id}",
                   {"properties": properties})

    def comment(self, page_id: str, text: str,
                mention_user_id: str | None = None) -> None:
        rich_text: list[dict[str, Any]] = []
        if mention_user_id:
            rich_text.append({"type": "mention", "mention": {
                "type": "user", "user": {"object": "user",
                                         "id": mention_user_id}}})
            rich_text.append({"type": "text", "text": {"content": " "}})
        rich_text.append({"type": "text", "text": {"content": text[:2000]}})
        self._send("POST", "https://api.notion.com/v1/comments",
                   {"parent": {"page_id": page_id}, "rich_text": rich_text})
