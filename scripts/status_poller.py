"""
status_poller.py - Backfill the real platform URLs onto ClickUp tasks.

Background
----------
When publisher.py creates a post on Post for Me (POST /v1/social-posts), Post
for Me's initial response often returns post_url as None - the platform-specific
URL (e.g. https://www.facebook.com/...) isn't known until after Facebook /
Instagram / Threads actually processes the post asynchronously. To keep the
publisher's per-platform idempotency check working, publisher.py writes a
placeholder URL (https://app.postforme.dev/posts/{post_id}) into each Posted URL
field on the ClickUp task.

This poller's job: scan ClickUp for PUBLISHED tasks whose Posted URL fields are
still placeholders, query GET /v1/social-posts/{pfm_post_id}, and if the live
platform URL is now available, overwrite the placeholder with the real one.

Usage:
    py status_poller.py             # one polling pass over all PUBLISHED tasks
    py status_poller.py --task ID   # poll one specific task by ID

Env vars (read from .env at project root):
    POSTFORME_API_KEY     - required (always, dry run or live)
    CLICKUP_API_TOKEN     - required
    POSTFORME_BASE_URL    - optional; default https://api.postforme.dev/v1

ClickUp list: 901614911598 (hardcoded for safety).
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
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
LOGS_DIR = PROJECT_ROOT / "logs"

CLICKUP_LIST_ID = "901614911598"

# Custom field IDs (canonical reference is the Publisher SKILL.md)
FIELD_POSTFORME_POST_ID = "2ce7e830-2168-44c9-9eff-a54d3055d510"
FIELD_POSTED_URL_FACEBOOK = "3a5a9be9-100d-4c93-966c-6c704671a1c6"
FIELD_POSTED_URL_INSTAGRAM = "bf5d5dd3-6c52-406a-92f7-831908138106"
FIELD_POSTED_URL_THREADS = "7b44e00d-ba41-4b39-aeea-e9b07b80c263"
# Reddit is manual; we don't poll for it
FIELD_POSTED_URL_REDDIT = "f010563d-1658-4725-bd80-fca2b3410fe6"

# Fields the description self-heal writes (Platform backfill + Final Caption sync)
FIELD_PLATFORM = "ef8cfddd-c950-40b8-95ca-6da001c6ac50"
FIELD_FINAL_CAPTION = "f9e3e3eb-de98-406a-a716-84760d13457a"
PLATFORM_OPTION_FACEBOOK = "673cfb92-15e7-4315-9bbf-94db2baffa08"
PLATFORM_OPTION_INSTAGRAM = "32e72ad5-83d0-4a92-87f6-b8c8b4990a44"
PLATFORM_OPTION_THREADS = "225e6544-1287-44b7-a019-7f3b1fdc31e1"

# Which platforms this poller cares about (Reddit is manual posting only)
PLATFORM_FIELD_MAP = {
    "facebook": FIELD_POSTED_URL_FACEBOOK,
    "instagram": FIELD_POSTED_URL_INSTAGRAM,
    "threads": FIELD_POSTED_URL_THREADS,
}

PHT = ZoneInfo("Asia/Manila")
STATUS_PUBLISHED = "published"

PLACEHOLDER_PREFIX = "https://app.postforme.dev/posts/"

DEFAULT_POSTFORME_BASE = "https://api.postforme.dev/v1"

# Cap per run to prevent runaway. With every-5-minute cadence and a healthy
# pipeline, the realistic queue is small (~3 tasks/day * 7-day retention * 3
# platforms = ~63 max placeholder updates needed across a week). 50/run is plenty.
MAX_POLLS_PER_RUN = 50

# How long after a publish to keep polling. After this, the platform probably
# isn't going to return a URL ever (failed post, deleted on platform side, etc.)
# and we should stop wasting API calls. Tasks older than this are skipped — they
# keep their placeholder URLs until manually updated.
POLLING_AGE_LIMIT_DAYS = 3


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logger() -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    today_pht = dt.datetime.now(PHT).strftime("%Y-%m-%d")
    log_path = LOGS_DIR / f"status-poller-{today_pht}.log"

    # Force stdout to UTF-8 (Windows console defaults to cp1252 and chokes
    # on non-ASCII chars in log messages — em dashes, arrows, task names with
    # Filipino diacritics, etc.)
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    logger = logging.getLogger("status_poller")
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
# ClickUp REST client (minimal — read tasks, write custom fields)
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

    def list_published_tasks(self) -> list[dict[str, Any]]:
        """Return all tasks on the locked list with status PUBLISHED."""
        all_tasks: list[dict[str, Any]] = []
        page = 0
        while True:
            r = self.session.get(
                f"{self.BASE}/list/{CLICKUP_LIST_ID}/task",
                params={
                    "archived": "false",
                    "subtasks": "false",
                    "include_closed": "true",
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
        return [
            t for t in all_tasks
            if ((t.get("status") or {}).get("status", "").lower() == STATUS_PUBLISHED)
        ]

    def set_custom_field(self, task_id: str, field_id: str, value: Any) -> None:
        r = self.session.post(
            f"{self.BASE}/task/{task_id}/field/{field_id}",
            json={"value": value},
            timeout=30,
        )
        r.raise_for_status()

    def list_tasks_by_statuses(self, statuses: list[str]) -> list[dict[str, Any]]:
        """Return tasks on the locked list whose status is in `statuses`."""
        all_tasks: list[dict[str, Any]] = []
        page = 0
        while True:
            r = self.session.get(
                f"{self.BASE}/list/{CLICKUP_LIST_ID}/task",
                params={
                    "archived": "false",
                    "subtasks": "false",
                    "include_closed": "true",
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
        wanted = {s.lower() for s in statuses}
        return [
            t for t in all_tasks
            if ((t.get("status") or {}).get("status", "").lower() in wanted)
        ]

    def set_status(self, task_id: str, status: str) -> None:
        r = self.session.put(
            f"{self.BASE}/task/{task_id}",
            json={"status": status},
            timeout=30,
        )
        r.raise_for_status()

    def set_description(self, task_id: str, text: str) -> None:
        r = self.session.put(
            f"{self.BASE}/task/{task_id}",
            json={"description": text},
            timeout=30,
        )
        r.raise_for_status()

    def add_comment(self, task_id: str, text: str) -> None:
        r = self.session.post(
            f"{self.BASE}/task/{task_id}/comment",
            json={"comment_text": text, "notify_all": False},
            timeout=30,
        )
        r.raise_for_status()


# ---------------------------------------------------------------------------
# Post for Me REST client (just the one endpoint we need)
# ---------------------------------------------------------------------------


class PostForMeClient:
    def __init__(self, api_key: str, base_url: str, logger: logging.Logger) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.log = logger
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            }
        )

    def get_social_post(self, post_id: str) -> dict[str, Any] | None:
        """GET /v1/social-posts/{post_id}. Returns the parsed response, or
        None on 4xx/5xx (logged as warning). 401 logs an error and returns None
        as well — there's no point retrying with bad creds."""
        try:
            r = self.session.get(
                f"{self.base_url}/social-posts/{post_id}",
                timeout=30,
            )
        except requests.RequestException as e:
            self.log.warning("Network error fetching %s: %s", post_id, e)
            return None
        if r.status_code == 401:
            self.log.error(
                "Post for Me returned 401 for %s - bad credentials? aborting poller cycle",
                post_id,
            )
            return None
        if r.status_code not in (200, 201):
            try:
                body = r.json()
            except Exception:
                body = r.text[:200]
            self.log.warning(
                "Post for Me returned HTTP %d for %s: %s",
                r.status_code, post_id, body,
            )
            return None
        try:
            return r.json()
        except ValueError:
            self.log.warning("Post for Me returned non-JSON body for %s", post_id)
            return None


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


