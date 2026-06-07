"""
create_task.py - The ONLY sanctioned way to create a TFS content task.

Skills and humans alike route ALL task creation through this script. Direct
calls to `clickup_create_task` (MCP), the ClickUp REST API, or any other
create path are forbidden. The validation gate here runs BEFORE any ClickUp
write; on ANY failure, the script exits non-zero and writes nothing to
ClickUp.

Architecture:
    Skill / human / CLI
        |
        v
    create_task.py    <-- structured input -> validation gate (THIS FILE)
        |
        v
    clickup_task_validator.py    <-- payload -> 5 ABSOLUTE RULES + ClickUp write
        |
        v
    ClickUp list 901614911598

Two layers of enforcement. This script catches the rich set of brief-leak
patterns and content-rule violations early; the validator backstops with
the 5 absolute structural rules and is what physically writes to ClickUp.

============================================================================
USAGE
============================================================================

Two input modes - pick one:

(A) Structured spec file (preferred for skill invocation):

    py scripts/create_task.py --spec path/to/spec.json

    Spec shape:
        {
          "task_name":     "YYYY-MM-DD [Pillar] [Topic Slug] [Platform Combo]",
          "caption":       "the full publishable caption text",
          "content_pillar":"Constitutional Awareness",
          "post_type":     "Reactive",
          "platform":      ["Facebook", "Instagram", "Threads"],
          "scheduled_publish_pht": "2027-01-15T08:00:00+08:00",
          "topic_number":  4.11,                                  // optional
          "news_hook":     "string",                              // optional
          "archetype":     "editorial_allegory",                  // optional
          "style":         "hyperreal_dramatic",                  // optional
          "image_prompt":  "1-2 sentence visual_subject",         // optional
          "text_in_image": { "headline": "...", "footer": "..." },// optional
          "threads_caption":"<=500-char Threads rewrite",         // optional
          "status":        "draft"                                // optional; default "draft"
        }

(B) Explicit CLI flags (preferred for humans):

    py scripts/create_task.py \\
        --task-name "2027-01-15 ..." \\
        --caption-file path/to/caption.txt \\
        --content-pillar "Constitutional Awareness" \\
        --post-type Reactive \\
        --platform Facebook,Instagram,Threads \\
        --scheduled-publish-pht 2027-01-15T08:00:00+08:00 \\
        --topic-number 4.11 \\
        --news-hook "..." \\
        --archetype editorial_allegory \\
        --status draft

Common to both modes:

    --dry-run            Validate only; do NOT create the task or invoke the
                         downstream validator.

Output (always JSON on stdout):

    Success:   {"status":"created",  "task_id":"...", "task_url":"...", ...}
    Dry-run:   {"status":"validated","note":"validation-only; no task created"}
    Rejected:  {"status":"rejected", "violations":[{"rule":"...","detail":"..."}]}
    API err:   {"status":"api_error","message":"..."}
    Input err: {"status":"input_error","message":"..."}

Exit codes:
    0  success or validated-dry-run
    1  validation failed (rejected)
    2  ClickUp API failure (passed through from validator)
    10 .env / input / parse error

This script ONLY writes to list 901614911598 (via the downstream validator,
which hardcodes the same list). It refuses any other list.
"""
from __future__ import annotations

# OS trust store for AV / firewall TLS chains
import truststore
truststore.inject_into_ssl()

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parent.parent
VALIDATOR_PATH = PROJECT_ROOT / "scripts" / "clickup_task_validator.py"
PHT = ZoneInfo("Asia/Manila")

# Hardcoded - downstream validator also hardcodes this. We never write to any
# other list. Reaffirmed here as the second line of defense.
LIST_ID = "901614911598"

# ---------------------------------------------------------------------------
# Canonical UUID lookups
# ---------------------------------------------------------------------------
# Sourced from scripts/publisher.py + skills/content-creation/SKILL.md +
# scripts/_phase_repair_malformed_tasks.py. Single source of truth. Names are
# the human-readable strings the agent / human supplies; values are the
# ClickUp option UUIDs. Never guess a UUID - if a value can't be resolved,
# the script fails before any ClickUp write.

