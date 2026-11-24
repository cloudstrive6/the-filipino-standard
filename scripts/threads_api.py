"""threads_api.py - post a text Thread to @thefilipinostandard straight through Meta's Threads API
(graph.threads.net), using the one TFS Meta app "TFS Publisher" (Bryce, 2026-11-24: free up Post for Me
and keep every TFS pipeline on one Meta app). Ported from the video pipeline's src/tfs/publish/threads.py.

Env: THREADS_ACCESS_TOKEN (long-lived, 60 days; the workflow refreshes it weekly with refresh_token() and writes the
fresh one back to the repo secret, because this repo is public and has no private state store).
"""
from __future__ import annotations

import os
import time

import requests

API = "https://graph.threads.net/v1.0"
TRANSIENT = ("unknown error", "unexpected error", "retry your request", "temporarily unavailable", "please try again")
MAX_CHARS = 500


def _token() -> str:
    tok = (os.environ.get("THREADS_ACCESS_TOKEN") or "").strip()
    if not tok:
        raise RuntimeError("THREADS_ACCESS_TOKEN is not set")
    return tok


def _call(method: str, path: str, **params) -> dict:
    params["access_token"] = _token()
    r = requests.request(method, f"{API}/{path}", timeout=120,
                         data=params if method == "POST" else None, params=params if method == "GET" else None)
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"Threads API {path}: {data['error'].get('message')}")
    return data


def _create(path: str, attempts: int = 4, sleep=time.sleep, **params) -> dict:
    """Create a container, retrying Meta's temporary errors (an unused container just expires)."""
    for attempt in range(attempts):
        try:
            return _call("POST", path, **params)
        except RuntimeError as e:
            if attempt == attempts - 1 or not any(t in str(e).lower() for t in TRANSIENT):
                raise
            sleep(10 * (attempt + 1))
    raise AssertionError("unreachable")


def _wait_ready(container: str, timeout_s: int = 300, sleep=time.sleep) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = _call("GET", container, fields="status,error_message")
        if status.get("status") in ("FINISHED", "PUBLISHED"):
            return
        if status.get("status") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Threads container {container} failed: {status.get('error_message')}")
        sleep(3)
    raise TimeoutError(f"Threads container {container} not ready after {timeout_s}s")


def post_text(text: str, sleep=time.sleep) -> dict:
    """Publish one text Thread now. Returns {"post_id", "post_url"}."""
    if len(text) > MAX_CHARS:
        raise ValueError(f"Threads text is {len(text)} chars (max {MAX_CHARS})")
    user = (os.environ.get("THREADS_USER_ID") or "").strip() or "me"
    container = _create(f"{user}/threads", media_type="TEXT", text=text, sleep=sleep)["id"]
    _wait_ready(container, sleep=sleep)
    media_id = _call("POST", f"{user}/threads_publish", creation_id=container)["id"]
    try:
        url = _call("GET", media_id, fields="permalink").get("permalink", "")
    except Exception:  # noqa: BLE001 - the post is up; a missing link is not a failure
        url = ""
    return {"post_id": media_id, "post_url": url}


def whoami() -> str:
    """For checks: the username the token posts as."""
    return _call("GET", "me", fields="id,username").get("username", "")


def refresh_token(token: str) -> str:
    """Exchange a long-lived token (at least 24 h old) for a fresh 60-day one. Returns the new token."""
    r = requests.get("https://graph.threads.net/refresh_access_token", timeout=30,
                     params={"grant_type": "th_refresh_token", "access_token": token})
    data = r.json()
    if "access_token" not in data:
        raise RuntimeError(f"Threads token refresh failed: {data.get('error', {}).get('message', data)}")
    return data["access_token"]