def is_placeholder(url: str | None) -> bool:
    """True if the URL looks like a publisher-written placeholder pointing at
    Post for Me's dashboard (rather than a real Facebook/IG/Threads URL)."""
    if not url:
        return False
    return str(url).startswith(PLACEHOLDER_PREFIX)


def task_age_pht(task: dict[str, Any]) -> dt.timedelta:
    """How long ago was this task last updated, in PHT?
    Used to skip ancient tasks whose URLs are never going to materialize."""
    ts_raw = task.get("date_updated") or task.get("date_done")
    if not ts_raw:
        return dt.timedelta(0)
    try:
        ts_ms = int(ts_raw)
    except (TypeError, ValueError):
        return dt.timedelta(0)
    ts = dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.timezone.utc).astimezone(PHT)
    return dt.datetime.now(PHT) - ts


def detect_platform_from_url(url: str) -> str | None:
    """Best-effort: identify which platform a live post URL belongs to."""
    if not url:
        return None
    u = url.lower()
    if "facebook.com" in u or "fb.me" in u or "fb.com" in u:
        return "facebook"
    if "instagram.com" in u or "instagr.am" in u:
        return "instagram"
    if "threads.net" in u or "threads.com" in u:
        return "threads"
    return None


def extract_platform_urls(pfm_response: dict[str, Any]) -> dict[str, str]:
    """Walk a Post for Me /social-posts/{id} response and return any
    platform-specific live URLs we can find, keyed by lowercase platform name.

    The response shape varies between sync/async fanout and platform type — we
    check several known locations defensively. Returns {} if nothing found.
    """
    out: dict[str, str] = {}

    # Location 1: account_configurations - list of per-account publish details.
    # This is where successful publishes typically land their URLs.
    for cfg in pfm_response.get("account_configurations") or []:
        if not isinstance(cfg, dict):
            continue
        url = cfg.get("post_url") or cfg.get("url") or cfg.get("permalink")
        if not url:
            continue
        platform = (cfg.get("platform") or "").lower() or detect_platform_from_url(url)
        if platform and platform not in out:
            out[platform] = url

    # Location 2: platform_configurations - dict keyed by platform name.
    pc = pfm_response.get("platform_configurations") or {}
    if isinstance(pc, dict):
        for platform_name, platform_cfg in pc.items():
            if not isinstance(platform_cfg, dict):
                continue
            url = (
                platform_cfg.get("post_url")
                or platform_cfg.get("url")
                or platform_cfg.get("permalink")
            )
            key = platform_name.lower()
            if url and key not in out:
                out[key] = url

    # Location 3: social_accounts may have URLs attached after publish.
    for acct in pfm_response.get("social_accounts") or []:
        if not isinstance(acct, dict):
            continue
        url = acct.get("post_url") or acct.get("url") or acct.get("permalink")
        if not url:
            continue
        platform = (acct.get("platform") or "").lower() or detect_platform_from_url(url)
        if platform and platform not in out:
            out[platform] = url

    # Location 4: top-level post_url / url (rare but possible).
    top = pfm_response.get("post_url") or pfm_response.get("url")
    if top:
        platform = detect_platform_from_url(top)
        if platform and platform not in out:
            out[platform] = top

    return out


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def process_task(
    task: dict[str, Any],
    clickup: ClickUpClient,
    postforme: PostForMeClient,
    logger: logging.Logger,
) -> int:
    """Poll one task. Returns number of Posted URL fields successfully updated."""
    task_id = task["id"]
    task_name = task.get("name", "(unnamed)")

    pfm_post_id = field_value(task, FIELD_POSTFORME_POST_ID)
    if not pfm_post_id or not str(pfm_post_id).strip():
        return 0  # No PFM record to poll — nothing to do

    # Identify which platform fields are still placeholders
    current_urls: dict[str, str | None] = {}
    needs_update: list[str] = []
    for platform, field_id in PLATFORM_FIELD_MAP.items():
        v = field_value(task, field_id)
        current_urls[platform] = v
        if is_placeholder(v):
            needs_update.append(platform)
    if not needs_update:
        return 0  # All platforms have real URLs or are empty (untargeted)

    logger.info(
        'Task %s "%s" — polling pfm_post_id=%s for platforms: %s',
        task_id, task_name, pfm_post_id, ", ".join(needs_update),
    )

    pfm_response = postforme.get_social_post(str(pfm_post_id).strip())
    if pfm_response is None:
        return 0

    real_urls = extract_platform_urls(pfm_response)
    if not real_urls:
        logger.info(
            'Task %s — no platform URLs in PFM response yet (status=%s); will retry next cycle',
            task_id, pfm_response.get("status"),
        )
        return 0

    updates = 0
    for platform in needs_update:
        real_url = real_urls.get(platform)
        if not real_url:
            continue
        if is_placeholder(real_url):
            # Defensive: don't overwrite placeholder with placeholder
            continue
        field_id = PLATFORM_FIELD_MAP[platform]
        try:
            clickup.set_custom_field(task_id, field_id, real_url)
            logger.info(
                'Task %s [%s] - backfilled Posted URL: %s', task_id, platform, real_url,
            )
            updates += 1
        except requests.HTTPError as e:
            logger.warning(
                "Task %s [%s] - failed to update Posted URL field: %s",
                task_id, platform, e,
            )
    return updates


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Description-reconciliation safety net (defense in depth)
# ---------------------------------------------------------------------------
#
# Even with scripts/create_task.py as the sanctioned create path, a task
# could in theory be created or edited by some other route (a human typing
# directly in the ClickUp UI, an older skill that wasn't updated, a manual
# REST call). This reconciliation pass scans the list for tasks in
# publishable statuses (approved / scheduled) and re-runs the same caption
# validation rules. Any failing task is moved to needs-revision and gets a
# comment naming the failed rule(s). The check is idempotent.
#
# Validation rules are imported from create_task.py so the two paths
# CANNOT drift. The lock list is read at startup; locked tasks are skipped
# (defense-in-depth on top of the existing lock check elsewhere).

