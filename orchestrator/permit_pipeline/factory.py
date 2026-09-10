"""Assemble production ports from the configured JAY-OS environment.

The Notion connection this repository can reach directly is read-only, so
writes prefer the n8n bridge, which performs them under the write-capable
credential production automations already use. If neither path is
configured this raises, because a pipeline that cannot write must stop
rather than report success.
"""
from __future__ import annotations

from . import config
from .ports import ConnectorError, HttpNotionPort, N8nNotionPort


def notion_writer():
    """The best available production Notion writer, or an explicit failure."""
    url = config.get("JAYOS_NOTION_BRIDGE_URL")
    secret = config.get("JAYOS_NOTION_BRIDGE_SECRET")
    if url and secret:
        return N8nNotionPort(url, secret)

    token = config.get("NOTION_API_KEY")
    if token:
        return HttpNotionPort(token)

    raise ConnectorError(
        "No Notion write path is configured. Set JAYOS_NOTION_BRIDGE_URL and "
        "JAYOS_NOTION_BRIDGE_SECRET (the n8n bridge), or grant the direct "
        "NOTION_API_KEY connection edit access. Refusing to simulate writes."
    )


def describe_notion_writer() -> str:
    if config.get("JAYOS_NOTION_BRIDGE_URL"):
        return f"n8n bridge ({config.source_of('JAYOS_NOTION_BRIDGE_URL')})"
    if config.get("NOTION_API_KEY"):
        return f"direct Notion API ({config.source_of('NOTION_API_KEY')})"
    return "none configured"
