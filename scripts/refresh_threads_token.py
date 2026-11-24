"""refresh_threads_token.py - keep the Threads token alive (runs before every Threads agent run in Actions).

Threads long-lived tokens last 60 days and can be refreshed once they are at least 24 hours old. This repo is public
and has no private state store, so the refreshed token goes back into the repo secret THREADS_ACCESS_TOKEN (with a
fine-grained token TFS_REPO_ADMIN_PAT limited to this repo: Secrets and Variables read/write), and the refresh date
into the repo variable THREADS_TOKEN_REFRESHED. The token value is never printed: it is masked and handed to the
next step through $GITHUB_ENV.

Env: THREADS_ACCESS_TOKEN, THREADS_TOKEN_REFRESHED (ISO date, may be empty), GH_TOKEN (the PAT), REPO, GITHUB_ENV.
"""
from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from threads_api import refresh_token  # noqa: E402

EVERY = dt.timedelta(days=6)


def due(last: str, today: dt.date) -> bool:
    try:
        return today - dt.date.fromisoformat(last.strip()[:10]) >= EVERY
    except ValueError:
        return True


def main() -> int:
    token = (os.environ.get("THREADS_ACCESS_TOKEN") or "").strip()
    if not token:
        print("No THREADS_ACCESS_TOKEN: Threads posts fall back to Post for Me.")
        return 0
    today = dt.datetime.now(dt.timezone.utc).date()
    if not due(os.environ.get("THREADS_TOKEN_REFRESHED") or "", today):
        print("Threads token refreshed recently; nothing to do.")
        return 0
    try:
        fresh = refresh_token(token)
    except Exception as e:  # noqa: BLE001 - the current token still works until it expires
        print(f"::warning::Threads token refresh failed ({type(e).__name__}); the current token is used.")
        return 0
    print(f"::add-mask::{fresh}")
    with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as fh:
        fh.write(f"THREADS_ACCESS_TOKEN_FRESH={fresh}\n")
    repo, pat = os.environ.get("REPO", ""), (os.environ.get("GH_TOKEN") or "").strip()
    if not pat:
        print("::warning::TFS_REPO_ADMIN_PAT is not set: the fresh token is used for this run only.")
        return 0
    env = {**os.environ, "GH_TOKEN": pat}
    subprocess.run(["gh", "secret", "set", "THREADS_ACCESS_TOKEN", "-R", repo], input=fresh, text=True, env=env, check=True)
    subprocess.run(["gh", "variable", "set", "THREADS_TOKEN_REFRESHED", "-R", repo, "--body", today.isoformat()],
                   env=env, check=True)
    print(f"Threads token refreshed and saved ({today.isoformat()}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