try:
    # When status_poller.py is executed as `py scripts/status_poller.py`,
    # `scripts/` lands on sys.path - so create_task is importable.
    from create_task import validate_caption as _validate_caption
except ImportError:
    _validate_caption = None  # graceful degrade; logged in cmd_reconcile

# Reuse the locked-task list. Optional: if absent, treat as no locks.
def _load_locked_task_ids() -> set[str]:
    p = Path(__file__).resolve().parent.parent / "config" / "locked_tasks.json"
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    return {
        e["task_id"] for e in (data.get("locked_tasks") or [])
        if e.get("task_id")
    }


# Pulls the finished-post file path out of a brief-leak description.
_POST_FILE_RE = re.compile(
    r'output[\\/]+posts[\\/]+([^\s"<>\\/]+\.md)', re.IGNORECASE
)


def _clean_caption(text: str) -> str:
    """Strip markdown artifacts so a file's caption can be posted verbatim:
    drop heading lines (`# ...`) and horizontal rules (`---`), unwrap blockquote
    markers (`> `), remove `**bold**` markers, and replace em/en dashes."""
    out: list[str] = []
    for line in text.splitlines():
        if re.match(r'^\s{0,3}#{1,6}\s+\S', line):           # markdown heading (has space)
            continue
        if re.match(r'^\s*(-{3,}|\*{3,}|_{3,})\s*$', line):  # horizontal rule
            continue
        line = re.sub(r'^\s{0,3}>\s?', '', line)             # blockquote marker
        line = line.replace('**', '')                        # bold markers
        out.append(line)
    cleaned = "\n".join(out)
    cleaned = cleaned.replace("—", ", ").replace("–", ", ")
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
    return cleaned


