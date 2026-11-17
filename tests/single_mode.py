"""Test helper for single mode (KNOWLEDGE_FEDERATION_DESIGN.md Section 15).

In single mode ``/`` gives 302 to ``/@{username}/`` of the admin, and only after the first setup gave the admin a USERNAME. A test that reads the workspace of single mode must first complete the setup of the admin.
"""

from __future__ import annotations

import time
from pathlib import Path

from claire.store import db as dbm


def complete_admin_setup(db_file: str | Path, username: str = "adminuser") -> str:
    """Give the admin account the state that the first setup gives: ``active`` with a USERNAME. Return the USERNAME. The database must exist (``create_app`` makes it)."""
    conn = dbm.connect_existing(db_file)
    try:
        conn.execute("UPDATE accounts SET username=?, status='active', activated_at=? WHERE role='admin'", (username, time.time()))
        conn.commit()
    finally:
        conn.close()
    return username
