"""
generate_image_gemini.py - Nano Banana (Gemini 2.5 Flash Image) image worker.

A simpler alternative to generate_image.py (which uses OpenAI gpt-image-1).
This worker feeds the post caption directly to Google's Gemini image model
("Nano Banana") and asks for a scroll-stopping, text-free, brand-safe image
that complements the post. It then saves the PNG, attaches it to the ClickUp
task, and writes the hosted attachment URL into the Image URL custom field -
reusing the proven ClickUp client from generate_image.py.

Usage:
  py generate_image_gemini.py --task 86d33ff33            Generate + attach for one task
  py generate_image_gemini.py --task 86d33ff33 --dry-run  Generate + save locally only (no ClickUp writes)
  py generate_image_gemini.py --tasks 86d33ff33,86d30xhq1 Generate + attach for several tasks

Environment (.env at project root):
  GEMINI_API_KEY      required
  CLICKUP_API_TOKEN   required
  GEMINI_IMAGE_MODEL  optional, defaults to gemini-2.5-flash-image
"""

from __future__ import annotations

import truststore
truststore.inject_into_ssl()

import argparse
import base64
import datetime as dt
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

# Reuse the proven ClickUp client + helpers + constants from the OpenAI worker.
from generate_image import (
    ClickUpClient,
    setup_logger,
    field_value,
    slug_from_task_name,
    parse_pht_date,
    FIELD_IMAGE_PROMPT,
    FIELD_IMAGE_URL,
    OUTPUT_IMAGES_DIR,
    PHT,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash-image"

# Eligibility for --pending (which tasks get an unattended Gemini image)
FIELD_PLATFORM = "ef8cfddd-c950-40b8-95ca-6da001c6ac50"
PLATFORM_OPTION_FACEBOOK = "673cfb92-15e7-4315-9bbf-94db2baffa08"
PLATFORM_OPTION_INSTAGRAM = "32e72ad5-83d0-4a92-87f6-b8c8b4990a44"
MAX_PENDING_PER_RUN = 12

PROMPT_TEMPLATE = (
    "Create a scroll-stopping, editorial-quality image to accompany this social "
    "media post for The Filipino Standard, a Philippine governance and reform "
    "commentary page. Visually complement the post's core idea and emotion "
    "without being literal or clickbait. The image MUST contain NO text, "
    "letters, numbers, watermarks, or logos of any kind. If people appear they "
    "must read as authentically Filipino (Malay/mestizo features, Filipino "
    "attire and setting; not generic East-Asian or Western). No recognizable "
    "real-world politicians or celebrities. Credible and editorial, never "
    "sensational or exploitative. Square 1:1 composition.\n\n"
    "POST CAPTION:\n{caption}"
)


class GeminiImageClient:
    """Minimal REST client for Gemini image generation (Nano Banana)."""

    BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, api_key: str, model: str, logger) -> None:
        self.api_key = api_key
        self.model = model
        self.log = logger

    def generate(self, prompt: str) -> bytes:
        url = f"{self.BASE}/{self.model}:generateContent"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["IMAGE"]},
        }
        r = requests.post(
            url,
            headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
            json=body,
            timeout=180,
        )
        if r.status_code != 200:
            raise RuntimeError(f"Gemini HTTP {r.status_code}: {r.text[:600]}")
        data = r.json()
        for cand in data.get("candidates") or []:
            for part in (cand.get("content") or {}).get("parts", []) or []:
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    return base64.b64decode(inline["data"])
        raise RuntimeError(f"No image part in Gemini response: {json.dumps(data)[:600]}")


def process(task_id: str, clickup: ClickUpClient, gem: GeminiImageClient,
            logger, dry_run: bool = False) -> bool:
    task = clickup.get_task(task_id)
    name = task.get("name", "")
    caption = (task.get("text_content") or task.get("description") or "").strip()
    visual = str(field_value(task, FIELD_IMAGE_PROMPT) or "").strip()

    if (field_value(task, FIELD_IMAGE_URL) or "").strip():
        logger.info("Task %s '%s' - Image URL already set, skipping", task_id, name)
        return False
    if not caption and not visual:
        logger.info("Task %s '%s' - no caption or visual prompt, skipping", task_id, name)
        return False

    prompt = PROMPT_TEMPLATE.format(caption=caption or visual)
    if visual and caption:
        prompt += f"\n\nART DIRECTION HINT (optional, non-literal): {visual}"

    logger.info("Task %s '%s' - generating via %s (caption %d chars)",
                task_id, name, gem.model, len(caption))
    img = gem.generate(prompt)

    OUTPUT_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    date_str = parse_pht_date(task)
    slug = slug_from_task_name(name)
    ts = dt.datetime.now(PHT).strftime("rendered-%Y%m%dT%H%M%S")
    png = OUTPUT_IMAGES_DIR / f"{date_str}-{slug}.gemini.{ts}.png"
    png.write_bytes(img)
    logger.info("Task %s - saved %s (%d bytes)", task_id, png.name, len(img))

    if dry_run:
        logger.info("Task %s - DRY RUN: no ClickUp writes", task_id)
        return True

    attach = clickup.attach_image(task_id, png)
    if not attach or not attach.get("url"):
        logger.error("Task %s - attachment upload failed; Image URL not written", task_id)
        return False
    clickup.set_custom_field(task_id, FIELD_IMAGE_URL, attach["url"])
    logger.info("Task %s - SUCCESS - Image URL=%s", task_id, attach["url"])
    return True