def _platform_value_for(task_name: str, filename: str) -> list[str] | None:
    """Best-effort Platform field value from the task name / filename."""
    name = (task_name or "").lower()
    fn = (filename or "").lower()
    if "threads" in fn or name.startswith("threads post") or name.endswith(" th"):
        return [PLATFORM_OPTION_THREADS]
    if fn.endswith("-fb.md") or name.startswith("fb post") or "fb" in name:
        return [PLATFORM_OPTION_FACEBOOK, PLATFORM_OPTION_INSTAGRAM]
    return None


def _attempt_autorepair(
    task: dict[str, Any], desc: str, clickup: ClickUpClient, logger: logging.Logger,
) -> bool:
    """If a brief-leak description names a finished post file that exists on
    disk, replace the description with that file's cleaned caption (and backfill
    Final Caption + an empty Platform). Returns True only if the task was
    actually repaired AND the cleaned caption passes validation. On any miss
    (no path, missing file, still-invalid caption), returns False so the caller
    falls back to quarantining."""
    tid = task.get("id", "")
    m = _POST_FILE_RE.search(desc or "")
    if not m:
        return False
    filename = m.group(1)
    post_file = PROJECT_ROOT / "output" / "posts" / filename
    if not post_file.exists():
        logger.info("Auto-repair: task %s names %s but the file is missing", tid, filename)
        return False
    try:
        caption = _clean_caption(post_file.read_text(encoding="utf-8"))
    except OSError as e:
        logger.warning("Auto-repair: cannot read %s: %s", post_file, e)
        return False
    if not caption:
        return False
    # Never write a caption that itself still fails the rules — quarantine instead.
    if _validate_caption is not None and _validate_caption(caption):
        logger.info(
            "Auto-repair: cleaned caption from %s still fails validation; "
            "leaving for quarantine", filename,
        )
        return False
    try:
        clickup.set_description(tid, caption)
        clickup.set_custom_field(tid, FIELD_FINAL_CAPTION, caption)
        if not (field_value(task, FIELD_PLATFORM) or []):
            pv = _platform_value_for(task.get("name", ""), filename)
            if pv:
                clickup.set_custom_field(tid, FIELD_PLATFORM, pv)
        clickup.add_comment(
            tid,
            "Auto-repaired by status_poller: the description was a brief leak "
            f"(not the finished caption). Replaced it with the verbatim caption "
            f"from output/posts/{filename}, synced Final Caption, and backfilled "
            "Platform if it was empty. Status left unchanged. Long-term fix: "
            "create tasks via scripts/create_task.py (the validating gate), not a "
            "direct ClickUp create call."
        )
        logger.info(
            "Task %s - AUTO-REPAIRED description from %s (%d chars)",
            tid, filename, len(caption),
        )
        return True
    except requests.HTTPError as e:
        logger.error("Auto-repair: failed to write task %s: %s", tid, e)
        return False


