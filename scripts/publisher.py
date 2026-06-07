"""
publisher.py — The Filipino Standard publishing worker.

Monitors ClickUp list 901614911598 for approved/scheduled tasks and publishes
them to Facebook, Instagram, and Threads via the Post for Me API. Reddit is
left for manual posting (the script just leaves a comment on the task).

DRY RUN IS THE DEFAULT. Live publishing requires PUBLISHER_LIVE_MODE=true in .env.

Usage:
    py publisher.py --monitor            # 15-minute scan cycle
    py publisher.py --task 86d2z9gz3     # Publish one specific task

Spec / blueprint:
    Z:\\Business Empire\\The Filipino Standard\\skills\\publisher\\SKILL.md

Environment (read from .env at project root):
    POSTFORME_API_KEY              — required
    POSTFORME_PROJECT_ID           — required
    POSTFORME_FB_PAGE_ID           — required for Facebook publishing
    POSTFORME_IG_ACCOUNT_ID        — required for Instagram publishing
    POSTFORME_THREADS_ACCOUNT_ID   — required for Threads publishing
    CLICKUP_API_TOKEN              — required
    PUBLISHER_LIVE_MODE            — "true" to enable live publishing; default dry run
    POSTFORME_BASE_URL             — optional; default https://api.postforme.dev
"""

from __future__ import annotations

# Use the OS trust store (Windows certificate store) so AV / firewall-intercepted
# TLS chains validate correctly. Must run before any TLS-using import is exercised.
import truststore
truststore.inject_into_ssl()

import argparse
import datetime as dt
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

# Single source of truth for caption validation. The same validate_caption that
# runs at task-creation time (scripts/create_task.py) and during reconciliation
# (scripts/status_poller.py) ALSO runs immediately before publish here. This
# closes the poller/publisher race window: a caption that was clean at creation
# but got edited into a brief-leak afterwards is caught at the broadcast door,
# not after going live.
try:
    from create_task import validate_caption as _validate_caption  # type: ignore
except ImportError:
    _validate_caption = None  # graceful degrade; logged in process_publishable_task

# ---------------------------------------------------------------------------
# Project constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
LOGS_DIR = PROJECT_ROOT / "logs"
PUBLISHED_DIR = PROJECT_ROOT / "published"
FAILED_DIR = PROJECT_ROOT / "failed"

# Hardcoded — refuse to write to any other list
CLICKUP_LIST_ID = "901614911598"

# Custom field IDs
FIELD_SCHEDULED_PUBLISH = "8a89f1c0-f964-4281-bbe8-82f2bc187ca0"
FIELD_FINAL_CAPTION = "f9e3e3eb-de98-406a-a716-84760d13457a"
FIELD_IMAGE_URL = "34e674b6-bfb7-4c71-80f3-35065c84f1a3"
FIELD_PLATFORM = "ef8cfddd-c950-40b8-95ca-6da001c6ac50"
FIELD_POSTFORME_POST_ID = "2ce7e830-2168-44c9-9eff-a54d3055d510"
FIELD_POSTED_URL_FACEBOOK = "3a5a9be9-100d-4c93-966c-6c704671a1c6"
FIELD_POSTED_URL_INSTAGRAM = "bf5d5dd3-6c52-406a-92f7-831908138106"
FIELD_POSTED_URL_THREADS = "7b44e00d-ba41-4b39-aeea-e9b07b80c263"
FIELD_POSTED_URL_REDDIT = "f010563d-1658-4725-bd80-fca2b3410fe6"
# Threads-specific caption (under 500 chars). When present and Platform includes
# Threads alongside other platforms, the publisher uses this for the Threads
# post instead of the (longer) task description. See process_publishable_task.
FIELD_THREADS_CAPTION = "0f7e069d-83ab-4037-ab3a-a76720a3410d"

# Platform label option IDs (used to translate Platform field values → names)
PLATFORM_OPTION_FACEBOOK = "673cfb92-15e7-4315-9bbf-94db2baffa08"
PLATFORM_OPTION_REDDIT = "5bd32f20-3976-4c9e-931b-6f1d562c8c58"
PLATFORM_OPTION_THREADS = "225e6544-1287-44b7-a019-7f3b1fdc31e1"
PLATFORM_OPTION_INSTAGRAM = "32e72ad5-83d0-4a92-87f6-b8c8b4990a44"

# Reverse lookup
PLATFORM_OPTION_TO_NAME = {
    PLATFORM_OPTION_FACEBOOK: "facebook",
    PLATFORM_OPTION_REDDIT: "reddit",
    PLATFORM_OPTION_THREADS: "threads",
    PLATFORM_OPTION_INSTAGRAM: "instagram",
}

AUTOMATED_PLATFORMS = {"facebook", "instagram", "threads"}
MANUAL_PLATFORMS = {"reddit"}
# Platforms that REQUIRE an image — skip the platform with a comment if Image URL
# is empty. Other automated platforms (Threads) treat the image as optional and
# publish text-only when no image is provided.
IMAGE_REQUIRED_PLATFORMS = {"facebook", "instagram"}

# Per-platform → which env var holds the social account ID and which field gets the URL
PLATFORM_CONFIG: dict[str, dict[str, str]] = {
    "facebook": {
        "env": "POSTFORME_FB_PAGE_ID",
        "posted_url_field": FIELD_POSTED_URL_FACEBOOK,
    },
    "instagram": {
        "env": "POSTFORME_IG_ACCOUNT_ID",
        "posted_url_field": FIELD_POSTED_URL_INSTAGRAM,
    },
    "threads": {
        "env": "POSTFORME_THREADS_ACCOUNT_ID",
        "posted_url_field": FIELD_POSTED_URL_THREADS,
    },
}

# ClickUp status names — must match the list's configured statuses
STATUS_DRAFT = "draft"
STATUS_APPROVED = "approved"
STATUS_SCHEDULED = "scheduled"
STATUS_PUBLISHED = "published"
STATUS_NEEDS_REVISION = "needs-revision"
STATUS_COMPLETE = "complete"

PHT = ZoneInfo("Asia/Manila")

# Safety / behavior constants from Publisher SKILL.md
MAX_PUBLISHES_PER_RUN = 10
APPROVED_NEAR_BUFFER_MINUTES = 30  # APPROVED + sched within this window → publishable
RETRY_BACKOFF_SECONDS = [60, 120, 240]  # 1 min, 2 min, 4 min — 3 attempts total

# --- Publisher safety guards -------------------------------------------------
#
# Any task whose Scheduled Publish lands in this year or later is treated as a
# hard-blocked far-future placeholder, regardless of status. The 2027 anchor is
# the convention used to "park" draft/in-progress tasks; tasks not yet ready to
# go live keep this date and would otherwise sit in SCHEDULED indefinitely. By
# making the placeholder year an explicit hard publish block, we ensure that
# a status flip alone is never enough to broadcast — the date must also be
# moved to a real near-term value. TUNABLE.
FAR_FUTURE_YEAR_THRESHOLD = 2027

# Staleness floor: any task whose Scheduled Publish is more than this many
# hours in the past is NOT cleared to publish. The publisher will refuse the
# broadcast and quarantine the task to needs-revision with a comment naming
# how far in the past the schedule is. Prevents reactive posts from
# auto-resurrecting hours later when a delayed worker catches up. TUNABLE.
STALE_PUBLISH_HOURS = 6

# Hardcoded last-line defense: even if config/locked_tasks.json disappears or
# the on-disk lock list is empty, these task IDs must never reach the publish
# call. Currently: 86d30n0ne (the canonical constitutional_quote benchmark).
ABSOLUTELY_NEVER_PUBLISH = frozenset({"86d30n0ne"})