def _needs_image(task: dict) -> bool:
    """Eligible for an unattended image: Image URL empty, status is
    approved/scheduled, and Platform targets Facebook or Instagram
    (Threads-only posts are text-only and need no image)."""
    if (field_value(task, FIELD_IMAGE_URL) or "").strip():
        return False
    status = (task.get("status") or {}).get("status", "").lower()
    if status not in ("approved", "scheduled"):
        return False
    platform = field_value(task, FIELD_PLATFORM) or []
    if not isinstance(platform, list):
        return False
    ids = {p.get("id") if isinstance(p, dict) else p for p in platform}
    return PLATFORM_OPTION_FACEBOOK in ids or PLATFORM_OPTION_INSTAGRAM in ids


def cmd_pending(clickup: "ClickUpClient", gem: "GeminiImageClient", logger) -> int:
    """Scan the list for approved/scheduled FB/IG tasks with no image and
    generate one for each (capped per run). Used by the scheduled worker."""
    try:
        tasks = clickup.list_tasks()
    except Exception as e:  # noqa: BLE001 - never crash the scheduled run
        logger.error("Failed to list tasks: %s", e)
        return 1
    eligible = [t for t in tasks if _needs_image(t)]
    logger.info(
        "Found %d task(s) needing a Gemini image; processing up to %d",
        len(eligible), MAX_PENDING_PER_RUN,
    )
    ok = 0
    for t in eligible[:MAX_PENDING_PER_RUN]:
        try:
            if process(t["id"], clickup, gem, logger):
                ok += 1
        except Exception as e:  # noqa: BLE001
            logger.exception("Task %s - unhandled error: %s", t.get("id"), e)
    logger.info("=== --pending complete: %d image(s) generated ===", ok)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate images for ClickUp tasks via Gemini (Nano Banana)."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--task", metavar="TASK_ID", help="One ClickUp task ID.")
    group.add_argument("--tasks", metavar="IDS", help="Comma-separated ClickUp task IDs.")
    group.add_argument("--pending", action="store_true",
                       help="Scan the list and image every approved/scheduled FB/IG "
                            "task that has no image yet (the scheduled-worker mode).")
    group.add_argument("--from-caption", metavar="CAPTION_FILE",
                       help="ClickUp-free: generate an image from a caption file and "
                            "save it to --out (used by the direct-publish pipeline).")
    parser.add_argument("--out", help="Output PNG path (used with --from-caption).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate and save locally; no ClickUp writes.")
    args = parser.parse_args()

    logger = setup_logger()

    if not ENV_PATH.exists():
        logger.error(".env not found at %s", ENV_PATH)
        return 10
    load_dotenv(ENV_PATH)

    gkey = (os.environ.get("GEMINI_API_KEY") or "").strip()
    ctoken = (os.environ.get("CLICKUP_API_TOKEN") or "").strip()
    model = (os.environ.get("GEMINI_IMAGE_MODEL") or "").strip() or DEFAULT_GEMINI_MODEL
    if not gkey:
        logger.error("Missing required env var: GEMINI_API_KEY")
        return 11

    logger.info("Using Gemini image model: %s", model)
    gem = GeminiImageClient(api_key=gkey, model=model, logger=logger)

    # ClickUp-free path: caption file -> PNG on disk (for the direct pipeline).
    if args.from_caption:
        if not args.out:
            logger.error("--from-caption requires --out <path.png>")
            return 1
        caption = Path(args.from_caption).read_text(encoding="utf-8").strip()
        if not caption:
            logger.error("Caption file is empty: %s", args.from_caption)
            return 1
        img = gem.generate(PROMPT_TEMPLATE.format(caption=caption))
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(img)
        logger.info("Generated image from caption -> %s (%d bytes)", out, len(img))
        return 0

    # Everything below needs ClickUp.
    if not ctoken:
        logger.error("Missing required env var: CLICKUP_API_TOKEN")
        return 11
    clickup = ClickUpClient(api_token=ctoken, logger=logger)

    if args.pending:
        return cmd_pending(clickup, gem, logger)

    ids = [args.task] if args.task else [t.strip() for t in args.tasks.split(",") if t.strip()]
    ok_count = 0
    for tid in ids:
        try:
            if process(tid, clickup, gem, logger, dry_run=args.dry_run):
                ok_count += 1
        except Exception as e:
            logger.exception("Task %s - unhandled error: %s", tid, e)
    logger.info("=== done: %d/%d succeeded ===", ok_count, len(ids))
    return 0 if ok_count == len(ids) else 1


if __name__ == "__main__":
    sys.exit(main())