def cmd_reconcile(
    clickup: ClickUpClient,
    logger: logging.Logger,
) -> int:
    """Scan publishable-status tasks for description-rule violations.
    Move offenders to needs-revision with a descriptive comment.

    NEVER runs on locked tasks. NEVER touches tasks already in
    needs-revision / draft / published / complete - those are not
    candidates for auto-flip."""
    if _validate_caption is None:
        logger.warning(
            "Description reconciliation skipped: could not import "
            "create_task.validate_caption (path issue?)"
        )
        return 0
    locked = _load_locked_task_ids()
    logger.info(
        "=== reconciliation: scanning approved/scheduled tasks on list %s "
        "(locked-skip set: %s) ===",
        CLICKUP_LIST_ID, sorted(locked) or "(none)",
    )
    try:
        tasks = clickup.list_tasks_by_statuses(["approved", "scheduled"])
    except requests.HTTPError as e:
        logger.error("Failed to list approved/scheduled tasks: %s", e)
        return 1
    if not tasks:
        logger.info("No approved/scheduled tasks to reconcile this pass.")
        return 0

    flipped = 0
    repaired = 0
    for task in tasks:
        tid = task.get("id", "")
        if tid in locked:
            logger.info("Skipping LOCKED task %s (per config/locked_tasks.json)", tid)
            continue
        desc = task.get("description") or ""
        violations = _validate_caption(desc)
        if not violations:
            continue
        # Self-heal first: if this brief-leak names a finished caption file on
        # disk, replace the description in place rather than quarantining.
        if _attempt_autorepair(task, desc, clickup, logger):
            repaired += 1
            continue
        rule_summary = "; ".join(
            f'{v.get("rule")}: {v.get("detail")}' for v in violations
        )
        logger.warning(
            "Task %s has %d description-rule violation(s); moving to "
            "needs-revision. Violations: %s",
            tid, len(violations), rule_summary,
        )
        try:
            clickup.set_status(tid, "needs-revision")
            clickup.add_comment(
                tid,
                "Auto-flagged by status_poller's description reconciliation: "
                "the task description failed the create_task.py caption "
                "validation rules. Failed rules:\n\n"
                + "\n".join(
                    f"- {v.get('rule')}: {v.get('detail')}"
                    for v in violations
                )
                + "\n\nFix the description and re-approve. The sanctioned "
                  "create path (scripts/create_task.py) prevents this class "
                  "of failure on task creation; this comment means a task "
                  "bypassed that path or was edited after creation."
            )
            flipped += 1
        except requests.HTTPError as e:
            logger.error(
                "Failed to move %s to needs-revision or add comment: %s",
                tid, e,
            )
    logger.info(
        "=== reconciliation complete: %d auto-repaired, %d moved to "
        "needs-revision ===",
        repaired, flipped,
    )
    return 0