FIELD_CONTENT_PILLAR    = "b2ffb6c6-7fe3-412d-9ba3-a7ca2ca5e9f1"
FIELD_POST_TYPE         = "6a3e613e-524d-4471-b9eb-8fc5451e3077"
FIELD_PLATFORM          = "ef8cfddd-c950-40b8-95ca-6da001c6ac50"
FIELD_SCHEDULED_PUBLISH = "8a89f1c0-f964-4281-bbe8-82f2bc187ca0"
FIELD_ORIGINAL_DRAFT    = "54a8d8d0-f051-4e70-a50c-0ec526bbc1cf"
FIELD_FINAL_CAPTION     = "f9e3e3eb-de98-406a-a716-84760d13457a"
FIELD_ARCHETYPE         = "ff14a5f5-9124-4a92-95c0-44a33dde7ee7"
FIELD_STYLE             = "d91c15b8-95dc-47f2-aa70-6d58effa7b01"
FIELD_IMAGE_PROMPT      = "f74ba9b7-c635-48b8-a762-5ccb093eeeaa"
FIELD_TEXT_IN_IMAGE     = "c265f867-bf8a-4be1-bf07-0777fc58cfa0"
FIELD_TOPIC_NUMBER      = "a3cfd2e6-f963-4e39-9778-c4d488802d00"
FIELD_NEWS_HOOK         = "fa544f0a-85e2-4b55-bf41-9c121898930f"
FIELD_THREADS_CAPTION   = "0f7e069d-83ab-4037-ab3a-a76720a3410d"

PILLAR_BY_NAME: dict[str, str] = {
    "Governance Comparison":     "b161199b-3f7c-4f5c-a0df-b3d882259ee5",
    "Political Commentary":      "891ebe37-d6db-4949-9f09-11c5360b9b16",
    "Constitutional Awareness":  "346a2cb6-828f-4057-a56a-ea78abc809cd",
    "Economic & Utility Reform": "d2c5c063-69c6-4c1a-b3c9-d8fddcfb248e",
    "Filipino Empowerment":      "6814f263-c442-4fb0-9bac-8d64fb527d89",
    "Business & SME Advocacy":   "1c142aa1-1160-4d0a-b50f-89da54909b58",
}

POST_TYPE_BY_NAME: dict[str, str] = {
    "Static":   "b411d4ca-43db-4cd8-b2dd-37f10e88d38a",
    "Reactive": "95f47825-d966-4381-a856-1e2a709e3da9",
    "Hybrid":   "15b2a458-ff72-49ba-8610-f6eb89df4353",
    "Reels":    "d0f50ee1-28ee-4030-ac2f-60c5baaca0dc",
}

PLATFORM_BY_NAME: dict[str, str] = {
    "Facebook":  "673cfb92-15e7-4315-9bbf-94db2baffa08",
    "Instagram": "32e72ad5-83d0-4a92-87f6-b8c8b4990a44",
    "Threads":   "225e6544-1287-44b7-a019-7f3b1fdc31e1",
    "Reddit":    "5bd32f20-3976-4c9e-931b-6f1d562c8c58",
}

# Short codes used in task names (FB+IG+TH etc.)
PLATFORM_NAME_BY_CODE: dict[str, str] = {
    "FB": "Facebook", "IG": "Instagram", "TH": "Threads", "RD": "Reddit",
}

ARCHETYPE_BY_NAME: dict[str, str] = {
    "editorial_allegory":   "46c41af2-5383-476b-b37b-e145f47f65cc",
    "ph_vs_nz_split":       "d2521238-88ac-44c4-9248-3e4f5f5fc4b6",
    "satirical_meme":       "f3b26d25-0a2a-49eb-98c5-b0a8663e8eb2",
    "constitutional_quote": "a67ef797-f40c-4e9e-8c78-d67c41471dfa",
    "pain_point":           "207c9b7b-c2ba-4d21-919d-08665929a374",
}

STYLE_BY_NAME: dict[str, str] = {
    "flat_editorial":      "dfee3861-a93b-4971-b0c5-760079711a2c",
    "cinematic_realistic": "1e690af0-bd95-4323-9577-323a4a94d5d0",
    "hyperreal_dramatic":  "e0fd5991-c930-4ee1-8678-72391466fc36",
    "editorial_cartoon":   "7d88f17c-0ae2-44d1-8376-090f757b6164",
    "documentary_photo":   "5d44f5c3-7f63-4399-b578-c54f7be7b5de",
}

# Legacy style name aliases (the new curated 8-style palette resolves to the
# closest ClickUp dropdown option for the actual write).
STYLE_ALIASES: dict[str, str] = {
    "cinematic":         "cinematic_realistic",
    "moody_documentary": "documentary_photo",
    "oil_painting":      "flat_editorial",
    "monochrome":        "flat_editorial",
    "mythic":            "hyperreal_dramatic",
    "surreal":           "hyperreal_dramatic",
    "old_cartoon":       "editorial_cartoon",
    "hopeful":           "cinematic_realistic",
}