# TEST-task detection. A task is treated as a test task if ANY of the
# following hold (all case-insensitive):
#   1. Its name contains the standalone token TEST as a whole word
#      (\bTEST\b). This is the canonical signal. It deliberately does NOT
#      match PROTEST, CONTEST, LATEST, GREATEST, TESTING, TESTER — none of
#      those contain TEST as a whole word.
#   2. Its name begins with the dated test-convention prefix
#      "YYYY-MM-DD TEST ..." or "YYYY-MM-DD <pillar> TEST ..." (a redundant
#      but documentary signal — covered by rule 1, kept explicit so the
#      convention is enforced if rule 1 ever changes).
#   3. It carries a "test" tag in the ClickUp tags array.
# Tag check is independent of the name regex — a task tagged "test" but
# named like a real post is still a test task.
TEST_TOKEN_RE = re.compile(r"\bTEST\b", re.IGNORECASE)
DATED_TEST_PREFIX_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}\s+(?:[A-Za-z][A-Za-z\-']*\s+)*TEST\b",
    re.IGNORECASE,
)
TEST_TAG_NAME = "test"

# Auto-completion: PUBLISHED tasks older than this become COMPLETE
COMPLETE_AGE_DAYS = 7
MAX_COMPLETIONS_PER_RUN = 20
# Custom log level for completion events — sits between INFO (20) and WARNING (30)
COMPLETE_LEVEL_NUM = 25

# HTTP status classifications
PERMANENT_STATUSES = {400, 401, 403, 422}
TRANSIENT_STATUSES = {429, 500, 502, 503, 504}

# Default Post for Me base — includes the /v1 path prefix. All endpoint paths
# in PostForMeClient are relative to this. Override via POSTFORME_BASE_URL in .env.
DEFAULT_POSTFORME_BASE = "https://api.postforme.dev/v1"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logger() -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    today_pht = dt.datetime.now(PHT).strftime("%Y-%m-%d")
    log_path = LOGS_DIR / f"publisher-{today_pht}.log"

    # Register the custom COMPLETE level so [COMPLETE] shows in the level slot
    logging.addLevelName(COMPLETE_LEVEL_NUM, "COMPLETE")

    # Force stdout to UTF-8 so non-ASCII characters in log messages (em dashes,
    # arrows, Filipino diacritics in task names) don't raise UnicodeEncodeError
    # on Windows consoles, which default to cp1252.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    logger = logging.getLogger("publisher")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "[%(asctime)s PHT] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.Formatter.converter = lambda *_: dt.datetime.now(PHT).timetuple()

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    return logger


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class HaltRun(Exception):
    """Raised on HTTP 401 — bad credentials affect every task; stop the run."""


class PermanentPublishError(Exception):
    """Don't retry — task → NEEDS-REVISION."""

    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(message)
        self.status = status


class TransientPublishError(Exception):
    """Retry with exponential backoff."""

    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# ClickUp REST client
# ---------------------------------------------------------------------------


class ClickUpClient:
    BASE = "https://api.clickup.com/api/v2"

    def __init__(self, api_token: str, logger: logging.Logger) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": api_token, "Content-Type": "application/json"}
        )
        self.log = logger

    def get_task(self, task_id: str) -> dict[str, Any]:
        r = self.session.get(
            f"{self.BASE}/task/{task_id}",
            params={"custom_fields": "true", "include_subtasks": "false"},
            timeout=30,
        )
        r.raise_for_status()
        task = r.json()
        list_id = (task.get("list") or {}).get("id")
        if list_id != CLICKUP_LIST_ID:
            raise RuntimeError(
                f"Task {task_id} is on list {list_id}, not {CLICKUP_LIST_ID}. Refusing."
            )
        return task

    def list_tasks(self) -> list[dict[str, Any]]:
        all_tasks: list[dict[str, Any]] = []
        page = 0
        while True:
            r = self.session.get(
                f"{self.BASE}/list/{CLICKUP_LIST_ID}/task",
                params={
                    "archived": "false",
                    "subtasks": "false",
                    "include_closed": "true",  # we want PUBLISHED etc.
                    "page": page,
                },
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            tasks = data.get("tasks", [])
            if not tasks:
                break
            all_tasks.extend(tasks)
            page += 1
            if len(tasks) < 100:
                break
        return all_tasks

    def set_custom_field(self, task_id: str, field_id: str, value: Any) -> None:
        r = self.session.post(
            f"{self.BASE}/task/{task_id}/field/{field_id}",
            json={"value": value},
            timeout=30,
        )
        r.raise_for_status()

    def set_status(self, task_id: str, status: str) -> None:
        r = self.session.put(
            f"{self.BASE}/task/{task_id}",
            json={"status": status},
            timeout=30,
        )
        r.raise_for_status()

    def add_comment(self, task_id: str, comment_text: str) -> None:
        r = self.session.post(
            f"{self.BASE}/task/{task_id}/comment",
            json={"comment_text": comment_text, "notify_all": False},
            timeout=30,
        )
        r.raise_for_status()


# ---------------------------------------------------------------------------
# Post for Me REST client
# ---------------------------------------------------------------------------


class PostForMeClient:
    """Post for Me API wrapper. Endpoint shape matches the reference
    HelloNorg implementation, which is proven against the live API.

    All endpoint paths are RELATIVE to self.base_url, which already includes
    the /v1 prefix (e.g., "https://api.postforme.dev/v1"). Do not prepend /v1
    to PATH_* constants.
    """

    PATH_MEDIA_UPLOAD_URL = "/media/create-upload-url"
    PATH_SOCIAL_POSTS = "/social-posts"
    PATH_SOCIAL_ACCOUNTS = "/social-accounts"

    def __init__(
        self,
        api_key: str,
        project_id: str,
        base_url: str,
        logger: logging.Logger,
    ) -> None:
        self.api_key = api_key
        self.project_id = project_id
        self.base_url = base_url.rstrip("/")
        self.log = logger
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )
        # Cache for external_id -> internal_id resolution. Populated on first call
        # to get_social_accounts(); reused for the lifetime of the client instance.
        self._accounts_cache: list[dict[str, Any]] | None = None

    def _wrap_http_error(self, r: requests.Response, context: str) -> Exception:
        """Convert an HTTP error response into the right exception class."""
        status = r.status_code
        try:
            body = r.json()
        except Exception:
            body = r.text[:300]
        msg = f"{context}: HTTP {status} - {body}"
        if status in PERMANENT_STATUSES:
            if status == 401:
                return HaltRun(msg)
            return PermanentPublishError(status, msg)
        if status in TRANSIENT_STATUSES:
            return TransientPublishError(status, msg)
        # Unknown status -> treat as permanent (safe default)
        return PermanentPublishError(status, msg)

    # ---- social accounts -------------------------------------------------

    def get_social_accounts(self) -> list[dict[str, Any]]:
        """GET /v1/social-accounts. Returns a list of account dicts.

        Caches the result for the lifetime of the client. Post for Me's response
        shape varies between deployments — may be a raw list or wrapped in
        {"data": [...]} / {"social_accounts": [...]} / similar. We handle both.
        """
        if self._accounts_cache is not None:
            return self._accounts_cache
        try:
            r = self.session.get(
                f"{self.base_url}{self.PATH_SOCIAL_ACCOUNTS}",
                timeout=30,
            )
        except requests.RequestException as e:
            raise TransientPublishError(None, f"Network error listing social accounts: {e}")
        if r.status_code not in {200, 201}:
            raise self._wrap_http_error(r, "get_social_accounts")
        data = r.json()
        if isinstance(data, list):
            accounts = data
        elif isinstance(data, dict):
            accounts = []
            for key in ("data", "social_accounts", "accounts", "items"):
                if key in data and isinstance(data[key], list):
                    accounts = data[key]
                    break
        else:
            accounts = []
        self._accounts_cache = accounts
        return accounts

    def resolve_external_id(
        self,
        external_id: str,
        platform_hint: str | None = None,
    ) -> str | None:
        """Translate a user-facing external_id (e.g. "tfs-facebook") into a
        Post for Me internal account ID (e.g. "spc_xxx").

        Post for Me projects can contain MANY accounts sharing the same
        external_id — typically because a project is shared across brands or
        because previously-linked accounts left a trail of disconnected stubs
        with the same external_id. The first match is rarely the right one.

        This resolver enforces three filters:
            1. external_id matches exactly
            2. platform matches (if platform_hint is provided)
            3. status == "connected" AND access_token is non-empty

        Returns the first account that passes all filters, or None if none do.
        If the value already looks like an internal ID (spc_/sa_/acct_ prefix),
        returns it as-is — callers can pass internal IDs directly without a lookup.

        On a non-match, logs a diagnostic explaining which filter rejected what.
        """
        if not external_id:
            return None
        if external_id.startswith(("spc_", "sa_", "acct_")):
            return external_id

        accounts = self.get_social_accounts()
        ext_matches = 0
        ext_and_platform_matches = 0

        for account in accounts:
            ext = account.get("external_id") or account.get("externalId")
            if ext != external_id:
                continue
            ext_matches += 1

            if platform_hint:
                acct_platform = (account.get("platform") or "").lower()
                if acct_platform != platform_hint.lower():
                    continue
            ext_and_platform_matches += 1

            status = (account.get("status") or "").lower()
            if status != "connected":
                continue
            if not account.get("access_token"):
                continue

            internal = account.get("id")
            if internal:
                self.log.info(
                    "Resolved external_id %r (platform=%s) -> %s \"%s\" [connected]",
                    external_id, account.get("platform"), internal, account.get("username"),
                )
                return internal

        # Diagnostic — tell the caller exactly which filter rejected what
        if ext_matches == 0:
            self.log.warning(
                "No Post for Me social account has external_id=%r", external_id,
            )
        elif platform_hint and ext_and_platform_matches == 0:
            self.log.warning(
                "Found %d account(s) with external_id=%r but none on platform %r",
                ext_matches, external_id, platform_hint,
            )
        else:
            scope = (
                f"external_id={external_id!r} on platform {platform_hint!r}"
                if platform_hint else f"external_id={external_id!r}"
            )
            self.log.warning(
                "Found %d account(s) matching %s but none are status=connected with a valid access_token",
                ext_and_platform_matches, scope,
            )
        return None

    # ---- media upload ----------------------------------------------------

    @staticmethod
    def _content_type_for(image_path: Path) -> str:
        ext = image_path.suffix.lower()
        if ext == ".png":
            return "image/png"
        if ext in (".jpg", ".jpeg"):
            return "image/jpeg"
        raise PermanentPublishError(None, f"Unsupported image extension: {ext}")

    def upload_image(self, image_path: Path) -> str:
        """Two-step signed-URL upload:

            1. POST /v1/media/create-upload-url with {"content_type": "image/png"}
               -> returns {"upload_url": <signed>, "media_url": <final>}
            2. PUT bytes to upload_url with Content-Type header
               (NO Authorization header — the upload_url is pre-signed)

        Returns: the media_url string to use in /social-posts media[].url
        """
        if not image_path.exists():
            raise PermanentPublishError(None, f"Image file not found: {image_path}")
        content_type = self._content_type_for(image_path)

        # Step 1: request signed upload URL
        try:
            r = self.session.post(
                f"{self.base_url}{self.PATH_MEDIA_UPLOAD_URL}",
                json={"content_type": content_type},
                timeout=30,
            )
        except requests.RequestException as e:
            raise TransientPublishError(None, f"Network error during create-upload-url: {e}")
        if r.status_code not in {200, 201}:
            raise self._wrap_http_error(r, "create-upload-url")
        data = r.json()
        upload_url = data.get("upload_url") or data.get("uploadUrl")
        media_url = data.get("media_url") or data.get("mediaUrl")
        if not upload_url or not media_url:
            raise PermanentPublishError(
                None, f"create-upload-url response missing upload_url/media_url: {data}"
            )

        # Step 2: PUT bytes to the signed URL. No auth header — the signed URL
        # already carries the credential. Streaming the file handle keeps memory
        # bounded for large images.
        try:
            with image_path.open("rb") as fh:
                put = requests.put(
                    upload_url,
                    data=fh,
                    headers={"Content-Type": content_type},
                    timeout=120,
                )
        except requests.RequestException as e:
            raise TransientPublishError(None, f"Network error during signed-URL PUT: {e}")
        if put.status_code not in {200, 201, 204}:
            raise self._wrap_http_error(put, "Signed-URL PUT")

        return media_url

    # ---- post creation ---------------------------------------------------

    def create_post(
        self,
        *,
        caption: str,
        social_accounts: list[str],
        media_url: str | None = None,
        scheduled_at: str | None = None,
        external_id: str | None = None,
    ) -> dict[str, Any]:
        """POST /v1/social-posts.

        Args:
            caption: post text (same across all platforms in this call)
            social_accounts: list of Post for Me INTERNAL account IDs (e.g. ["spc_xxx"]).
                Pass one ID per platform; the API supports multi-platform fanout.
            media_url: the media_url returned by upload_image(), OR None for a
                text-only post (Threads supports text-only; FB/IG do not — the
                caller is responsible for not invoking text-only against FB/IG).
            scheduled_at: optional ISO 8601 UTC string (e.g. "2026-05-14T12:00:00Z").
                If omitted, Post for Me publishes immediately.
            external_id: optional idempotency key. Recommended format:
                "{task_id}-{platform}" — prevents duplicate posts on retries.

        Returns: {"post_id": ..., "post_url": ..., "raw": <full response dict>}
        """
        body: dict[str, Any] = {
            "caption": caption,
            "social_accounts": list(social_accounts),
        }
        if media_url:
            body["media"] = [{"url": media_url}]
        # When media_url is None we OMIT the media key entirely. This is the
        # text-only Threads path. If Post for Me's API requires the key to be
        # present (e.g. as an empty array), change this to body["media"] = [].
        if scheduled_at:
            body["scheduled_at"] = scheduled_at
        if external_id:
            body["external_id"] = external_id

        try:
            r = self.session.post(
                f"{self.base_url}{self.PATH_SOCIAL_POSTS}",
                json=body,
                timeout=60,
            )
        except requests.RequestException as e:
            raise TransientPublishError(None, f"Network error during post creation: {e}")
        if r.status_code not in {200, 201}:
            raise self._wrap_http_error(r, "Post creation")
        data = r.json()
        return {
            "post_id": data.get("id") or data.get("post_id"),
            "post_url": data.get("post_url") or data.get("url"),
            "raw": data,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def find_field(task: dict[str, Any], field_id: str) -> dict[str, Any] | None:
    for f in task.get("custom_fields", []) or []:
        if f.get("id") == field_id:
            return f
    return None


def field_value(task: dict[str, Any], field_id: str) -> Any:
    f = find_field(task, field_id)
    return None if not f else f.get("value")


def status_name(task: dict[str, Any]) -> str:
    return ((task.get("status") or {}).get("status") or "").lower()


def task_platforms(task: dict[str, Any]) -> list[str]:
    """Return the list of platform names targeted by the task."""
    raw = field_value(task, FIELD_PLATFORM) or []
    names: list[str] = []
    for entry in raw:
        # ClickUp labels are stored as either option IDs or objects
        opt_id = entry.get("id") if isinstance(entry, dict) else entry
        name = PLATFORM_OPTION_TO_NAME.get(opt_id)
        if name:
            names.append(name)
    return names


def scheduled_publish_pht(task: dict[str, Any]) -> dt.datetime | None:
    sp = field_value(task, FIELD_SCHEDULED_PUBLISH)
    if not sp:
        return None
    try:
        ms = int(sp)
        return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).astimezone(PHT)
    except (ValueError, TypeError):
        return None


