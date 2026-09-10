"""Source adapters. One module per upstream system, one boundary each.

v1 ships three LIVE adapters against data already on disk, plus declared
boundaries for the connectors that are not wired yet. The stubs exist so the
shape of a connector is decided once, here, rather than improvised later by
whoever happens to add Granola.
"""
from . import (base, permit_folders, portal_csv, projects_json,
              sentinel_contacts)  # noqa: F401

#: Adapters that can run today.
LIVE = ("projects_json", "permit_folders", "portal_csv",
        "sentinel_known_contacts", "sentinel_matched_emails")

#: Declared, not yet implemented. See `base.PendingConnector`.
PLANNED = ("granola", "quo", "gmail", "notion", "google_drive", "roam",
           "agent_outputs", "city_review_comments")
