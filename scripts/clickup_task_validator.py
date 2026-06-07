"""
clickup_task_validator.py — gatekeeper for ClickUp task creation.

The Cowork-side skills (research-trending, content-creation, threads-creator)
must call this script instead of `clickup_create_task` directly. The script:

  1. Validates the task payload against the 5 ABSOLUTE RULES.
  2. If any rule fails, REJECTS the task. No ClickUp call is made. The skill
     receives a JSON list of violations and can fix the payload.
  3. If validation passes, the script creates the task via ClickUp's REST API
     on list 901614911598 (hardcoded — never accepted from the payload).
  4. Every attempt (pass or fail) is logged to /logs/task-validator-YYYY-MM-DD.log.

Usage (skill side):

    # Preferred: pipe JSON payload on stdin
    echo '<JSON>' | py scripts/clickup_task_validator.py

    # Or: read from a file
    py scripts/clickup_task_validator.py --task-file payload.json

    # Validate only (no task creation)
    py scripts/clickup_task_validator.py --dry-run < payload.json

Output (always JSON, on stdout):

    Success:   {"status": "created",   "task_id": "...", "task_url": "..."}
    Dry run:   {"status": "validated", "note": "dry-run; no task created"}
    Rejected:  {"status": "rejected",  "violations": [{"rule": N, "field": ..., "issue": ...}, ...]}
    API err:   {"status": "api_error", "message": "..."}
    Input err: {"status": "input_error", "message": "..."}

Exit codes:
    0  = success or validated-dry-run
    1  = validation failed (rejected)
    2  = ClickUp API failure
    10 = .env / input / parse error

Logs go to stderr AND to /logs/task-validator-YYYY-MM-DD.log. Stdout is reserved
for the JSON result so the calling skill can parse it cleanly.

Payload shape (JSON):

    {
      "name": "2026-05-15 Political Commentary Senate Flip FB",
      "description": "the caption text and nothing else",
      "status": "approved",
      "custom_fields": [
        {"id": "b2ffb6c6-7fe3-412d-9ba3-a7ca2ca5e9f1", "value": "<pillar option ID>"},
        {"id": "6a3e613e-524d-4471-b9eb-8fc5451e3077", "value": "<post type option ID>"},
        ...
      ],
      "tags": []   // optional; if present, stripped silently
    }

List ID is NOT taken from the payload. It is hardcoded to 901614911598.
"""

from __future__ import annotations

# Use the OS trust store so AV / firewall-intercepted TLS chains validate.
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

# Hardcoded — never accepted from payload, never overridable.
LIST_ID = "901614911598"

PHT = ZoneInfo("Asia/Manila")

# Required custom fields, keyed by ID. Skipping any of these is a rule-4 violation.
REQUIRED_FIELDS: dict[str, str] = {
    "b2ffb6c6-7fe3-412d-9ba3-a7ca2ca5e9f1": "Content Pillar",
    "6a3e613e-524d-4471-b9eb-8fc5451e3077": "Post Type",
    "ef8cfddd-c950-40b8-95ca-6da001c6ac50": "Platform",
    "8a89f1c0-f964-4281-bbe8-82f2bc187ca0": "Scheduled Publish",
    "54a8d8d0-f051-4e70-a50c-0ec526bbc1cf": "Original AI Draft",
    "f9e3e3eb-de98-406a-a716-84760d13457a": "Final Caption",
}
FIELD_ORIGINAL_DRAFT = "54a8d8d0-f051-4e70-a50c-0ec526bbc1cf"
FIELD_FINAL_CAPTION  = "f9e3e3eb-de98-406a-a716-84760d13457a"

# Rule 1: forbidden markers in the description (case-sensitive — these are the
# metadata-style labels that Cowork-scheduled invocations have been leaking in).
FORBIDDEN_MARKERS: list[str] = [
    "Hook:",
    "Core argument:",
    "Pillar:",
    "Source:",
    "File path:",
    "Brief:",
    "Post:",
    "Image prompt",
    "Scheduled Publish:",
    "Status:",
    "Notes:",
    "Character count:",
    "auto-publish halt",
    "Verification done",
    "SANITY CHECKLIST",
]