# ---------------------------------------------------------------------------
# Validation rules (the gate)
# ---------------------------------------------------------------------------
#
# All rules below run BEFORE any ClickUp write. On ANY failure, the script
# returns "rejected" with the rule name + detail and exits non-zero.

BRIEF_MARKERS_LITERAL: list[str] = [
    "Hook:",
    "Core Argument", "Core argument",
    "Constitutional Citation", "Constitutional citation",
    "File Paths", "File paths",
    "Sources",
    "Sanity Check", "Sanity Checklist",
    "Publish Plan",
]

# Standalone header lines (a line that consists ONLY of "Note" / "Note:" /
# "Scheduled Publish" / "Scheduled Publish:") - distinct from inline
# mentions of those words in caption body.
STANDALONE_HEADER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^Note:?\s*$",              re.MULTILINE),
    re.compile(r"^Scheduled\s+Publish:?\s*$", re.MULTILINE | re.IGNORECASE),
]

# Markdown blockquote lines
BLOCKQUOTE_LINE_RE = re.compile(r"(?m)^>", )

EM_DASH = "—"
EN_DASH = "–"


def _violation(rule: str, detail: str, field: str | None = None) -> dict[str, Any]:
    v: dict[str, Any] = {"rule": rule, "detail": detail}
    if field:
        v["field"] = field
    return v


def validate_caption(caption: str) -> list[dict[str, Any]]:
    """All caption-side rules. Returns a list of violations (empty = pass)."""
    out: list[dict[str, Any]] = []
    if not caption or not caption.strip():
        out.append(_violation(
            "EMPTY_CAPTION",
            "caption is empty or whitespace-only",
            field="caption",
        ))
        return out

    # Brief-leak markers (full list per spec)
    for marker in BRIEF_MARKERS_LITERAL:
        if marker in caption:
            out.append(_violation(
                "BRIEF_MARKER_PRESENT",
                f"caption contains the brief-marker substring {marker!r}",
                field="caption",
            ))

    # Standalone header lines
    for pat in STANDALONE_HEADER_PATTERNS:
        m = pat.search(caption)
        if m:
            out.append(_violation(
                "STANDALONE_HEADER_LINE",
                f"caption contains a standalone header line matching "
                f"{pat.pattern!r}: {m.group(0)!r}",
                field="caption",
            ))

    # Markdown blockquote (any line starting with '>')
    blockquote_lines = [
        ln for ln in caption.split("\n") if ln.lstrip().startswith(">")
    ]
    if blockquote_lines:
        # Report the first one concretely
        sample = blockquote_lines[0]
        out.append(_violation(
            "MARKDOWN_BLOCKQUOTE_LINE",
            f"caption contains a markdown blockquote line "
            f"(starts with '>'). First offending line: {sample[:80]!r}. "
            f"Total blockquote lines: {len(blockquote_lines)}.",
            field="caption",
        ))

    # Em-dash and en-dash (zero tolerance)
    em_count = caption.count(EM_DASH)
    en_count = caption.count(EN_DASH)
    if em_count:
        out.append(_violation(
            "EM_DASH_PRESENT",
            f"caption contains {em_count} em-dash character(s) (U+2014). "
            f"Use ASCII hyphen '-' or comma instead.",
            field="caption",
        ))
    if en_count:
        out.append(_violation(
            "EN_DASH_PRESENT",
            f"caption contains {en_count} en-dash character(s) (U+2013). "
            f"Use ASCII hyphen '-' or comma instead.",
            field="caption",
        ))
    return out


