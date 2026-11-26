"""Development network only: the first setup of one server, as an admin does it (docs/design/CONTRIBUTOR_DEVELOPMENT_ENVIRONMENT_DESIGN.md Section 6.6).

``./cb-manuscript dev devnet up`` runs this script in the toolbox container on the network ``claire-devnet-edge``. The script opens the one-time login link of the server through the gateway, completes ``/setup`` with a USERNAME and a password, and writes the admin credentials of the server in the state file (mode 0600). The tests of the development network read the state file.

Use: ``python bootstrap.py --server a --link https://a.claire.test/?t=... --state /devnet/state.json``
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
from pathlib import Path

import httpx

ADMIN_USERNAME = "keeper"


def _write_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
    os.replace(tmp, path)


def setup(server: str, link: str, state_path: Path) -> dict:
    origin = f"https://{server}.claire.test"
    if not link.startswith(f"{origin}/"):
        raise SystemExit(f"bootstrap: the link is not of {origin}")
    password = secrets.token_urlsafe(18)
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        opened = client.get(link)
        if opened.status_code >= 400:
            raise SystemExit(f"bootstrap: the login link gave {opened.status_code}")
        done = client.post(
            f"{origin}/setup",
            data={"username": ADMIN_USERNAME, "display_name": f"Keeper {server.upper()}", "password": password, "password_confirm": password, "ack": "1"},
            headers={"Origin": origin},
        )
        if done.status_code not in (302, 303):
            # The page gives each reason code in parentheses after its message.
            reasons = sorted(set(re.findall(r"\(([a-z_]+)\)", done.text)))
            raise SystemExit(f"bootstrap: /setup gave {done.status_code}, reasons {reasons}")
        session = client.cookies.get("__Host-claire_session") or client.cookies.get("claire_session")
        if not session:
            raise SystemExit("bootstrap: no session cookie after /setup")
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    state[server] = {"origin": origin, "username": ADMIN_USERNAME, "password": password, "session": session}
    _write_state(state_path, state)
    return state[server]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--server", required=True, choices=["a", "b"])
    parser.add_argument("--link", required=True)
    parser.add_argument("--state", required=True, type=Path)
    args = parser.parse_args(argv)
    made = setup(args.server, args.link, args.state)
    print(f"bootstrap: {made['origin']}: admin {made['username']} is set up")
    return 0


if __name__ == "__main__":
    sys.exit(main())