# Rule 1: file paths (project-local, .md/.py suffixes)
FORBIDDEN_PATH_MARKERS: list[str] = [
    r"Z:\\",   # backslash-escaped form (will be matched by substring after raw conversion)
    "Z:\\",
    "Z:/",
    "/output/",
    ".md",
    ".py",
]

# Rule 1: URL prefixes
FORBIDDEN_URL_PREFIXES: list[str] = ["http://", "https://", "file:///"]

# Rule 1: a line containing only "---" (markdown horizontal rule used as separator)
SEPARATOR_LINE_RE = re.compile(r"^\s*---\s*$", re.MULTILINE)

# Rule 3: canonical task-name format
TASK_NAME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} .+ ((?:FB|IG|TH|RD)(?:\+(?:FB|IG|TH|RD))*)$"
)

# Rule 3: legacy/wrong name prefixes that should never appear
FORBIDDEN_NAME_PREFIXES: list[str] = [
    "FB Post:", "FB post:",
    "IG Post:", "IG post:",
    "TH Post:", "TH post:",
    "Threads Post:", "Threads post:",
    "Reddit Post:", "Reddit post:",
    "Instagram Post:", "Instagram post:",
    "Facebook Post:", "Facebook post:",
]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logger() -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    today_pht = dt.datetime.now(PHT).strftime("%Y-%m-%d")
    log_path = LOGS_DIR / f"task-validator-{today_pht}.log"

    # Force stdout AND stderr to UTF-8 so non-ASCII content doesn't crash
    # logging on Windows consoles (cp1252 default).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    logger = logging.getLogger("task_validator")
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

    # IMPORTANT: log to STDERR, not stdout. Stdout is reserved for the JSON
    # result so the calling skill can parse it without filtering log noise.
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    return logger


# ---------------------------------------------------------------------------
# Validation rules
# ---------------------------------------------------------------------------


def _add(violations: list[dict[str, Any]], rule: int, field: str, issue: str) -> None:
    violations.append({"rule": rule, "field": field, "issue": issue})


def validate_description(description: str, violations: list[dict[str, Any]]) -> None:
    """Rule 1: description must be the caption ONLY. No metadata, no paths,
    no URLs, no `---` separator lines."""
    if not description or not description.strip():
        _add(violations, 1, "description", "description is empty")
        return

    for marker in FORBIDDEN_MARKERS:
        if marker in description:
            _add(
                violations, 1, "description",
                f"contains forbidden metadata marker: {marker!r}",
            )

    for path_marker in FORBIDDEN_PATH_MARKERS:
        if path_marker in description:
            _add(
                violations, 1, "description",
                f"contains file path marker: {path_marker!r}",
            )

    for url_prefix in FORBIDDEN_URL_PREFIXES:
        if url_prefix in description:
            _add(
                violations, 1, "description",
                f"contains URL prefix: {url_prefix!r}",
            )

    if SEPARATOR_LINE_RE.search(description):
        _add(
            violations, 1, "description",
            "contains a '---' separator line (a line with only `---`)",
        )


def strip_tags_silently(payload: dict[str, Any], logger: logging.Logger) -> None:
    """Rule 2: tags must be empty. If present, strip them silently."""
    tags = payload.get("tags")
    if tags:
        logger.info("Rule 2: stripping %d tag(s) from payload: %s", len(tags), tags)
        payload["tags"] = []