def validate_metadata(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """All metadata-side rules."""
    out: list[dict[str, Any]] = []

    # Required fields (must be non-empty)
    if not (spec.get("task_name") or "").strip():
        out.append(_violation(
            "MISSING_REQUIRED_FIELD",
            "task_name is required and must be non-empty",
            field="task_name",
        ))
    if not (spec.get("content_pillar") or "").strip():
        out.append(_violation(
            "MISSING_REQUIRED_FIELD",
            "content_pillar is required and must be non-empty",
            field="content_pillar",
        ))
    plat = spec.get("platform")
    if not plat or (isinstance(plat, list) and not [p for p in plat if p]):
        out.append(_violation(
            "MISSING_REQUIRED_FIELD",
            "platform is required and must be a non-empty list of platform "
            "names (e.g. ['Facebook'] or ['Facebook','Instagram','Threads'])",
            field="platform",
        ))
    if not (spec.get("post_type") or "").strip():
        out.append(_violation(
            "MISSING_REQUIRED_FIELD",
            "post_type is required and must be non-empty",
            field="post_type",
        ))
    if not (spec.get("scheduled_publish_pht") or "").strip():
        out.append(_violation(
            "MISSING_REQUIRED_FIELD",
            "scheduled_publish_pht is required (ISO 8601 with +08:00 offset, "
            "or unix-ms integer)",
            field="scheduled_publish_pht",
        ))
    return out


def resolve_pillar(name: str) -> tuple[str | None, list[dict[str, Any]]]:
    if name in PILLAR_BY_NAME:
        return PILLAR_BY_NAME[name], []
    return None, [_violation(
        "UNKNOWN_PILLAR",
        f"content_pillar {name!r} is not in the canonical pillar set: "
        f"{sorted(PILLAR_BY_NAME)}",
        field="content_pillar",
    )]


def resolve_post_type(name: str) -> tuple[str | None, list[dict[str, Any]]]:
    if name in POST_TYPE_BY_NAME:
        return POST_TYPE_BY_NAME[name], []
    return None, [_violation(
        "UNKNOWN_POST_TYPE",
        f"post_type {name!r} is not in the canonical set: "
        f"{sorted(POST_TYPE_BY_NAME)}",
        field="post_type",
    )]


def resolve_platforms(platforms: list[str]) -> tuple[list[str] | None, list[dict[str, Any]]]:
    out: list[str] = []
    errors: list[dict[str, Any]] = []
    for p in platforms:
        if p in PLATFORM_BY_NAME:
            out.append(PLATFORM_BY_NAME[p])
            continue
        # Accept short codes too
        if p in PLATFORM_NAME_BY_CODE:
            out.append(PLATFORM_BY_NAME[PLATFORM_NAME_BY_CODE[p]])
            continue
        errors.append(_violation(
            "UNKNOWN_PLATFORM",
            f"platform {p!r} is not in the canonical set: "
            f"{sorted(PLATFORM_BY_NAME)} or codes {sorted(PLATFORM_NAME_BY_CODE)}",
            field="platform",
        ))
    return (out if not errors else None), errors


def resolve_archetype(name: str | None) -> tuple[str | None, list[dict[str, Any]]]:
    if not name:
        return None, []
    if name in ARCHETYPE_BY_NAME:
        return ARCHETYPE_BY_NAME[name], []
    return None, [_violation(
        "UNKNOWN_ARCHETYPE",
        f"archetype {name!r} is not in the canonical set: "
        f"{sorted(ARCHETYPE_BY_NAME)}",
        field="archetype",
    )]


def resolve_style(name: str | None) -> tuple[str | None, list[dict[str, Any]]]:
    if not name:
        return None, []
    if name in STYLE_BY_NAME:
        return STYLE_BY_NAME[name], []
    if name in STYLE_ALIASES:
        return STYLE_BY_NAME[STYLE_ALIASES[name]], []
    return None, [_violation(
        "UNKNOWN_STYLE",
        f"style {name!r} is not in the canonical set: "
        f"{sorted(STYLE_BY_NAME)} or aliases {sorted(STYLE_ALIASES)}",
        field="style",
    )]


def parse_scheduled_publish(value: str | int | float) -> tuple[int | None, list[dict[str, Any]]]:
    """Accept ISO 8601 with +08:00 offset (preferred) OR unix-ms int.
    Always returns a unix-ms integer suitable for ClickUp."""
    if isinstance(value, (int, float)):
        ms = int(value)
        if ms <= 0:
            return None, [_violation(
                "INVALID_SCHEDULED_PUBLISH",
                f"scheduled_publish_pht as integer must be a positive unix-ms; "
                f"got {value!r}",
                field="scheduled_publish_pht",
            )]
        return ms, []
    s = str(value).strip()
    if s.isdigit():
        return int(s), []
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError as e:
        return None, [_violation(
            "INVALID_SCHEDULED_PUBLISH",
            f"scheduled_publish_pht is not parseable as ISO 8601: {s!r} ({e})",
            field="scheduled_publish_pht",
        )]
    if d.tzinfo is None:
        # Assume PHT if no offset given; warn-via-violation if strict
        d = d.replace(tzinfo=PHT)
    return int(d.timestamp() * 1000), []


def parse_topic_number(value: Any) -> tuple[float | None, list[dict[str, Any]]]:
    if value in (None, "", []):
        return None, []
    try:
        return float(value), []
    except (TypeError, ValueError) as e:
        return None, [_violation(
            "INVALID_TOPIC_NUMBER",
            f"topic_number is not parseable as a number: {value!r} ({e})",
            field="topic_number",
        )]


# ---------------------------------------------------------------------------
# Payload assembly + downstream call
# ---------------------------------------------------------------------------


def build_payload(
    spec: dict[str, Any],
    caption: str,
    pillar_id: str,
    post_type_id: str,
    platform_ids: list[str],
    scheduled_ms: int,
    archetype_id: str | None,
    style_id: str | None,
    topic_number: float | None,
) -> dict[str, Any]:
    """Build the validator-shaped payload from resolved inputs."""
    custom_fields: list[dict[str, Any]] = [
        {"id": FIELD_CONTENT_PILLAR, "value": pillar_id},
        {"id": FIELD_POST_TYPE,      "value": post_type_id},
        {"id": FIELD_PLATFORM,       "value": platform_ids},
        {"id": FIELD_SCHEDULED_PUBLISH, "value": scheduled_ms},
        {"id": FIELD_ORIGINAL_DRAFT, "value": caption},
        {"id": FIELD_FINAL_CAPTION,  "value": caption},
    ]
    if archetype_id:
        custom_fields.append({"id": FIELD_ARCHETYPE, "value": archetype_id})
    if style_id:
        custom_fields.append({"id": FIELD_STYLE, "value": style_id})

    image_prompt = (spec.get("image_prompt") or "").strip()
    if image_prompt:
        custom_fields.append({"id": FIELD_IMAGE_PROMPT, "value": image_prompt})

    text_in_image = spec.get("text_in_image")
    if text_in_image:
        v = (json.dumps(text_in_image, ensure_ascii=False)
             if not isinstance(text_in_image, str) else text_in_image)
        custom_fields.append({"id": FIELD_TEXT_IN_IMAGE, "value": v})

    if topic_number is not None:
        custom_fields.append({"id": FIELD_TOPIC_NUMBER, "value": topic_number})

    news_hook = (spec.get("news_hook") or "").strip()
    if news_hook:
        custom_fields.append({"id": FIELD_NEWS_HOOK, "value": news_hook})

    threads_caption = (spec.get("threads_caption") or "").strip()
    if threads_caption:
        custom_fields.append({"id": FIELD_THREADS_CAPTION, "value": threads_caption})

    return {
        "name":          spec.get("task_name", "").strip(),
        "description":   caption,
        "status":        (spec.get("status") or "draft").strip(),
        "custom_fields": custom_fields,
    }


def call_validator(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """Pipe the payload to clickup_task_validator.py and return its output."""
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    out = proc.stdout.strip()
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        data = {"status": "input_error",
                "message": f"could not parse validator stdout: {out!r}; "
                           f"stderr: {proc.stderr[:500]!r}"}
    return proc.returncode, data


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _emit(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _read_caption(spec: dict[str, Any]) -> str:
    """Pull the caption from the spec, supporting caption-as-string or
    caption_file-as-path."""
    if spec.get("caption"):
        return str(spec["caption"])
    cap_file = spec.get("caption_file")
    if cap_file:
        return Path(cap_file).read_text(encoding="utf-8")
    return ""


def _build_spec_from_args(args: argparse.Namespace) -> dict[str, Any]:
    """Convert CLI args to the canonical spec dict shape."""
    spec: dict[str, Any] = {}
    if args.spec:
        spec.update(json.loads(Path(args.spec).read_text(encoding="utf-8")))
    if args.task_name:
        spec["task_name"] = args.task_name
    if args.caption:
        spec["caption"] = args.caption
    if args.caption_file:
        spec["caption_file"] = args.caption_file
    if args.content_pillar:
        spec["content_pillar"] = args.content_pillar
    if args.post_type:
        spec["post_type"] = args.post_type
    if args.platform:
        spec["platform"] = [p.strip() for p in args.platform.split(",") if p.strip()]
    if args.scheduled_publish_pht:
        spec["scheduled_publish_pht"] = args.scheduled_publish_pht
    if args.topic_number is not None:
        spec["topic_number"] = args.topic_number
    if args.news_hook:
        spec["news_hook"] = args.news_hook
    if args.archetype:
        spec["archetype"] = args.archetype
    if args.style:
        spec["style"] = args.style
    if args.image_prompt:
        spec["image_prompt"] = args.image_prompt
    if args.text_in_image:
        spec["text_in_image"] = args.text_in_image
    if args.threads_caption:
        spec["threads_caption"] = args.threads_caption
    if args.threads_caption_file:
        spec["threads_caption"] = Path(args.threads_caption_file).read_text(encoding="utf-8")
    if args.status:
        spec["status"] = args.status
    return spec


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sanctioned create path for TFS content tasks on list "
                    "901614911598. Validates structured input BEFORE any "
                    "ClickUp write. Direct create-task calls are forbidden.",
    )
    parser.add_argument("--spec", help="Path to a JSON spec file.")
    parser.add_argument("--task-name")
    parser.add_argument("--caption")
    parser.add_argument("--caption-file")
    parser.add_argument("--content-pillar")
    parser.add_argument("--post-type")
    parser.add_argument("--platform",
                        help="Comma-separated platform names "
                             "(e.g. 'Facebook,Instagram,Threads')")
    parser.add_argument("--scheduled-publish-pht",
                        help="ISO 8601 with +08:00 offset OR unix-ms int")
    parser.add_argument("--topic-number", type=float, default=None)
    parser.add_argument("--news-hook")
    parser.add_argument("--archetype")
    parser.add_argument("--style")
    parser.add_argument("--image-prompt")
    parser.add_argument("--text-in-image",
                        help="JSON object string for text_in_image field")
    parser.add_argument("--threads-caption")
    parser.add_argument("--threads-caption-file")
    parser.add_argument("--status", default=None,
                        help="Initial task status; default 'draft'.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run validation only; do not invoke the "
                             "downstream validator or create a task.")
    args = parser.parse_args()

    try:
        spec = _build_spec_from_args(args)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        _emit({"status": "input_error",
               "message": f"could not load spec/args: {e}"})
        return 10

    caption = _read_caption(spec)

    # ---- Run the validation gate ----
    violations: list[dict[str, Any]] = []
    violations.extend(validate_caption(caption))
    violations.extend(validate_metadata(spec))

    # Resolve UUIDs (each can add its own UNKNOWN_* violation)
    pillar_id, e1 = (None, [])
    if spec.get("content_pillar"):
        pillar_id, e1 = resolve_pillar(spec["content_pillar"])
        violations.extend(e1)
    post_type_id, e2 = (None, [])
    if spec.get("post_type"):
        post_type_id, e2 = resolve_post_type(spec["post_type"])
        violations.extend(e2)
    platform_ids, e3 = (None, [])
    if isinstance(spec.get("platform"), list) and spec["platform"]:
        platform_ids, e3 = resolve_platforms(spec["platform"])
        violations.extend(e3)
    archetype_id, e4 = resolve_archetype(spec.get("archetype"))
    violations.extend(e4)
    style_id, e5 = resolve_style(spec.get("style"))
    violations.extend(e5)
    topic_number, e6 = parse_topic_number(spec.get("topic_number"))
    violations.extend(e6)
    scheduled_ms = None
    if spec.get("scheduled_publish_pht"):
        scheduled_ms, e7 = parse_scheduled_publish(spec["scheduled_publish_pht"])
        violations.extend(e7)

    if violations:
        _emit({
            "status": "rejected",
            "violation_count": len(violations),
            "violations": violations,
            "list_id": LIST_ID,
            "note": "No ClickUp write was attempted. Fix the violations and re-run.",
        })
        return 1

    # ---- All gates passed. Build payload ----
    payload = build_payload(
        spec=spec, caption=caption,
        pillar_id=pillar_id, post_type_id=post_type_id,
        platform_ids=platform_ids, scheduled_ms=scheduled_ms,
        archetype_id=archetype_id, style_id=style_id,
        topic_number=topic_number,
    )

    if args.dry_run:
        _emit({
            "status": "validated",
            "note": "dry-run; no ClickUp write performed",
            "list_id": LIST_ID,
            "payload_preview": {
                "name":   payload["name"],
                "status": payload["status"],
                "description_length": len(payload["description"]),
                "custom_field_count": len(payload["custom_fields"]),
            },
        })
        return 0

    # ---- Hand off to the downstream validator (which performs the
    # 5 ABSOLUTE RULES + the actual ClickUp write) ----
    rc, validator_out = call_validator(payload)
    # Stamp our additional context onto the validator's output for clarity.
    validator_out.setdefault("list_id", LIST_ID)
    validator_out["created_via"] = "scripts/create_task.py"
    _emit(validator_out)
    return rc


if __name__ == "__main__":
    sys.exit(main())