def cmd_scan(
    clickup: ClickUpClient,
    postforme: PostForMeClient,
    logger: logging.Logger,
) -> int:
    logger.info("=== status poller: scanning PUBLISHED tasks on list %s ===", CLICKUP_LIST_ID)
    try:
        tasks = clickup.list_published_tasks()
    except requests.HTTPError as e:
        logger.error("Failed to list PUBLISHED tasks: %s", e)
        return 1

    age_limit = dt.timedelta(days=POLLING_AGE_LIMIT_DAYS)
    # Filter: needs poll = has a placeholder URL on some platform field AND not too old
    candidates: list[dict[str, Any]] = []
    for task in tasks:
        any_placeholder = any(
            is_placeholder(field_value(task, fid))
            for fid in PLATFORM_FIELD_MAP.values()
        )
        if not any_placeholder:
            continue
        if task_age_pht(task) > age_limit:
            continue
        candidates.append(task)

    if not candidates:
        logger.info("No tasks have placeholder URLs needing polling this cycle.")
        return 0

    logger.info(
        "Found %d task(s) with placeholder URLs; will poll up to %d this run",
        len(candidates), MAX_POLLS_PER_RUN,
    )
    if len(candidates) > MAX_POLLS_PER_RUN:
        deferred = [t["id"] for t in candidates[MAX_POLLS_PER_RUN:]]
        logger.warning(
            "Poll cap reached - %d tasks deferred to next cycle: %s",
            len(deferred), ", ".join(deferred),
        )
        candidates = candidates[:MAX_POLLS_PER_RUN]

    total_updates = 0
    for task in candidates:
        try:
            total_updates += process_task(task, clickup, postforme, logger)
        except Exception as e:  # never let one task crash the loop
            logger.exception('Unhandled error polling task %s: %s', task.get("id"), e)
    logger.info(
        "=== poller cycle complete: backfilled %d Posted URL field(s) across %d task(s) ===",
        total_updates, len(candidates),
    )
    return 0


def cmd_task(
    task_id: str,
    clickup: ClickUpClient,
    postforme: PostForMeClient,
    logger: logging.Logger,
) -> int:
    logger.info("=== status poller: --task mode for %s ===", task_id)
    try:
        task = clickup.get_task(task_id)
    except requests.HTTPError as e:
        logger.error("Failed to fetch task %s: %s", task_id, e)
        return 1
    except RuntimeError as e:
        logger.error("Refusing to proceed: %s", e)
        return 2

    updates = process_task(task, clickup, postforme, logger)
    logger.info("=== --task complete: backfilled %d Posted URL field(s) ===", updates)
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill real platform URLs onto ClickUp PUBLISHED "
                    "tasks. Also runs a description-rule reconciliation "
                    "pass against approved/scheduled tasks each cycle.",
    )
    parser.add_argument(
        "--task",
        metavar="TASK_ID",
        help="Poll one specific task instead of scanning all PUBLISHED tasks.",
    )
    parser.add_argument(
        "--no-reconcile", action="store_true",
        help="Skip the description-reconciliation pass this cycle.",
    )
    parser.add_argument(
        "--reconcile-only", action="store_true",
        help="Skip the URL backfill pass; run ONLY the description "
             "reconciliation.",
    )
    args = parser.parse_args()

    logger = setup_logger()

    if not ENV_PATH.exists():
        logger.error(".env file not found at %s", ENV_PATH)
        return 10
    load_dotenv(ENV_PATH)

    pfm_key = (os.environ.get("POSTFORME_API_KEY") or "").strip()
    clickup_token = (os.environ.get("CLICKUP_API_TOKEN") or "").strip()
    base_url = (os.environ.get("POSTFORME_BASE_URL") or "").strip() or DEFAULT_POSTFORME_BASE

    missing = [
        name for name, val in {
            "POSTFORME_API_KEY": pfm_key,
            "CLICKUP_API_TOKEN": clickup_token,
        }.items() if not val
    ]
    if missing:
        logger.error("Missing required env vars: %s", ", ".join(missing))
        return 11

    logger.info("Post for Me base URL: %s", base_url)
    logger.info("ClickUp list (locked): %s", CLICKUP_LIST_ID)

    clickup = ClickUpClient(api_token=clickup_token, logger=logger)
    postforme = PostForMeClient(api_key=pfm_key, base_url=base_url, logger=logger)

    if args.task:
        return cmd_task(args.task, clickup, postforme, logger)

    if args.reconcile_only:
        return cmd_reconcile(clickup, logger)

    # Default scheduled-run behavior: URL backfill + description reconciliation
    rc_scan = cmd_scan(clickup, postforme, logger)
    if args.no_reconcile:
        return rc_scan
    rc_reconcile = cmd_reconcile(clickup, logger)
    # Surface the worst of the two return codes
    return rc_scan if rc_scan != 0 else rc_reconcile


if __name__ == "__main__":
    sys.exit(main())