def already_published_on(task: dict[str, Any], platform: str) -> bool:
    field_id = PLATFORM_CONFIG.get(platform, {}).get("posted_url_field")
    if not field_id:
        return False
    v = field_value(task, field_id)
    return bool(v and str(v).strip())


def image_url_to_local_path(value: str) -> Path | None:
    """Convert a file:// or raw-path Image URL value to a local Path.

    The current generator writes a hosted https URL to FIELD_IMAGE_URL, but
    older tasks may still carry a file:// URI from before that change.
    https URLs are handled by resolve_image_to_local_path() (which downloads
    to a temp file); this helper only resolves the local-path forms.
    """
    from urllib.parse import urlparse
    from urllib.request import url2pathname

    if not value:
        return None
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme == "file":
        return Path(url2pathname(parsed.path))
    if parsed.scheme in ("http", "https"):
        return None  # caller uses download_to_temp instead
    # No scheme — treat as a raw local path (back-compat)
    return Path(value)


def _ext_for_content_type(ct: str) -> str:
    ct = (ct or "").lower().split(";")[0].strip()
    if ct == "image/png":
        return ".png"
    if ct in ("image/jpeg", "image/jpg"):
        return ".jpg"
    return ".png"  # safe default — PostForMeClient.upload_image looks at suffix


def download_to_temp(
    url: str,
    logger: logging.Logger,
    task_id: str,
) -> Path | None:
    """Download an https image URL to a temp file and return the Path.
    Returns None on failure (network error, non-200, empty body).

    Used for the standard hosted-URL path (ClickUp attachments) and for the
    attachment-list fallback. The temp file is left for the caller to use
    and clean up; it lives in the OS temp dir with a deterministic prefix
    so it's easy to find for debugging.
    """
    import tempfile
    try:
        r = requests.get(url, timeout=60, stream=True)
    except requests.RequestException as e:
        logger.warning('Task %s — image download network error: %s', task_id, e)
        return None
    if r.status_code != 200:
        logger.warning(
            'Task %s — image download non-200: HTTP %d (url=%s)',
            task_id, r.status_code, url[:120],
        )
        return None
    ext = _ext_for_content_type(r.headers.get("content-type", ""))
    fd, tmp_path_str = tempfile.mkstemp(prefix="tfs-publisher-img-", suffix=ext)
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "wb") as fh:
            for chunk in r.iter_content(chunk_size=65536):
                if chunk:
                    fh.write(chunk)
    except Exception as e:
        logger.warning('Task %s — image download write error: %s', task_id, e)
        try:
            tmp_path.unlink()
        except OSError:
            pass
        return None
    if tmp_path.stat().st_size == 0:
        logger.warning('Task %s — downloaded image is empty', task_id)
        try:
            tmp_path.unlink()
        except OSError:
            pass
        return None
    return tmp_path


def _latest_image_attachment_url(task: dict[str, Any]) -> str | None:
    """Find the most recent image attachment on the task. Returns its hosted
    https URL, or None if no image attachment is present. Sorts by `date`
    descending so the latest upload wins, matching the rule that the most
    recent generation is the live one."""
    attachments = task.get("attachments") or []
    candidates: list[dict[str, Any]] = []
    for a in attachments:
        if not isinstance(a, dict):
            continue
        ext = (a.get("extension") or "").lower()
        title = (a.get("title") or "").lower()
        url = a.get("url") or a.get("url_w_query") or ""
        if not url:
            continue
        is_image = ext in {"png", "jpg", "jpeg", "webp"} or any(
            title.endswith(s) for s in (".png", ".jpg", ".jpeg", ".webp")
        )
        if is_image:
            candidates.append(a)
    if not candidates:
        return None

    def _date_key(a: dict[str, Any]) -> int:
        try:
            return int(a.get("date") or 0)
        except (TypeError, ValueError):
            return 0
    candidates.sort(key=_date_key, reverse=True)
    return candidates[0].get("url") or candidates[0].get("url_w_query")


def resolve_image_to_local_path(
    image_url: str | None,
    task: dict[str, Any],
    logger: logging.Logger,
    task_id: str,
) -> tuple[Path | None, str]:
    """Resolve a task's image to a local Path the publisher can upload.

    Order (one fallback, not a system):
      1. FIELD_IMAGE_URL is a hosted https URL → download to temp.
      2. FIELD_IMAGE_URL is a file:// URI or local path → use as-is if it exists.
      3. FIELD_IMAGE_URL empty/unfetchable → find the latest image attachment
         on the task and download THAT.

    Returns (path_or_None, source_label) where source_label is a short string
    describing which branch succeeded ("image-url-https", "image-url-file",
    "attachment-fallback", or "none"). Caller is responsible for deleting any
    temp file when done (tmp files start with "tfs-publisher-img-").
    """
    val = (image_url or "").strip()
    if val:
        from urllib.parse import urlparse
        scheme = urlparse(val).scheme
        if scheme in ("http", "https"):
            tmp = download_to_temp(val, logger, task_id)
            if tmp is not None:
                return tmp, "image-url-https"
            logger.info(
                'Task %s — FIELD_IMAGE_URL https download failed; trying '
                'attachment fallback',
                task_id,
            )
        else:
            local = image_url_to_local_path(val)
            if local is not None and local.is_absolute() and local.exists():
                return local, "image-url-file"
            logger.info(
                'Task %s — FIELD_IMAGE_URL local path not usable (%s); '
                'trying attachment fallback',
                task_id, val,
            )

    # One fallback: latest image attachment on the task
    fb_url = _latest_image_attachment_url(task)
    if fb_url:
        tmp = download_to_temp(fb_url, logger, task_id)
        if tmp is not None:
            logger.info(
                'Task %s — using attachment fallback (latest image '
                'attachment, url=%s)', task_id, fb_url[:120],
            )
            return tmp, "attachment-fallback"
        logger.warning(
            'Task %s — attachment fallback download failed (url=%s)',
            task_id, fb_url[:120],
        )
    return None, "none"