def validate_name(name: str, violations: list[dict[str, Any]]) -> None:
    """Rule 3: task name must match the canonical format."""
    if not name or not name.strip():
        _add(violations, 3, "name", "name is empty")
        return

    for prefix in FORBIDDEN_NAME_PREFIXES:
        if name.startswith(prefix):
            _add(
                violations, 3, "name",
                f"starts with forbidden legacy prefix {prefix!r}; "
                "expected format 'YYYY-MM-DD [Pillar] [Topic Slug] [FB|IG|TH|RD|combo]'",
            )
            return  # Pointless to check format if prefix is already wrong

    if not TASK_NAME_RE.match(name):
        _add(
            violations, 3, "name",
            f"does not match required format 'YYYY-MM-DD [Pillar] [Topic Slug] "
            f"[FB|IG|TH|RD|combo]': got {name!r}",
        )


def validate_required_fields(
    custom_fields: list[dict[str, Any]], violations: list[dict[str, Any]]
) -> None:
    """Rule 4: every required custom field must be present with a non-empty value."""
    field_map: dict[str, Any] = {}
    for cf in (custom_fields or []):
        fid = cf.get("id")
        if fid:
            field_map[fid] = cf.get("value")

    for fid, fname in REQUIRED_FIELDS.items():
        if fid not in field_map:
            _add(
                violations, 4, fname,
                f"required custom field '{fname}' (id={fid}) is missing",
            )
            continue
        v = field_map[fid]
        empty = (
            v is None
            or (isinstance(v, str) and not v.strip())
            or (isinstance(v, list) and len(v) == 0)
        )
        if empty:
            _add(
                violations, 4, fname,
                f"required custom field '{fname}' is present but empty",
            )


def validate_consistency(
    payload: dict[str, Any], violations: list[dict[str, Any]]
) -> None:
    """Rule 5: description, Original AI Draft, and Final Caption must match."""
    description = (payload.get("description") or "").strip()
    cf_map = {
        cf.get("id"): cf.get("value")
        for cf in (payload.get("custom_fields") or [])
    }
    orig = (cf_map.get(FIELD_ORIGINAL_DRAFT) or "")
    final = (cf_map.get(FIELD_FINAL_CAPTION) or "")
    orig_s = orig.strip() if isinstance(orig, str) else ""
    final_s = final.strip() if isinstance(final, str) else ""

    if description and orig_s and description != orig_s:
        _add(
            violations, 5, "Original AI Draft",
            "does not match description text (must be identical)",
        )
    if description and final_s and description != final_s:
        _add(
            violations, 5, "Final Caption",
            "does not match description text (must be identical)",
        )


def validate(payload: dict[str, Any], logger: logging.Logger) -> list[dict[str, Any]]:
    """Run all 5 rules. Returns a list of violations (empty = pass)."""
    violations: list[dict[str, Any]] = []
    validate_description(payload.get("description") or "", violations)
    strip_tags_silently(payload, logger)  # rule 2 — mutates payload
    validate_name(payload.get("name") or "", violations)
    validate_required_fields(payload.get("custom_fields") or [], violations)
    validate_consistency(payload, violations)
    return violations


# ---------------------------------------------------------------------------
# ClickUp task creation
# ---------------------------------------------------------------------------


