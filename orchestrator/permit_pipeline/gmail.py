"""Read jurisdiction mail directly from Gmail.

Uses the Google OAuth credentials the Hermes agent already holds. That
token carries `gmail.readonly` and nothing else, so this module physically
cannot send, label, archive or delete -- the ingest path is read-only by
construction rather than by convention.

Full message bodies matter. Snippets truncate at ~200 characters, and the
sentence that decides an event is routinely past that: Phoenix's "I am
going to place both of these CTR's in a correction status" sits in the
fourth sentence of its message.
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterator

from .ports import ConnectorError

DEFAULT_TOKEN_PATHS = (
    Path("/mnt/c/Users/profe/AppData/Local/hermes/google_token.json"),
    Path.home() / "AppData/Local/hermes/google_token.json",
)
API = "https://gmail.googleapis.com/gmail/v1/users/me"
REQUIRED_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


class GmailReader:
    def __init__(self, token_path: Path | None = None, timeout: int = 30) -> None:
        self._path = self._resolve(token_path)
        self._token: dict[str, Any] = json.loads(self._path.read_text())
        scopes = self._token.get("scopes") or []
        if REQUIRED_SCOPE not in scopes:
            raise ConnectorError(
                f"{self._path} does not carry {REQUIRED_SCOPE}; refusing to "
                f"guess at mail access")
        # Anything beyond reading would be a surprise from an ingest path.
        writey = [s for s in scopes if s.endswith(("gmail.send", "gmail.modify",
                                                   "mail.google.com"))]
        if writey:
            raise ConnectorError(
                f"refusing to use a token with write scopes for ingest: {writey}")
        self._timeout = timeout
        self._access: str | None = None

    @staticmethod
    def _resolve(explicit: Path | None) -> Path:
        for candidate in ([explicit] if explicit else list(DEFAULT_TOKEN_PATHS)):
            if candidate and candidate.exists():
                return candidate
        raise ConnectorError(
            "no Google token found; looked in "
            + ", ".join(str(p) for p in DEFAULT_TOKEN_PATHS))

    # -- auth --------------------------------------------------------------
    def _refresh(self) -> str:
        payload = urllib.parse.urlencode({
            "client_id": self._token["client_id"],
            "client_secret": self._token["client_secret"],
            "refresh_token": self._token["refresh_token"],
            "grant_type": "refresh_token",
        }).encode()
        request = urllib.request.Request(
            self._token.get("token_uri", "https://oauth2.googleapis.com/token"),
            data=payload, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            raise ConnectorError(
                f"Google token refresh {error.code}: "
                f"{error.read().decode(errors='replace')[:200]}") from error
        except Exception as error:
            raise ConnectorError(f"Google token refresh failed: {error}") from error
        access = body.get("access_token")
        if not access:
            raise ConnectorError("token refresh returned no access_token")
        return access

    def _get(self, url: str) -> Any:
        if self._access is None:
            self._access = self._refresh()
        for attempt in (1, 2):
            request = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {self._access}"})
            try:
                with urllib.request.urlopen(request, timeout=self._timeout) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as error:
                if error.code == 401 and attempt == 1:
                    self._access = self._refresh()
                    continue
                if error.code == 429 and attempt == 1:
                    time.sleep(2)
                    continue
                raise ConnectorError(
                    f"Gmail {error.code}: "
                    f"{error.read().decode(errors='replace')[:200]}") from error
            except Exception as error:
                raise ConnectorError(f"Gmail unreachable: {error}") from error
        raise ConnectorError("Gmail request failed after retry")

    # -- reading -----------------------------------------------------------
    def search(self, query: str, limit: int = 200) -> Iterator[str]:
        """Yield message ids matching a Gmail query."""
        page: str | None = None
        yielded = 0
        while yielded < limit:
            params = {"q": query, "maxResults": str(min(100, limit - yielded))}
            if page:
                params["pageToken"] = page
            body = self._get(f"{API}/messages?{urllib.parse.urlencode(params)}")
            for message in body.get("messages", []):
                yield message["id"]
                yielded += 1
            page = body.get("nextPageToken")
            if not page:
                return

    def message(self, message_id: str) -> dict[str, Any]:
        """One message in the shape `sources.from_gmail_message` expects."""
        raw = self._get(f"{API}/messages/{message_id}?format=full")
        headers = {h["name"].lower(): h["value"]
                   for h in raw.get("payload", {}).get("headers", [])}
        return {
            "id": raw.get("id"),
            "threadId": raw.get("threadId"),
            "sender": _address(headers.get("from", "")),
            "subject": headers.get("subject", ""),
            "plaintextBody": _plain_text(raw.get("payload", {})),
            "snippet": raw.get("snippet", ""),
            "date": headers.get("date", ""),
            "internalDate": raw.get("internalDate"),
            "toRecipients": [a.strip() for a in
                             headers.get("to", "").split(",") if a.strip()],
        }

    def fetch(self, query: str, limit: int = 200) -> list[dict[str, Any]]:
        return [self.message(mid) for mid in self.search(query, limit)]


def _address(value: str) -> str:
    """'Cathy Chapman <cathy.chapman@phoenix.gov>' -> the address."""
    if "<" in value and ">" in value:
        return value[value.rindex("<") + 1:value.rindex(">")].strip()
    return value.strip()


def _plain_text(payload: dict[str, Any], depth: int = 0) -> str:
    """Prefer text/plain; fall back to stripped HTML. Recurses into parts."""
    if depth > 8:
        return ""
    mime = payload.get("mimeType", "")
    data = (payload.get("body") or {}).get("data")
    if data and mime == "text/plain":
        return _decode(data)
    collected: list[str] = []
    for part in payload.get("parts", []) or []:
        text = _plain_text(part, depth + 1)
        if text:
            collected.append(text)
    if collected:
        return "\n".join(collected)
    if data and mime == "text/html":
        return _strip_html(_decode(data))
    return ""


def _decode(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _strip_html(html: str) -> str:
    import re

    without_blocks = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", without_blocks)
    text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                .replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'")
                .replace("&quot;", '"'))
    return re.sub(r"[ \t]{2,}", " ", text)