def caption_sanity_check(caption: str, logger: logging.Logger, task_id: str) -> bool:
    """Catch obvious off-brand markers before publishing. Returns True if it looks OK.

    Note: this is a soft secondary check. The hard publish guards live in
    check_publish_guards() and are run BEFORE this is reached. This function
    is left in place for legacy logging of starts-with-generic-opener cases.
    """
    issues: list[str] = []
    if "—" in caption:
        issues.append("contains em dash (—)")
    if caption.lower().startswith(("in today's world", "have you ever wondered", "it is no secret")):
        issues.append("starts with a generic opener")
    if issues:
        logger.warning(
            "Task %s caption sanity check failed: %s", task_id, "; ".join(issues)
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Publisher safety guards
# ---------------------------------------------------------------------------
#
# These guards run BEFORE any publish call. A task must clear every guard
# before its caption can be broadcast to the live brand. The guards fire in
# this fixed order:
#
#   GUARD 0 — LOCKED_TASK
#       Defense in depth. The task ID is in config/locked_tasks.json OR in
#       the hardcoded ABSOLUTELY_NEVER_PUBLISH set. Skip silently; do NOT
#       quarantine (we never want to mutate a locked task).
#
#   GUARD 1 — NOT_CLEARED_TO_PUBLISH (the cleared-to-publish gate)
#       Status must be SCHEDULED with Scheduled Publish <= now, OR APPROVED
#       with Scheduled Publish within APPROVED_NEAR_BUFFER_MINUTES of now.
#       AND Scheduled Publish must be a real near-term PHT datetime
#       (year < FAR_FUTURE_YEAR_THRESHOLD = 2027). Far-future placeholders
#       are a HARD publish block; not a quarantine, just a skip.
#
#   GUARD 2 — TEST_TASK_BROADCAST_BLOCKED
#       Task name contains "TEST" (case-insensitive). Never publish to the
#       live brand under any status. Quarantine (set needs-revision + comment).
#       Directly prevents the 86d30n0ne incident class.
#
#   GUARD 3 — EMPTY_PLATFORM_FIELD
#       The Platform custom field (FIELD_PLATFORM) is empty. We refuse to
#       fall back to tags or to a "default to all three" silent broadcast.
#       Quarantine.
#
#   GUARD 4 — caption violations (BRIEF_MARKER_PRESENT, EM_DASH_PRESENT, ...)
#       The text that WILL be sent as the caption (the task description) is
#       run through the same validate_caption used at task creation and in
#       the status_poller reconcile hook. Any violation = quarantine.
#       Closes the race window where a task is approved with a clean caption
#       and then edited into a brief-leak before publish.


class PublishBlock:
    """One reason a task cannot publish. Returned by check_publish_guards().

    Attributes:
        rule: short stable identifier (e.g. "BRIEF_MARKER_PRESENT").
        detail: human-readable explanation; safe to include in a ClickUp comment.
        quarantine: if True, the task should be moved to needs-revision with a
            comment naming the rule. If False, the task is just SKIPPED — used
            for normal holds (not-yet-due, far-future placeholder, locked task)
            that aren't malformed and shouldn't be auto-flipped.
    """

    __slots__ = ("rule", "detail", "quarantine")

    def __init__(self, rule: str, detail: str, *, quarantine: bool) -> None:
        self.rule = rule
        self.detail = detail
        self.quarantine = quarantine

    def __repr__(self) -> str:  # pragma: no cover — debug aid only
        return f"PublishBlock(rule={self.rule!r}, quarantine={self.quarantine})"


def _load_locked_task_ids() -> set[str]:
    """Read config/locked_tasks.json (read-only) and return the locked task IDs.

    Same shape as the file consumed by generate_image.py + status_poller.py —
    a top-level "locked_tasks" array of objects with a "task_id" field. If the
    file is missing or malformed, return an empty set; ABSOLUTELY_NEVER_PUBLISH
    is always merged in as a hardcoded last-line defense.
    """
    out: set[str] = set(ABSOLUTELY_NEVER_PUBLISH)
    p = PROJECT_ROOT / "config" / "locked_tasks.json"
    if not p.exists():
        return out
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return out
    for e in (data.get("locked_tasks") or []):
        tid = (e or {}).get("task_id")
        if tid:
            out.add(tid)
    return out


def is_cleared_to_publish(task: dict[str, Any]) -> tuple[bool, str, str]:
    """The cleared-to-publish condition, expressed as a single function.

    A task is cleared IFF ALL of the following hold:

      1. Its status is SCHEDULED with Scheduled Publish <= now, OR APPROVED
         with Scheduled Publish within APPROVED_NEAR_BUFFER_MINUTES of now.
      2. Scheduled Publish is a REAL near-term PHT datetime — i.e. its year
         is < FAR_FUTURE_YEAR_THRESHOLD (= 2027). Tasks whose Scheduled
         Publish is the year-2027 (or later) placeholder are a HARD publish
         block, even if their status is publishable. This makes the placeholder
         date a real safety mechanism, not just a convention.
      3. Scheduled Publish is NOT more than STALE_PUBLISH_HOURS hours in the
         past. A task whose schedule slipped past the staleness floor is
         held + quarantined; reactive posts must not silently resurrect when
         a delayed worker catches up hours later.

    Returns (cleared, reason, kind) where kind is one of:
        "ok"    — cleared; publish path may proceed
        "hold"  — not cleared but normal hold (wrong status, sched in future,
                  far-future placeholder); skip silently, do NOT mutate
        "stale" — not cleared because sched is too far in the past;
                  QUARANTINE (set needs-revision + comment)
    """
    status = status_name(task)
    if status not in (STATUS_APPROVED, STATUS_SCHEDULED):
        return False, (
            f"status is {status!r}, not {STATUS_APPROVED!r}/{STATUS_SCHEDULED!r}"
        ), "hold"
    sp = scheduled_publish_pht(task)
    if sp is None:
        return False, "no Scheduled Publish set", "hold"
    if sp.year >= FAR_FUTURE_YEAR_THRESHOLD:
        return False, (
            f"Scheduled Publish is far-future placeholder ({sp.isoformat()}); "
            f"year >= {FAR_FUTURE_YEAR_THRESHOLD} is a HARD publish block"
        ), "hold"
    now = dt.datetime.now(PHT)
    # Staleness floor — sched is too far in the past.
    age = now - sp
    if age > dt.timedelta(hours=STALE_PUBLISH_HOURS):
        hours_past = age.total_seconds() / 3600.0
        return False, (
            f"Scheduled Publish ({sp.isoformat()}) is {hours_past:.1f}h in "
            f"the past — beyond the {STALE_PUBLISH_HOURS}h staleness floor. "
            f"Reactive posts must not auto-resurrect. Set a fresh schedule "
            f"and re-approve."
        ), "stale"
    if status == STATUS_SCHEDULED:
        if now >= sp:
            return True, (
                f"SCHEDULED and now ({now.isoformat()}) >= sched ({sp.isoformat()})"
            ), "ok"
        return False, (
            f"SCHEDULED but sched ({sp.isoformat()}) is still in the future "
            f"(now={now.isoformat()})"
        ), "hold"
    # status == APPROVED
    if (sp - now) <= dt.timedelta(minutes=APPROVED_NEAR_BUFFER_MINUTES):
        return True, (
            f"APPROVED and sched ({sp.isoformat()}) is within "
            f"{APPROVED_NEAR_BUFFER_MINUTES}min of now ({now.isoformat()})"
        ), "ok"
    return False, (
        f"APPROVED but sched ({sp.isoformat()}) is more than "
        f"{APPROVED_NEAR_BUFFER_MINUTES}min from now ({now.isoformat()})"
    ), "hold"


def _is_test_task(task: dict[str, Any]) -> tuple[bool, str]:
    """TEST-task detection. Returns (is_test, reason) — reason is human-readable
    and safe to include in a ClickUp comment when the test guard fires.

    A task is a TEST task IFF ANY of these signals fire (all case-insensitive):
      A. Name matches \\bTEST\\b — standalone TEST as a whole word. Does NOT
         match PROTEST, CONTEST, LATEST, GREATEST, TESTING, TESTER, ATTEST,
         etc., because in those words TEST is not a free-standing token.
      B. Name begins with the dated test-convention prefix
         "YYYY-MM-DD [<pillar words>] TEST ..." — redundant with (A) but
         enforces the convention explicitly.
      C. Task carries a "test" tag in its ClickUp tags array.
    """
    name = task.get("name") or ""
    # Tag check is independent of the name regex
    for tag in (task.get("tags") or []):
        tag_name = tag.get("name") if isinstance(tag, dict) else tag
        if isinstance(tag_name, str) and tag_name.strip().lower() == TEST_TAG_NAME:
            return True, f"task has explicit {TEST_TAG_NAME!r} tag"
    if DATED_TEST_PREFIX_RE.match(name):
        return True, (
            f"name begins with dated test-convention prefix "
            f"(YYYY-MM-DD ... TEST): {name!r}"
        )
    if TEST_TOKEN_RE.search(name):
        return True, f"name contains the standalone TEST token (\\bTEST\\b): {name!r}"
    return False, ""


def check_publish_guards(
    task: dict[str, Any],
    *,
    validate_caption_fn: Callable[[str], list[dict[str, Any]]] | None,
    locked_ids: set[str],
) -> list[PublishBlock]:
    """Run all pre-publish safety guards on a task in fixed order.

    Returns an empty list if every guard permits the task to publish, or a
    list of PublishBlock entries explaining what fired (and whether each is
    a hold-skip or a quarantine-and-comment).
    """
    blocks: list[PublishBlock] = []
    task_id = task.get("id", "")
    name = task.get("name") or ""

    # --- GUARD 0: locked-task defense in depth ---
    # Short-circuit immediately if the task ID is locked; never even read
    # its other fields. We MUST NOT mutate a locked task, so a hit here
    # is always a non-quarantine skip.
    if task_id in locked_ids:
        blocks.append(PublishBlock(
            "LOCKED_TASK",
            f"task {task_id!r} is in the lock list (config/locked_tasks.json "
            f"or ABSOLUTELY_NEVER_PUBLISH); refusing to touch",
            quarantine=False,
        ))
        return blocks

    # --- GUARD 1: cleared-to-publish gate ---
    # Returns (cleared, reason, kind). kind="stale" must QUARANTINE; kind="hold"
    # is a normal skip (wrong status, sched in future, far-future placeholder).
    cleared, why, kind = is_cleared_to_publish(task)
    if not cleared:
        if kind == "stale":
            blocks.append(PublishBlock(
                "STALE_SCHEDULE",
                f"task is stale and will not auto-publish: {why}",
                quarantine=True,
            ))
        else:
            blocks.append(PublishBlock(
                "NOT_CLEARED_TO_PUBLISH",
                f"task is not cleared to publish: {why}",
                quarantine=False,  # hold-skip, NOT a quarantine
            ))
        # Don't run the rest — if the task isn't cleared, there's nothing
        # to validate against publishing.
        return blocks

    # --- GUARD 2: TEST-task broadcast block ---
    # Uses _is_test_task: whole-word \bTEST\b match (case-insensitive, does NOT
    # match PROTEST/CONTEST/LATEST/GREATEST), dated test-convention prefix,
    # OR explicit "test" tag.
    is_test, test_reason = _is_test_task(task)
    if is_test:
        blocks.append(PublishBlock(
            "TEST_TASK_BROADCAST_BLOCKED",
            f"task identified as a test task ({test_reason}); refusing to "
            f"broadcast to the live brand under any status. Rename the task, "
            f"remove the 'test' tag, or use a separate test environment.",
            quarantine=True,
        ))

    # --- GUARD 3: Platform broadcast safety ---
    # Platforms must come from the Platform custom field. task_platforms()
    # reads ONLY FIELD_PLATFORM (the labels field); it never falls back to
    # tags. An empty Platform field is a hard refusal, never a "publish to
    # all three by default" fallback.
    platforms = task_platforms(task)
    if not platforms:
        blocks.append(PublishBlock(
            "EMPTY_PLATFORM_FIELD",
            "Platform custom field is empty; cannot determine target "
            "platforms. (Tags are NEVER used as a fallback.) Set the "
            "Platform labels explicitly before re-approving.",
            quarantine=True,
        ))

    # --- GUARD 4: pre-publish caption validation ---
    # Run validate_caption against the EXACT text that will be sent as the
    # caption (the task description / text_content). Single source of truth
    # with create_task.py + status_poller.py. Catches the case where the
    # description was clean at creation but later edited into a brief-leak.
    caption = (task.get("text_content") or task.get("description") or "")
    caption = str(caption).strip()
    if validate_caption_fn is None:
        blocks.append(PublishBlock(
            "VALIDATE_CAPTION_UNAVAILABLE",
            "could not import create_task.validate_caption; refusing to "
            "publish without the same caption-rule check used at creation",
            quarantine=False,  # not the task's fault — fail closed, no mutation
        ))
    else:
        for v in validate_caption_fn(caption):
            blocks.append(PublishBlock(
                v.get("rule", "CAPTION_VIOLATION"),
                v.get("detail", "caption failed validate_caption"),
                quarantine=True,
            ))

    return blocks


def _enforce_publish_guards(
    task: dict[str, Any],
    clickup: "ClickUpClient",
    logger: logging.Logger,
    dry_run: bool,
    locked_ids: set[str],
) -> bool:
    """Run check_publish_guards and act on the result. Returns True if the
    task is clear to proceed to the actual publish pipeline; False otherwise.

    Side effects (live mode only): quarantine-class violations cause the task
    to be moved to needs-revision with a comment naming the failed rules.
    Dry-run mode never mutates the task — it only logs what WOULD happen.
    """
    task_id = task.get("id", "")
    task_name = task.get("name", "(unnamed)")

    blocks = check_publish_guards(
        task,
        validate_caption_fn=_validate_caption,
        locked_ids=locked_ids,
    )
    if not blocks:
        return True

    fired = ", ".join(b.rule for b in blocks)
    logger.warning(
        'Task %s "%s" — publish BLOCKED by guard(s): %s', task_id, task_name, fired,
    )
    for b in blocks:
        logger.warning("    - rule=%s (quarantine=%s): %s", b.rule, b.quarantine, b.detail)

    quarantine_blocks = [b for b in blocks if b.quarantine]
    if not quarantine_blocks:
        # All blocks are hold-skips (e.g. NOT_CLEARED_TO_PUBLISH / LOCKED_TASK).
        # Just skip silently; do NOT change status.
        logger.info(
            'Task %s "%s" — skipping (no quarantine-class violations)',
            task_id, task_name,
        )
        return False

    # At least one quarantine-class violation — needs-revision + comment.
    comment_lines = [
        "Publisher refused to publish this task. Guard violations:",
        *(f"  - rule={b.rule}: {b.detail}" for b in quarantine_blocks),
    ]
    comment_text = "\n".join(comment_lines)

    if dry_run:
        logger.info(
            '[DRY RUN] Task %s "%s" — would set status=%s and post comment '
            '(%d quarantine rule(s)). Comment preview:\n%s',
            task_id, task_name, STATUS_NEEDS_REVISION, len(quarantine_blocks),
            comment_text,
        )
        return False

    try:
        clickup.set_status(task_id, STATUS_NEEDS_REVISION)
        clickup.add_comment(task_id, comment_text)
        logger.warning(
            'Task %s "%s" — QUARANTINED → %s (%d guard rule(s))',
            task_id, task_name, STATUS_NEEDS_REVISION, len(quarantine_blocks),
        )
    except requests.HTTPError as e:
        logger.error(
            'Task %s "%s" — could not quarantine after guard violation: %s',
            task_id, task_name, e,
        )
    return False


# ---------------------------------------------------------------------------
# Per-task processing
# ---------------------------------------------------------------------------


def transition_approved_to_scheduled_if_needed(
    task: dict[str, Any],
    clickup: ClickUpClient,
    logger: logging.Logger,
    dry_run: bool,
) -> None:
    """If status is APPROVED and Scheduled Publish is more than the buffer away,
    move it to SCHEDULED."""
    status = status_name(task)
    if status != STATUS_APPROVED:
        return
    sp = scheduled_publish_pht(task)
    if sp is None:
        # No publish time — leave in APPROVED, drop a comment if not already noted
        logger.info(
            'Task %s "%s" — APPROVED with no Scheduled Publish; commenting',
            task["id"], task.get("name"),
        )
        if not dry_run:
            try:
                clickup.add_comment(
                    task["id"],
                    "No publish time set. Please set Scheduled Publish before this can go live.",
                )
            except requests.HTTPError as e:
                logger.warning("Could not comment on %s: %s", task["id"], e)
        return
    now = dt.datetime.now(PHT)
    delta = sp - now
    if delta > dt.timedelta(minutes=APPROVED_NEAR_BUFFER_MINUTES):
        logger.info(
            'Task %s "%s" — APPROVED + sched in %s → moving to SCHEDULED',
            task["id"], task.get("name"), delta,
        )
        if not dry_run:
            try:
                clickup.set_status(task["id"], STATUS_SCHEDULED)
            except requests.HTTPError as e:
                logger.warning(
                    "Could not move %s to SCHEDULED: %s", task["id"], e
                )


def is_publishable_now(task: dict[str, Any]) -> bool:
    """Quick-scan filter used by the monitor loop. Returns True for tasks that
    the publisher should process — either because they're cleared (kind="ok")
    or because they're stale and need quarantine attention (kind="stale").
    Hold-class non-clearance (wrong status, sched in future, far-future
    placeholder) returns False — those tasks are skipped silently with no
    side effects until they actually become publishable.
    """
    cleared, _, kind = is_cleared_to_publish(task)
    return cleared or kind == "stale"


def upload_with_retry(
    postforme: PostForMeClient,
    image_path: Path,
    logger: logging.Logger,
    task_id: str,
    task_name: str,
) -> str:
    """Upload the image once per task with the same retry/backoff policy as
    publish. Returns the media_url for use across all platform posts on this
    task. Raises PermanentPublishError or HaltRun on terminal failure.
    """
    last_transient: TransientPublishError | None = None
    for attempt in range(len(RETRY_BACKOFF_SECONDS) + 1):
        try:
            media_url = postforme.upload_image(image_path)
            logger.info(
                'Task %s "%s" — image uploaded (media_url=%s)',
                task_id, task_name,
                media_url[:80] + "..." if len(media_url) > 80 else media_url,
            )
            return media_url
        except HaltRun:
            raise
        except PermanentPublishError as e:
            logger.error(
                'Task %s "%s" — upload permanent error: %s',
                task_id, task_name, e,
            )
            raise
        except TransientPublishError as e:
            last_transient = e
            if attempt < len(RETRY_BACKOFF_SECONDS):
                backoff = RETRY_BACKOFF_SECONDS[attempt]
                logger.warning(
                    'Task %s "%s" — upload transient error (%s), sleeping %ds and retrying',
                    task_id, task_name, e, backoff,
                )
                time.sleep(backoff)
                continue
            logger.error(
                'Task %s "%s" — upload transient retries exhausted: %s',
                task_id, task_name, e,
            )
            raise PermanentPublishError(e.status, f"Upload retries exhausted: {e}")
    raise PermanentPublishError(None, f"Unknown upload failure path; last transient: {last_transient}")


def publish_one_platform(
    platform: str,
    caption: str,
    media_url: str | None,
    internal_account_id: str,
    postforme: PostForMeClient,
    logger: logging.Logger,
    task_id: str,
    task_name: str,
    scheduled_at: str | None = None,
    external_id: str | None = None,
) -> dict[str, Any]:
    """Create one post on one platform, with retry/backoff.

    If media_url is provided, the image must already be uploaded (pass the
    return value of upload_with_retry()). If media_url is None, the post is
    text-only — supported on Threads, not on Facebook/Instagram. Callers
    must enforce that contract; this function trusts the caller.

    Returns the success dict from PostForMeClient.create_post or raises
    PermanentPublishError / HaltRun on terminal failure.
    """
    last_transient: TransientPublishError | None = None
    for attempt in range(len(RETRY_BACKOFF_SECONDS) + 1):
        try:
            result = postforme.create_post(
                caption=caption,
                social_accounts=[internal_account_id],
                media_url=media_url,
                scheduled_at=scheduled_at,
                external_id=external_id,
            )
            logger.info(
                'Task %s "%s" [%s] — SUCCESS — post_id=%s url=%s',
                task_id, task_name, platform, result.get("post_id"), result.get("post_url"),
            )
            return result
        except HaltRun:
            raise
        except PermanentPublishError as e:
            logger.error(
                'Task %s "%s" [%s] — permanent error: %s',
                task_id, task_name, platform, e,
            )
            raise
        except TransientPublishError as e:
            last_transient = e
            if attempt < len(RETRY_BACKOFF_SECONDS):
                backoff = RETRY_BACKOFF_SECONDS[attempt]
                logger.warning(
                    'Task %s "%s" [%s] — transient error (%s), sleeping %ds and retrying',
                    task_id, task_name, platform, e, backoff,
                )
                time.sleep(backoff)
                continue
            logger.error(
                'Task %s "%s" [%s] — transient retries exhausted, treating as permanent: %s',
                task_id, task_name, platform, e,
            )
            raise PermanentPublishError(e.status, f"Transient retries exhausted: {e}")
    # Defensive — should not reach
    raise PermanentPublishError(None, f"Unknown failure path; last transient: {last_transient}")


def process_publishable_task(
    task: dict[str, Any],
    clickup: ClickUpClient,
    postforme: PostForMeClient | None,
    env: dict[str, str],
    logger: logging.Logger,
    dry_run: bool,
) -> bool:
    """Run the publish pipeline for one task. Returns True if all automated
    platforms succeeded (or the task is Reddit-only, which counts as done)."""
    task_id = task["id"]
    task_name = task.get("name", "(unnamed)")
    logger.info('=== Processing task %s "%s" ===', task_id, task_name)

    # --- Pre-publish safety guards ---
    # Runs the cleared-to-publish gate, TEST-name block, Platform safety,
    # and caption validation (same validate_caption used by create_task.py
    # and the status_poller reconcile hook). Any quarantine-class violation
    # moves the task to needs-revision with a comment naming the failed rule.
    # Hold-skips (locked task, not cleared) just return False without mutation.
    locked_ids = _load_locked_task_ids()
    if not _enforce_publish_guards(task, clickup, logger, dry_run, locked_ids):
        return False

    # Report exactly which platforms this task will broadcast to, derived
    # SOLELY from the Platform custom field (FIELD_PLATFORM). Tags are not
    # an acceptable source of truth and are never inspected.
    logger.info(
        'Task %s "%s" — Platform broadcast: %s '
        '(derived from Platform custom field, NOT tags)',
        task_id, task_name, ", ".join(task_platforms(task)),
    )

    # Read the caption from the task DESCRIPTION (the markdown body shown in
    # ClickUp's task view — the field the reviewer reads and edits). This is the
    # SOURCE OF TRUTH for what gets published to Facebook and Instagram.
    #
    # We prefer text_content (plain text — what platforms expect, no markdown)
    # and fall back to description (raw markdown) when text_content isn't populated.
    #
    # The "Final Caption" custom field is preserved as an archive copy of the AI
    # draft but is NOT read by the publisher. The "Original AI Draft" custom field
    # is the immutable original record.
    description_caption = task.get("text_content") or task.get("description") or ""

    # For multi-platform posts where Platform includes Threads alongside FB/IG,
    # Content Creation populates a Threads-specific short caption (under 500 chars)
    # in this custom field. When present, the publisher uses it for the Threads
    # post instead of the longer description. See the per-platform loop below.
    threads_caption_override = (field_value(task, FIELD_THREADS_CAPTION) or "")
    threads_caption_override = str(threads_caption_override).strip()

    image_url = field_value(task, FIELD_IMAGE_URL)
    platforms = task_platforms(task)

    if not description_caption or not str(description_caption).strip():
        logger.warning('Task %s "%s" — task description is empty; skipping', task_id, task_name)
        if not dry_run:
            try:
                clickup.add_comment(task_id, "Task description is empty. Cannot publish.")
            except requests.HTTPError:
                pass
        return False
    description_caption = str(description_caption).strip()

    if not platforms:
        logger.warning('Task %s "%s" — no Platform labels set; skipping', task_id, task_name)
        return False

    # ---- Image handling — image is OPTIONAL overall, REQUIRED per FB/IG only ----
    # FB/IG cannot publish without an image; Threads can (text-only posts often
    # perform better there). If we can't resolve a usable image, FB/IG fail
    # with a clear reason and Threads proceeds text-only.
    #
    # Resolution order (one fallback, not a system):
    #   1. FIELD_IMAGE_URL https → download to temp
    #   2. FIELD_IMAGE_URL file://  → use local file
    #   3. fallback: latest image attachment on the task → download to temp
    image_path, image_source = resolve_image_to_local_path(
        str(image_url) if image_url else None,
        task, logger, task_id,
    )
    if image_path is None:
        logger.warning(
            'Task %s "%s" — image unresolved (no usable Image URL and no '
            'image attachment on task); FB/IG will be skipped, Threads goes '
            'text-only',
            task_id, task_name,
        )
    else:
        logger.info(
            'Task %s "%s" — image resolved via %s -> %s',
            task_id, task_name, image_source, image_path,
        )

    caption_sanity_check(description_caption, logger, task_id)  # log only; don't block

    # Process each target platform
    automated_targets = [p for p in platforms if p in AUTOMATED_PLATFORMS]
    manual_targets = [p for p in platforms if p in MANUAL_PLATFORMS]

    successes: dict[str, dict[str, Any]] = {}
    failures: dict[str, Exception] = {}

    # Pre-flight: if any image-required platforms (FB/IG) are targeted but we
    # don't have a usable image, those specific platforms fail before publishing
    # starts. Threads-only tasks with no image still proceed text-only.
    image_required_blocked = [
        p for p in automated_targets
        if p in IMAGE_REQUIRED_PLATFORMS and image_path is None
    ]
    if image_required_blocked:
        logger.warning(
            'Task %s "%s" — image required for %s but Image URL is empty/unusable; '
            'those platforms will be skipped',
            task_id, task_name, ", ".join(image_required_blocked),
        )
        for platform in image_required_blocked:
            failures[platform] = PermanentPublishError(
                None,
                f"Image required for {platform} but Image URL is empty or unusable",
            )
        if not dry_run:
            try:
                clickup.add_comment(
                    task_id,
                    f"Image URL is empty or unusable. Image-required platforms "
                    f"({', '.join(image_required_blocked)}) cannot publish. "
                    f"Threads (if targeted) will publish text-only.",
                )
            except requests.HTTPError:
                pass

    # ---- Upload phase: once per task, reused across all platform posts ----
    # The same image goes to FB/IG/Threads; uploading three times wastes
    # bandwidth and risks rate limits.
    media_url: str | None = None
    if not dry_run and image_path is not None and automated_targets:
        # Only upload if (1) we actually have an image, AND (2) at least one
        # non-already-published platform that will USE it. Threads with no
        # image skips the upload entirely (text-only path).
        pending_upload = [
            p for p in automated_targets
            if not already_published_on(task, p) and p not in failures
        ]
        # Among the still-pending platforms, do any actually need the image?
        # (Threads with image_path set will use it; FB/IG always need it.)
        if pending_upload:
            assert postforme is not None
            try:
                media_url = upload_with_retry(
                    postforme, image_path, logger, task_id, task_name
                )
            except HaltRun:
                raise
            except PermanentPublishError as e:
                # Upload failed — block image-required platforms only;
                # Threads can still proceed text-only.
                for platform in pending_upload:
                    if platform in IMAGE_REQUIRED_PLATFORMS:
                        failures[platform] = e
                    else:
                        logger.warning(
                            'Task %s [%s] — image upload failed (%s); '
                            'will publish text-only',
                            task_id, platform, e,
                        )
                media_url = None  # Ensure the per-platform loop sees no media

    # ---- Scheduled-at: let Post for Me handle timed publishing -----------
    # If Scheduled Publish is comfortably in the future, pass it as scheduled_at
    # in ISO UTC and let Post for Me publish at the exact moment. If it's in
    # the past or too near now, omit scheduled_at and Post for Me publishes
    # immediately (the 1-minute buffer guards against clock skew).
    scheduled_at_utc: str | None = None
    if not dry_run:
        sp_pht = scheduled_publish_pht(task)
        if sp_pht is not None:
            sp_utc = sp_pht.astimezone(dt.timezone.utc)
            now_utc = dt.datetime.now(dt.timezone.utc)
            if sp_utc > now_utc + dt.timedelta(minutes=1):
                scheduled_at_utc = sp_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    for platform in automated_targets:
        # Per-platform idempotency
        if already_published_on(task, platform):
            logger.info(
                'Task %s "%s" [%s] — already published (Posted URL set); skipping',
                task_id, task_name, platform,
            )
            continue

        social_account_external = env.get(PLATFORM_CONFIG[platform]["env"], "").strip()

        # ---- Per-platform caption selection ----
        # Threads has a 500-character limit. When publishing to Threads alongside
        # FB/IG, the user should provide a Threads-specific short caption in the
        # Threads Caption custom field. If present and ≤500 chars, we use it.
        # Otherwise we fall back to truncating the description to 497 chars + "..."
        # Facebook and Instagram always use the full description as-is.
        if platform == "threads":
            if threads_caption_override and len(threads_caption_override) <= 500:
                platform_caption = threads_caption_override
                logger.info(
                    'Task %s [%s] — using Threads Caption field (%d chars)',
                    task_id, platform, len(platform_caption),
                )
            else:
                if threads_caption_override:
                    logger.warning(
                        'Task %s [%s] — Threads Caption field is over 500 chars (%d); falling back to truncated description',
                        task_id, platform, len(threads_caption_override),
                    )
                if len(description_caption) > 500:
                    platform_caption = description_caption[:497] + "..."
                    logger.warning(
                        'Task %s [%s] — description is %d chars; truncated to 500 for Threads',
                        task_id, platform, len(description_caption),
                    )
                else:
                    platform_caption = description_caption
        else:
            platform_caption = description_caption

        if dry_run:
            short = platform_caption[:60].replace("\n", " ")
            cred_note = (
                "" if social_account_external
                else f" (NOTE: {PLATFORM_CONFIG[platform]['env']} missing — would fail in live mode)"
            )
            # Show image path if present, "(text-only)" otherwise. For image-required
            # platforms in dry run, also flag if image is missing.
            if image_path is not None:
                image_note = f"image {image_path}"
            elif platform in IMAGE_REQUIRED_PLATFORMS:
                image_note = "NO IMAGE (would fail — this platform requires one)"
            else:
                image_note = "text-only (no image)"
            logger.info(
                '[DRY RUN] Task: %s "%s" — Would publish to %s with caption ("%s...") and %s%s',
                task_id, task_name, platform, short, image_note, cred_note,
            )
            successes[platform] = {"post_id": "DRYRUN", "post_url": "https://dry-run/"}
            continue

        # Live mode below — credentials are mandatory
        if not social_account_external:
            logger.error(
                'Task %s "%s" [%s] — missing %s in .env; skipping platform',
                task_id, task_name, platform, PLATFORM_CONFIG[platform]["env"],
            )
            failures[platform] = PermanentPublishError(
                None, f"Missing {PLATFORM_CONFIG[platform]['env']}"
            )
            continue

        # If upload already failed permanently for this platform, skip
        if platform in failures:
            continue

        # Image-required platforms need media_url; if it's None at this point
        # the platform was either blocked by the pre-flight check (already in
        # failures, skipped above) or by an upload failure that the upload
        # phase already recorded. Threads can proceed without media — that's
        # the text-only path; we pass media_url=None to publish_one_platform.
        if platform in IMAGE_REQUIRED_PLATFORMS and media_url is None:
            # Defensive — should be unreachable thanks to the pre-flight check,
            # but if we get here, record the failure clearly.
            logger.error(
                'Task %s "%s" [%s] — no media_url available for image-required platform; skipping',
                task_id, task_name, platform,
            )
            failures[platform] = PermanentPublishError(
                None, f"No image available for {platform} (image-required platform)"
            )
            continue
        # For Threads with no media_url, log that we're going text-only.
        if platform not in IMAGE_REQUIRED_PLATFORMS and media_url is None:
            logger.info(
                'Task %s "%s" [%s] — publishing text-only (no image attached)',
                task_id, task_name, platform,
            )

        assert postforme is not None

        # Resolve the .env external_id (e.g. "tfs-facebook") -> Post for Me internal id (spc_xxx).
        # Pass the platform name as a hint so the resolver disambiguates correctly
        # when one external_id is reused across multiple platforms (which happens in
        # multi-brand Post for Me projects — many disconnected stubs share the same
        # external_id, and we MUST land on the connected one matching this platform).
        try:
            internal_id = postforme.resolve_external_id(
                social_account_external, platform_hint=platform
            )
        except HaltRun:
            raise
        except (PermanentPublishError, TransientPublishError) as e:
            logger.error(
                'Task %s "%s" [%s] — failed to fetch social accounts during ID resolution: %s',
                task_id, task_name, platform, e,
            )
            failures[platform] = PermanentPublishError(
                getattr(e, "status", None), f"Account resolution failed: {e}"
            )
            continue
        if not internal_id:
            logger.error(
                'Task %s "%s" [%s] — could not resolve external_id %r to an internal Post for Me account ID',
                task_id, task_name, platform, social_account_external,
            )
            failures[platform] = PermanentPublishError(
                None, f"Unknown social account external_id: {social_account_external}"
            )
            continue

        try:
            result = publish_one_platform(
                platform=platform,
                caption=platform_caption,
                media_url=media_url,
                internal_account_id=internal_id,
                postforme=postforme,
                logger=logger,
                task_id=task_id,
                task_name=task_name,
                scheduled_at=scheduled_at_utc,
                external_id=f"{task_id}-{platform}",
            )
            successes[platform] = result
            # Write the Posted URL field immediately.
            #
            # Post for Me's initial /v1/social-posts response often omits post_url
            # because the actual platform URL isn't known until after FB/IG/Threads
            # finishes processing the post. If post_url is missing, write a
            # placeholder pointing at the Post for Me dashboard for this post —
            # status_poller.py overwrites this with the real platform URL on a
            # later cycle. Storing *something* non-empty here is essential for
            # the per-platform idempotency check (already_published_on) to work;
            # without it, the next monitor cycle would re-publish the same task.
            posted_field = PLATFORM_CONFIG[platform]["posted_url_field"]
            posted_url = result.get("post_url")
            if not posted_url:
                pfm_post_id = result.get("post_id")
                if pfm_post_id:
                    posted_url = f"https://app.postforme.dev/posts/{pfm_post_id}"
            try:
                clickup.set_custom_field(
                    task_id, posted_field, posted_url or ""
                )
            except requests.HTTPError as e:
                logger.warning(
                    "Could not write Posted URL field for %s on %s: %s",
                    platform, task_id, e,
                )
        except HaltRun:
            raise
        except PermanentPublishError as e:
            failures[platform] = e

    # Reddit (or any manual platforms) — just leave a comment
    if manual_targets and not dry_run:
        try:
            if successes:
                clickup.add_comment(
                    task_id,
                    "Automated platforms published. Reddit needs manual posting — "
                    "paste the URL into Posted URL Reddit when done.",
                )
            else:
                clickup.add_comment(
                    task_id,
                    "This task is Reddit-only. Publish manually and update Posted URL Reddit.",
                )
        except requests.HTTPError as e:
            logger.warning("Could not add manual-posting comment to %s: %s", task_id, e)

    # Compute final status transitions
    all_automated_succeeded = all(
        p in successes or already_published_on(task, p)
        for p in automated_targets
    )
    has_failures = bool(failures)

    if dry_run:
        # Dry run never advances status (per the SKILL spec)
        logger.info(
            'Task %s "%s" — DRY RUN complete; %d simulated successes, %d failures, '
            "no status updates written",
            task_id, task_name, len(successes), len(failures),
        )
        return not has_failures

    # Live mode — update status
    if all_automated_succeeded and not has_failures:
        try:
            # If task is Reddit-only with no automated platforms targeted, also move to PUBLISHED
            # (the human takes over from there)
            clickup.set_status(task_id, STATUS_PUBLISHED)
            # Write the Post for Me Post ID — prefer FB, then IG, then Threads
            for plat in ("facebook", "instagram", "threads"):
                if plat in successes and successes[plat].get("post_id"):
                    clickup.set_custom_field(
                        task_id,
                        FIELD_POSTFORME_POST_ID,
                        successes[plat]["post_id"],
                    )
                    break
            logger.info(
                'Task %s "%s" — SUCCESS — status → PUBLISHED', task_id, task_name
            )
            return True
        except requests.HTTPError as e:
            logger.error(
                'Task %s "%s" — failed to update status/post-id: %s',
                task_id, task_name, e,
            )
            return False

    if has_failures:
        # Permanent failure on at least one platform → NEEDS-REVISION
        try:
            clickup.set_status(task_id, STATUS_NEEDS_REVISION)
            summary = "; ".join(f"{p}: {err}" for p, err in failures.items())
            clickup.add_comment(
                task_id,
                f"Publish failed for {len(failures)} platform(s): {summary}. "
                "Will not retry automatically. Investigate and re-approve once fixed.",
            )
            logger.error(
                'Task %s "%s" — moved to NEEDS-REVISION (%d failures)',
                task_id, task_name, len(failures),
            )
        except requests.HTTPError as e:
            logger.error("Could not transition %s to NEEDS-REVISION: %s", task_id, e)
        return False

    # Partial success without permanent failures — keep in SCHEDULED, retry next cycle
    logger.info(
        'Task %s "%s" — partial completion; staying in SCHEDULED for next cycle',
        task_id, task_name,
    )
    return False


# ---------------------------------------------------------------------------
# Cleanup — auto-complete old PUBLISHED tasks
# ---------------------------------------------------------------------------


def cleanup_old_published(
    clickup: ClickUpClient,
    tasks: list[dict[str, Any]],
    logger: logging.Logger,
    dry_run: bool,
) -> int:
    """Move PUBLISHED tasks that have been published more than COMPLETE_AGE_DAYS
    ago into COMPLETE status. Caps at MAX_COMPLETIONS_PER_RUN per cycle.

    Uses date_done first (set when the task entered a "done" status group),
    falls back to date_updated. Both are Unix millisecond strings in ClickUp.
    """
    now = dt.datetime.now(PHT)
    age_threshold = now - dt.timedelta(days=COMPLETE_AGE_DAYS)

    eligible: list[tuple[dict[str, Any], dt.datetime]] = []
    for task in tasks:
        if status_name(task) != STATUS_PUBLISHED:
            continue
        ts_raw = task.get("date_done") or task.get("date_updated")
        if not ts_raw:
            continue
        try:
            ts_ms = int(ts_raw)
        except (TypeError, ValueError):
            continue
        published_at = dt.datetime.fromtimestamp(
            ts_ms / 1000, tz=dt.timezone.utc
        ).astimezone(PHT)
        if published_at < age_threshold:
            eligible.append((task, published_at))

    if not eligible:
        logger.info(
            "No PUBLISHED tasks older than %d days to auto-complete this cycle.",
            COMPLETE_AGE_DAYS,
        )
        return 0

    # Oldest first
    eligible.sort(key=lambda pair: pair[1])

    logger.info(
        "Found %d PUBLISHED tasks older than %d days; will complete up to %d this run",
        len(eligible), COMPLETE_AGE_DAYS, MAX_COMPLETIONS_PER_RUN,
    )
    if len(eligible) > MAX_COMPLETIONS_PER_RUN:
        deferred = [t["id"] for t, _ in eligible[MAX_COMPLETIONS_PER_RUN:]]
        logger.warning(
            "Completion cap reached — %d tasks deferred: %s",
            len(deferred), ", ".join(deferred),
        )
        eligible = eligible[:MAX_COMPLETIONS_PER_RUN]

    completed = 0
    for task, _published_at in eligible:
        task_id = task["id"]
        task_name = task.get("name", "(unnamed)")
        action_text = (
            "Would auto-complete (7 days since publish)" if dry_run
            else "Auto-completed (7 days since publish)"
        )
        if dry_run:
            logger.log(
                COMPLETE_LEVEL_NUM,
                'Task: %s "%s" — %s',
                task_id, task_name, action_text,
            )
            completed += 1
            continue
        try:
            clickup.set_status(task_id, STATUS_COMPLETE)
            logger.log(
                COMPLETE_LEVEL_NUM,
                'Task: %s "%s" — %s',
                task_id, task_name, action_text,
            )
            completed += 1
        except requests.HTTPError as e:
            logger.warning(
                'Could not auto-complete %s "%s": %s', task_id, task_name, e,
            )
    return completed


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def cmd_monitor(
    clickup: ClickUpClient,
    postforme: PostForMeClient | None,
    env: dict[str, str],
    logger: logging.Logger,
    dry_run: bool,
) -> int:
    logger.info("=== --monitor mode (dry_run=%s) ===", dry_run)
    try:
        tasks = clickup.list_tasks()
    except requests.HTTPError as e:
        logger.error("Failed to list tasks: %s", e)
        return 1

    # First pass: transition APPROVED → SCHEDULED where appropriate
    for task in tasks:
        try:
            transition_approved_to_scheduled_if_needed(task, clickup, logger, dry_run)
        except requests.HTTPError as e:
            logger.warning("Status transition failed for %s: %s", task.get("id"), e)

    # Second pass: collect publishable tasks
    publishable = [t for t in tasks if is_publishable_now(t)]
    publishable.sort(key=lambda t: scheduled_publish_pht(t) or dt.datetime.now(PHT))

    halted = False
    succeeded = 0

    if not publishable:
        logger.info("No publishable tasks this cycle.")
    else:
        logger.info(
            "Found %d publishable tasks; will process up to %d this run",
            len(publishable), MAX_PUBLISHES_PER_RUN,
        )
        if len(publishable) > MAX_PUBLISHES_PER_RUN:
            deferred = [t["id"] for t in publishable[MAX_PUBLISHES_PER_RUN:]]
            logger.warning("Monitor run cap reached — %d tasks deferred: %s", len(deferred), ", ".join(deferred))
            publishable = publishable[:MAX_PUBLISHES_PER_RUN]

        try:
            for task in publishable:
                try:
                    if process_publishable_task(task, clickup, postforme, env, logger, dry_run):
                        succeeded += 1
                except HaltRun as e:
                    logger.error("HALT — %s", e)
                    logger.error("Halting publish phase; cleanup will still run.")
                    halted = True
                    break
                except Exception as e:  # never let one task crash the loop
                    logger.exception('Unhandled error on task %s: %s', task.get("id"), e)
        finally:
            logger.info("=== publish phase done: %d/%d succeeded ===", succeeded, len(publishable))

    # Third pass: auto-complete old PUBLISHED tasks. Runs even on HaltRun
    # because cleanup only touches ClickUp (no Post for Me calls), so a
    # Post for Me auth failure shouldn't block the housekeeping job.
    try:
        cleanup_old_published(clickup, tasks, logger, dry_run)
    except Exception as e:  # never let cleanup crash the monitor
        logger.exception("Unhandled error during cleanup pass: %s", e)

    logger.info("=== --monitor cycle complete ===")
    return 2 if halted else 0


def cmd_task(
    task_id: str,
    clickup: ClickUpClient,
    postforme: PostForMeClient | None,
    env: dict[str, str],
    logger: logging.Logger,
    dry_run: bool,
) -> int:
    logger.info("=== --task mode: %s (dry_run=%s) ===", task_id, dry_run)
    try:
        task = clickup.get_task(task_id)
    except requests.HTTPError as e:
        logger.error("Failed to fetch task %s: %s", task_id, e)
        return 1
    except RuntimeError as e:
        logger.error("Refusing to proceed: %s", e)
        return 2

    try:
        ok = process_publishable_task(task, clickup, postforme, env, logger, dry_run)
    except HaltRun as e:
        logger.error("HALT — %s", e)
        return 2
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Publish ClickUp tasks to FB/IG/Threads via Post for Me."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--monitor",
        action="store_true",
        help="Scan ClickUp for tasks ready to publish (15-minute cadence).",
    )
    group.add_argument(
        "--task",
        metavar="TASK_ID",
        help="Publish one specific ClickUp task.",
    )
    args = parser.parse_args()

    logger = setup_logger()

    if not ENV_PATH.exists():
        logger.error(".env file not found at %s", ENV_PATH)
        return 10
    load_dotenv(ENV_PATH)

    env: dict[str, str] = {k: (v or "").strip() for k, v in os.environ.items()}

    live_mode = env.get("PUBLISHER_LIVE_MODE", "").lower() == "true"
    dry_run = not live_mode

    # CLICKUP_API_TOKEN is always required — without it we can't even read task state.
    # Post for Me credentials are required ONLY in live mode. In dry-run, the script
    # simulates publishes without making real API calls, so missing Post for Me creds
    # don't impede the simulation. Live-mode safety floor is preserved below.
    required = ["CLICKUP_API_TOKEN"]
    if live_mode:
        required.extend([
            "POSTFORME_API_KEY",
            "POSTFORME_PROJECT_ID",
            "POSTFORME_FB_PAGE_ID",
            "POSTFORME_IG_ACCOUNT_ID",
            "POSTFORME_THREADS_ACCOUNT_ID",
        ])
    missing = [name for name in required if not env.get(name)]
    if missing:
        logger.error(
            "Missing required env vars (mode=%s): %s",
            "LIVE" if live_mode else "DRY RUN",
            ", ".join(missing),
        )
        return 11

    if not live_mode:
        # Log which Post for Me creds are present/absent for visibility
        for name in ("POSTFORME_API_KEY", "POSTFORME_PROJECT_ID",
                     "POSTFORME_FB_PAGE_ID", "POSTFORME_IG_ACCOUNT_ID",
                     "POSTFORME_THREADS_ACCOUNT_ID"):
            logger.info("  %s: %s", name, "set" if env.get(name) else "MISSING (only needed in live mode)")

    base_url = env.get("POSTFORME_BASE_URL") or DEFAULT_POSTFORME_BASE
    logger.info("Post for Me base URL: %s", base_url)
    logger.info("ClickUp list (locked): %s", CLICKUP_LIST_ID)
    logger.info("Mode: %s", "LIVE" if live_mode else "DRY RUN")

    clickup = ClickUpClient(api_token=env["CLICKUP_API_TOKEN"], logger=logger)

    # PostForMeClient is only needed in live mode; in dry run we skip the wiring
    postforme = (
        PostForMeClient(
            api_key=env["POSTFORME_API_KEY"],
            project_id=env["POSTFORME_PROJECT_ID"],
            base_url=base_url,
            logger=logger,
        )
        if live_mode
        else None
    )

    if args.monitor:
        return cmd_monitor(clickup, postforme, env, logger, dry_run)
    return cmd_task(args.task, clickup, postforme, env, logger, dry_run)


if __name__ == "__main__":
    sys.exit(main())