def create_clickup_task(
    payload: dict[str, Any], token: str, logger: logging.Logger
) -> dict[str, Any]:
    """Create the task on list 901614911598 (hardcoded) via REST.

    Sends ONLY the safe set of fields. Never forwards `tags`, `priority`,
    `assignees`, or any other parameter from the payload.
    """
    url = f"https://api.clickup.com/api/v2/list/{LIST_ID}/task"
    headers = {"Authorization": token, "Content-Type": "application/json"}

    safe_payload: dict[str, Any] = {
        "name": payload["name"],
        "description": payload["description"],
        "status": payload.get("status", "approved"),
        "custom_fields": payload["custom_fields"],
    }

    logger.info(
        "Creating task on list %s: name=%r, status=%s, %d custom fields",
        LIST_ID, safe_payload["name"], safe_payload["status"],
        len(safe_payload["custom_fields"]),
    )

    try:
        r = requests.post(url, headers=headers, json=safe_payload, timeout=30)
    except requests.RequestException as e:
        raise RuntimeError(f"Network error talking to ClickUp: {e}")

    if r.status_code not in (200, 201):
        try:
            body = r.json()
        except Exception:
            body = r.text[:500]
        raise RuntimeError(f"ClickUp returned HTTP {r.status_code}: {body}")
    return r.json()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def emit(obj: dict[str, Any]) -> None:
    """Print a JSON result to stdout. Stdout is reserved for this — logs go
    to stderr / the daily log file."""
    sys.stdout.write(json.dumps(obj, indent=2) + "\n")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate and create a ClickUp task on list 901614911598.",
    )
    parser.add_argument(
        "--task-file", type=Path,
        help="Path to a JSON file with the task payload. If omitted, reads JSON from stdin.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Only validate; do not create the task even if validation passes.",
    )
    # --validate is accepted as a no-op flag for skill-side documentation compatibility.
    parser.add_argument(
        "--validate", action="store_true",
        help="(no-op; validation is always performed)",
    )
    args = parser.parse_args()

    logger = setup_logger()

    # ---- Read payload ----
    if args.task_file:
        try:
            payload = json.loads(args.task_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            logger.error("Task file not found: %s", args.task_file)
            emit({"status": "input_error", "message": f"Task file not found: {args.task_file}"})
            return 10
        except json.JSONDecodeError as e:
            logger.error("Could not parse task file as JSON: %s", e)
            emit({"status": "input_error", "message": f"Invalid JSON in task file: {e}"})
            return 10
    else:
        raw = sys.stdin.read()
        if not raw.strip():
            logger.error("No payload supplied (stdin was empty and --task-file not given)")
            emit({"status": "input_error", "message": "No payload supplied"})
            return 10
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("Could not parse stdin as JSON: %s", e)
            emit({"status": "input_error", "message": f"Invalid JSON on stdin: {e}"})
            return 10

    if not isinstance(payload, dict):
        logger.error("Payload must be a JSON object, got %s", type(payload).__name__)
        emit({"status": "input_error", "message": "Payload must be a JSON object"})
        return 10

    logger.info(
        "VALIDATING task — name=%r, %d custom fields, tags=%s, dry_run=%s",
        payload.get("name"),
        len(payload.get("custom_fields") or []),
        bool(payload.get("tags")),
        args.dry_run,
    )

    # ---- Validate ----
    violations = validate(payload, logger)

    if violations:
        logger.warning("REJECTED — %d violation(s):", len(violations))
        for v in violations:
            logger.warning("  - Rule %d / %s: %s", v["rule"], v["field"], v["issue"])
        emit({
            "status": "rejected",
            "list_id": LIST_ID,
            "violation_count": len(violations),
            "violations": violations,
            "hint": "Fix the violations above in your payload and try again. "
                    "Do not bypass the validator by calling ClickUp directly.",
        })
        return 1

    logger.info("PASSED validation")

    if args.dry_run:
        logger.info("Dry run — not creating task")
        emit({"status": "validated", "note": "dry-run; no task created"})
        return 0

    # ---- Load env and create ----
    if not ENV_PATH.exists():
        logger.error(".env not found at %s", ENV_PATH)
        emit({"status": "input_error", "message": f".env not found at {ENV_PATH}"})
        return 10
    load_dotenv(ENV_PATH)
    token = (os.environ.get("CLICKUP_API_TOKEN") or "").strip()
    if not token:
        logger.error("CLICKUP_API_TOKEN is missing from .env")
        emit({"status": "input_error", "message": "CLICKUP_API_TOKEN is missing"})
        return 10

    try:
        result = create_clickup_task(payload, token, logger)
    except RuntimeError as e:
        logger.error("API failure: %s", e)
        emit({"status": "api_error", "message": str(e)})
        return 2

    task_id = result.get("id")
    task_url = result.get("url")
    logger.info("CREATED task %s — %s", task_id, task_url)
    emit({
        "status": "created",
        "task_id": task_id,
        "task_url": task_url,
        "list_id": LIST_ID,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
