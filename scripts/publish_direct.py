"""
publish_direct.py - Publish a post straight to Facebook / Instagram / Threads via
Post for Me, with NO ClickUp in the loop. Validates the caption, uploads the
image, posts (or schedules), and records it in logs/posted_log.jsonl for
idempotency + audit.

This is the deterministic publish core for the ClickUp-free pipeline: an upstream
agent researches + writes + makes the image, then calls this to ship it.

Usage:
  # FB + IG, scheduled to a PHT peak slot, with an image:
  py publish_direct.py --platforms facebook,instagram --caption-file post.txt --image img.png --at "2026-06-10 19:00" --live

  # Threads, text-only, publish now:
  py publish_direct.py --platforms threads --caption "short reactive take" --live

  # Dry run (default): validates, resolves accounts, builds the payload, posts NOTHING.
  py publish_direct.py --platforms threads --caption "..."

Idempotency: pass --external-id, or one is derived from sha1(platforms + caption).
A run whose external_id is already in posted_log.jsonl is skipped, and Post for Me
also de-dupes on the same external_id server-side.

Env (.env): POSTFORME_API_KEY, POSTFORME_FB_PAGE_ID, POSTFORME_IG_ACCOUNT_ID,
POSTFORME_THREADS_ACCOUNT_ID, POSTFORME_PROJECT_ID, (POSTFORME_BASE_URL optional).
"""
from __future__ import annotations

import truststore
truststore.inject_into_ssl()

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

# Reuse the proven Post for Me client + platform→env wiring + caption validator.
from publisher import PostForMeClient, PLATFORM_CONFIG, setup_logger
try:
    from create_task import validate_caption as _validate_caption
except Exception:  # noqa: BLE001 - validation is best-effort
    _validate_caption = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
POSTED_LOG = PROJECT_ROOT / "logs" / "posted_log.jsonl"
PHT = ZoneInfo("Asia/Manila")
VALID_PLATFORMS = set(PLATFORM_CONFIG)  # facebook, instagram, threads


def _pht_to_utc_iso(s: str) -> str:
    """'YYYY-MM-DD HH:MM' interpreted as PHT -> ISO 8601 UTC 'Z' for Post for Me."""
    naive = dt.datetime.strptime(s.strip(), "%Y-%m-%d %H:%M")
    return naive.replace(tzinfo=PHT).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_posted_ids() -> set[str]:
    if not POSTED_LOG.exists():
        return set()
    ids: set[str] = set()
    for line in POSTED_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            eid = json.loads(line).get("external_id")
            if eid:
                ids.add(eid)
        except json.JSONDecodeError:
            pass
    return ids


def _append_log(entry: dict) -> None:
    POSTED_LOG.parent.mkdir(parents=True, exist_ok=True)
    with POSTED_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main() -> int:
    p = argparse.ArgumentParser(
        description="Publish directly to FB/IG/Threads via Post for Me (no ClickUp)."
    )
    p.add_argument("--platforms", required=True,
                   help="Comma list of: facebook, instagram, threads")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--caption", help="Caption text.")
    g.add_argument("--caption-file", help="Path to a file holding the caption.")
    p.add_argument("--image", help="Local image path (uploaded to Post for Me).")
    p.add_argument("--image-url", help="An already-hosted image URL.")
    p.add_argument("--at", help='Schedule time in PHT "YYYY-MM-DD HH:MM". Omit = publish now.')
    p.add_argument("--external-id", help="Idempotency key. Default: sha1(platforms+caption).")
    p.add_argument("--live", action="store_true",
                   help="Actually publish. Without it this is a DRY RUN (posts nothing).")
    args = p.parse_args()

    logger = setup_logger()
    if not ENV_PATH.exists():
        logger.error(".env not found at %s", ENV_PATH)
        return 10
    load_dotenv(ENV_PATH)

    platforms = [x.strip().lower() for x in args.platforms.split(",") if x.strip()]
    bad = [x for x in platforms if x not in VALID_PLATFORMS]
    if bad:
        logger.error("Unknown platform(s): %s (valid: %s)", bad, sorted(VALID_PLATFORMS))
        return 1

    caption = (args.caption if args.caption is not None
               else Path(args.caption_file).read_text(encoding="utf-8")).strip()
    if not caption:
        logger.error("Empty caption.")
        return 1

    # Same validation gate the ClickUp path used (no brief-leak, no em-dash, etc.)
    if _validate_caption is not None:
        violations = _validate_caption(caption)
        if violations:
            logger.error("Caption failed validation: %s",
                         "; ".join(v.get("rule", "") for v in violations))
            return 2

    needs_image = any(pl in ("facebook", "instagram") for pl in platforms)
    media_url = args.image_url
    image_path = Path(args.image) if args.image else None
    if needs_image and not (image_path or media_url):
        logger.error("Facebook/Instagram require an image (--image or --image-url).")
        return 2

    ext_id = args.external_id or (
        "direct-" + hashlib.sha1(
            (",".join(sorted(platforms)) + "|" + caption).encode("utf-8")
        ).hexdigest()[:16]
    )
    if ext_id in _load_posted_ids():
        logger.info("Skip: external_id %s already published (idempotent).", ext_id)
        return 0

    scheduled_at = _pht_to_utc_iso(args.at) if args.at else None

    pfm_key = (os.environ.get("POSTFORME_API_KEY") or "").strip()
    project_id = (os.environ.get("POSTFORME_PROJECT_ID") or "").strip()
    base = (os.environ.get("POSTFORME_BASE_URL") or "").strip() or "https://api.postforme.dev/v1"
    if not pfm_key or not project_id:
        logger.error("POSTFORME_API_KEY / POSTFORME_PROJECT_ID missing.")
        return 11
    pfm = PostForMeClient(api_key=pfm_key, project_id=project_id, base_url=base, logger=logger)

    social_accounts: list[str] = []
    for pl in platforms:
        ext = (os.environ.get(PLATFORM_CONFIG[pl]["env"]) or "").strip()
        if not ext:
            logger.error("Missing env %s for platform %s", PLATFORM_CONFIG[pl]["env"], pl)
            return 11
        internal = pfm.resolve_external_id(ext, pl)
        if not internal:
            logger.error("Could not resolve Post for Me account for %s (external_id=%s)", pl, ext)
            return 3
        social_accounts.append(internal)

    logger.info(
        "Prepared | platforms=%s | accounts=%s | image=%s | scheduled_at=%s | ext_id=%s | chars=%d",
        platforms, social_accounts, bool(image_path or media_url), scheduled_at, ext_id, len(caption),
    )

    # Double gate: actually publishing requires BOTH --live AND the env master
    # switch TFS_LIVE=true (or PUBLISHER_LIVE_MODE=true). During the dry-run
    # rollout the switch is off, so even if the agent passes --live, nothing ships.
    live_env = (os.environ.get("TFS_LIVE") or os.environ.get("PUBLISHER_LIVE_MODE") or "").strip().lower() == "true"
    if not (args.live and live_env):
        if args.live and not live_env:
            logger.warning("--live passed but TFS_LIVE/PUBLISHER_LIVE_MODE is not 'true' - refusing to publish.")
        logger.info("DRY RUN: nothing posted.")
        return 0

    if image_path:
        if not image_path.exists():
            logger.error("Image not found: %s", image_path)
            return 2
        media_url = pfm.upload_image(image_path)

    result = pfm.create_post(
        caption=caption,
        social_accounts=social_accounts,
        media_url=media_url,
        scheduled_at=scheduled_at,
        external_id=ext_id,
    )
    _append_log({
        "external_id": ext_id,
        "platforms": platforms,
        "social_accounts": social_accounts,
        "scheduled_at": scheduled_at,
        "post_id": result.get("post_id"),
        "post_url": result.get("post_url"),
        "chars": len(caption),
        "ts_pht": dt.datetime.now(PHT).isoformat(timespec="seconds"),
    })
    logger.info("PUBLISHED | post_id=%s | url=%s", result.get("post_id"), result.get("post_url"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
