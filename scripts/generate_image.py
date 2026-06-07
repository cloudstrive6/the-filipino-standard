"""
generate_image.py - The Filipino Standard image generation worker.

Pipeline: OpenAI gpt-image-1 for generation, gpt-4o for vision QA. Each task
runs an archetype-driven prompt template, a fact-check sanity scan, a
generation, and a vision QA pass. Up to 2 regen retries on QA failure. After
3 total failures, the task is moved to NEEDS-REVISION on ClickUp.

Inputs (per task) are read from ClickUp custom fields on list 901614911598:
  - archetype (dropdown): which prompt template to use
  - text_in_image (long text, JSON): headline/labels/stats to render in the image
  - Image Prompt (long text): the visual_subject (1-2 sentence scene description)
  - Description: the post caption (used for fact-check context only)

Outputs per task in /output/images/:
  - YYYY-MM-DD-<slug>.png      (the final image)
  - YYYY-MM-DD-<slug>.json     (sidecar with exact prompt, archetype, size,
                                 attempt history, QA results)

Usage:
  py generate_image.py --pending          Scan ClickUp for tasks needing images
  py generate_image.py --task 86d2z9gz3   Generate for a specific task

Environment (read from .env at project root):
  OPENAI_API_KEY              required
  CLICKUP_API_TOKEN           required
  OPENAI_IMAGE_MODEL          optional, defaults to gpt-image-1
  OPENAI_VISION_MODEL         optional, defaults to gpt-4o

ClickUp list: 901614911598 (hardcoded for safety).
"""

from __future__ import annotations

# OS trust store for AV-intercepted TLS chains
import truststore
truststore.inject_into_ssl()

import argparse
import base64
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

# ALL text (headline, labels, quote, attribution, meme top/bottom text, brand
# footer) is rendered deterministically by Pillow after generation, before
# vision QA. The model is asked to produce text-free imagery only. This
# permanently eliminates the entire text failure class (clipping, case
# errors, decimal mistakes, dropped text, garbled prop text).
from image_text_overlay import apply_text_for_archetype_to_bytes, verify_layout
from io import BytesIO
from PIL import Image


# ---------------------------------------------------------------------------
# Project constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
OUTPUT_IMAGES_DIR = PROJECT_ROOT / "output" / "images"
LOGS_DIR = PROJECT_ROOT / "logs"
KEY_FACTS_PATH = PROJECT_ROOT / "context" / "key_facts.md"

# Hardcoded list ID for safety. Never write to any other list.
CLICKUP_LIST_ID = "901614911598"

# Existing custom field IDs (from the canonical reference in Publisher SKILL.md)
FIELD_IMAGE_PROMPT     = "f74ba9b7-c635-48b8-a762-5ccb093eeeaa"  # now: visual_subject
FIELD_IMAGE_URL        = "34e674b6-bfb7-4c71-80f3-35065c84f1a3"
FIELD_CONTENT_PILLAR   = "b2ffb6c6-7fe3-412d-9ba3-a7ca2ca5e9f1"
FIELD_SCHEDULED_PUBLISH = "8a89f1c0-f964-4281-bbe8-82f2bc187ca0"

# New custom field IDs created via API
FIELD_ARCHETYPE     = "ff14a5f5-9124-4a92-95c0-44a33dde7ee7"
FIELD_TEXT_IN_IMAGE = "c265f867-bf8a-4be1-bf07-0777fc58cfa0"
FIELD_STYLE         = "d91c15b8-95dc-47f2-aa70-6d58effa7b01"

ARCHETYPE_OPTION_IDS: dict[str, str] = {
    "editorial_allegory":   "46c41af2-5383-476b-b37b-e145f47f65cc",
    "ph_vs_nz_split":       "d2521238-88ac-44c4-9248-3e4f5f5fc4b6",
    "satirical_meme":       "f3b26d25-0a2a-49eb-98c5-b0a8663e8eb2",
    "constitutional_quote": "a67ef797-f40c-4e9e-8c78-d67c41471dfa",
    "pain_point":           "207c9b7b-c2ba-4d21-919d-08665929a374",
}
OPTION_ID_TO_ARCHETYPE = {v: k for k, v in ARCHETYPE_OPTION_IDS.items()}

STYLE_OPTION_IDS: dict[str, str] = {
    "flat_editorial":      "dfee3861-a93b-4971-b0c5-760079711a2c",
    "cinematic_realistic": "1e690af0-bd95-4323-9577-323a4a94d5d0",
    "hyperreal_dramatic":  "e0fd5991-c930-4ee1-8678-72391466fc36",
    "editorial_cartoon":   "7d88f17c-0ae2-44d1-8376-090f757b6164",
    "documentary_photo":   "5d44f5c3-7f63-4399-b578-c54f7be7b5de",
}
OPTION_ID_TO_STYLE = {v: k for k, v in STYLE_OPTION_IDS.items()}

# Timezone, always PHT for log timestamps and filenames
PHT = ZoneInfo("Asia/Manila")

# OpenAI models. Overridable via env vars.
DEFAULT_IMAGE_MODEL  = "gpt-image-1"
DEFAULT_VISION_MODEL = "gpt-4o"

# Valid sizes for gpt-image-1
VALID_SIZES = {"1024x1024", "1024x1536", "1536x1024"}

# Max regeneration attempts after the initial generation, total 3 tries
MAX_REGEN_RETRIES = 2


# ---------------------------------------------------------------------------
# Lock list (permanent re-run protection)
# ---------------------------------------------------------------------------
#
# Tasks listed in config/locked_tasks.json are PERMANENTLY locked. The
# pipeline refuses to process them - no matter what - even with explicit
# --task <id> invocation, even if Image URL is cleared, even if status is
# changed. Approved, human-blessed assets can NEVER be silently destroyed
# by a re-run again. To validate a future change on a locked archetype,
# create a NEW throwaway task; never re-run a locked one.

LOCKED_TASKS_PATH = PROJECT_ROOT / "config" / "locked_tasks.json"


def load_locked_task_ids() -> dict[str, dict[str, Any]]:
    """Load the lock list from disk. Returns {task_id: entry_dict}. Missing
    file => empty dict (no tasks locked). Malformed file => empty dict +
    stderr warning - we never silently treat a malformed lock file as
    'nothing is locked' without surfacing the error."""
    if not LOCKED_TASKS_PATH.exists():
        return {}
    try:
        data = json.loads(LOCKED_TASKS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        sys.stderr.write(
            f"WARNING: could not parse {LOCKED_TASKS_PATH}: {e}\n"
            "Proceeding with NO locks. Fix the file and re-run.\n"
        )
        return {}
    entries = data.get("locked_tasks") or []
    return {entry["task_id"]: entry for entry in entries if entry.get("task_id")}


# ---------------------------------------------------------------------------
# Archetype size table
# ---------------------------------------------------------------------------
# The only per-archetype thing the pipeline now needs from this dict is the
# default canvas size. All archetypes share the same prompt path (light
# prompt → text-free scene), the same QA prompts, and the same dispatcher
# in image_text_overlay.py.

ARCHETYPES: dict[str, dict[str, Any]] = {
    "editorial_allegory":   {"default_size": "1024x1024"},
    "ph_vs_nz_split":       {"default_size": "1536x1024"},
    "satirical_meme":       {"default_size": "1024x1024"},
    "constitutional_quote": {"default_size": "1024x1536"},
    "pain_point":           {"default_size": "1024x1024"},
}


# ---------------------------------------------------------------------------
# Curated style palette (the new light-prompt path)
# ---------------------------------------------------------------------------
#
# Eight one-line style descriptors injected verbatim into the light prompt.
# The agent's `style` field picks one of these; the script wraps it into
# the prompt body. Adding new styles: extend STYLE_DESCRIPTORS; legacy
# values still on existing tasks resolve via LEGACY_STYLE_MAPPING below.

STYLE_DESCRIPTORS: dict[str, str] = {
    "cinematic":         "cinematic film-still, controlled dramatic lighting, emotional weight, slightly desaturated",
    "moody_documentary": "moody documentary photograph, dim contemplative natural light, candid, grounded",
    "oil_painting":      "classical oil painting, painterly, timeless civic gravitas",
    "monochrome":        "stark black-and-white, somber, high-contrast, no prettiness",
    "mythic":            "epic dramatic illustration, mythic scale, symbolic confrontation",
    "surreal":           "surreal symbolic illustration, dreamlike, conceptual metaphor",
    "old_cartoon":       "vintage 1930s rubber-hose editorial cartoon, ink-and-wash, satirical caricature",
    "hopeful":           "warm hopeful dawn light, uplifting, forward-looking",
}

# Legacy style names from earlier iterations of the pipeline. Existing tasks
# still carry these in the `style` ClickUp dropdown; this map resolves them
# to the new curated palette so we don't need to mass-migrate ClickUp data.
LEGACY_STYLE_MAPPING: dict[str, str] = {
    "documentary_photo":   "moody_documentary",
    "cinematic_realistic": "cinematic",
    "hyperreal_dramatic":  "mythic",
    "editorial_cartoon":   "old_cartoon",
    "flat_editorial":      "oil_painting",
}


def resolve_style_descriptor(style_name: str | None) -> tuple[str, str]:
    """Map a style name (new curated or legacy) to (canonical_name, descriptor).
    Falls back to 'cinematic' if the name is unknown so the pipeline degrades
    gracefully rather than crashing on a bad style value."""
    name = (style_name or "").strip()
    if name in STYLE_DESCRIPTORS:
        return name, STYLE_DESCRIPTORS[name]
    mapped = LEGACY_STYLE_MAPPING.get(name)
    if mapped and mapped in STYLE_DESCRIPTORS:
        return mapped, STYLE_DESCRIPTORS[mapped]
    return "cinematic", STYLE_DESCRIPTORS["cinematic"]


# Aspect ratio token derived from the canvas size. Inlined into the prompt
# so the model knows the target frame proportion.
_ASPECT_RATIO_TOKENS: dict[str, str] = {
    "1024x1024": "Square 1:1 aspect ratio",
    "1024x1536": "Portrait 2:3 aspect ratio",
    "1536x1024": "Landscape 3:2 aspect ratio",
}


# ---------------------------------------------------------------------------
# Archetype prompt templates
# ---------------------------------------------------------------------------

# Brand rules appended verbatim to every prompt. Critical contract with the
# model: these rules must be reflected in the rendered image, especially in
# any text overlays.
BRAND_RULES_APPENDIX = (
    "CRITICAL BRAND RULES (apply to all depicted subjects and scene "
    "content):\n"
    "1. Subjects depicting Filipinos must be authentically Filipino in "
    "facial features (Malay/mestizo features), clothing, and setting. Use "
    "settings like a jeepney interior, a palengke stall, a sari-sari store, "
    "an MRT platform, a tiangge, a home kitchen with a rice cooker, a BIR "
    "or SSS office; NOT a generic 'East-Asian' stand-in and NOT a generic "
    "Western caricature.\n"
    "2. No recognizable real-world political figures' faces. Caricature "
    "only as types, never as specific identifiable people.\n"
    "3. No trademarked logos (no Meralco logo, no Coca-Cola, no political "
    "party seals, no recognizable brand marks of any kind).\n"
    "\n"
    "ZERO TEXT REINFORCEMENT: This image must contain NO text whatsoever. "
    "No words, letters, numbers, signs with legible text, labels, "
    "captions, watermarks, page numbers, signatures, store names, brand "
    "names, document text, phone screen UI, licence plates, name tags, "
    "or typography of ANY kind, anywhere in the image. A separate Pillow "
    "step overlays all text after generation. Any documents, signs, "
    "papers, cheques, bills, receipts, screens, or other surfaces that "
    "would normally carry text must be visually blank or show only "
    "abstract non-text marks (lines, smudges, generic patterns). Even "
    "garbled, illegible, or partial text is a failure - aim for "
    "completely text-free surfaces.\n"
)


def build_prompt(
    archetype: str,
    visual_subject: str,
    text_in_image: dict[str, Any],
    style: str | None = None,
    size: str | None = None,
) -> str:
    """Compose the image-generation prompt.

    One path for every archetype: a short light prompt that sets the style
    register, the brand boundary (no recognizable politicians, authentic
    Filipino subjects, no logos), the no-text directive, and the aspect
    ratio. No prescriptive composition, no text_in_image in the prompt —
    burned-in text is rendered post-generation by Pillow per the dispatcher
    in scripts/image_text_overlay.py.
    """
    if archetype not in ARCHETYPES:
        raise ValueError(f"Unknown archetype: {archetype!r}")
    return _build_light_prompt(archetype, visual_subject, style, size)


def _build_light_prompt(
    archetype: str,
    visual_subject: str,
    style: str | None,
    size: str | None,
) -> str:
    """The new light prompt. One short paragraph: style register + brand
    identity + theme summary + no-text + Filipino authenticity + credibility +
    aspect ratio. That is the whole prompt. No prescriptive composition.

    For satirical_meme the style is forced to old_cartoon regardless of what
    the task carries. For everything else the agent's style choice (from the
    8 curated palette, or a legacy mapping) is honored.

    The `visual_subject` field is used as the theme summary - whatever the
    agent put there describes what the post is about. The surrounding
    template explicitly tells the model not to be literal, so even a verbose
    or prescriptive visual_subject still produces a non-literal interpretation.
    """
    if archetype == "satirical_meme":
        style_name, style_descriptor = "old_cartoon", STYLE_DESCRIPTORS["old_cartoon"]
    else:
        style_name, style_descriptor = resolve_style_descriptor(style)
    theme = visual_subject.strip().rstrip(".").rstrip() or "a Philippine governance-reform theme"
    aspect = _ASPECT_RATIO_TOKENS.get(size or "1024x1024", "Square 1:1 aspect ratio")
    return (
        f"Create a scroll-stopping, attention-grabbing {style_descriptor} image "
        f"for The Filipino Standard, a Philippine governance-reform commentary page. "
        f"The image should visually complement this post without being literal or "
        f"clickbait: {theme}. NO text anywhere in the image - a watermark is added "
        f"separately. If people appear they must read as authentically Filipino "
        f"(Malay/mestizo features, Filipino attire and setting, not generic "
        f"East-Asian or Western). Credible and editorial, never sensational, "
        f"exploitative, or caricatured into a real recognizable person. {aspect}."
    )


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logger() -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    today_pht = dt.datetime.now(PHT).strftime("%Y-%m-%d")
    log_path = LOGS_DIR / f"image-generation-{today_pht}.log"

    # Force stdout/stderr to UTF-8 so log messages with non-ASCII content
    # don't crash on Windows cp1252 consoles.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    logger = logging.getLogger("image_generation")
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
# ClickUp REST client (minimal: read tasks, update fields, attach files,
# add comments, set status)
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
        if (task.get("list") or {}).get("id") != CLICKUP_LIST_ID:
            raise RuntimeError(
                f"Task {task_id} is on list "
                f"{(task.get('list') or {}).get('id')}, not {CLICKUP_LIST_ID}. "
                "Refusing to proceed."
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
                    "include_closed": "false",
                    "page": page,
                },
                timeout=30,
            )
            r.raise_for_status()
            tasks = r.json().get("tasks", [])
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

    def attach_image(self, task_id: str, image_path: Path) -> dict[str, str] | None:
        """Upload a PNG as a task attachment. Returns
        {'id': <attachment_id>, 'url': <hosted_https_url>} on success, or
        None on any failure. The hosted URL is the value the publisher
        will download from on the other side of the ClickUp pipe — it is
        the source of truth for the task's image."""
        try:
            with image_path.open("rb") as fh:
                files = {"attachment": (image_path.name, fh, "image/png")}
                r = requests.post(
                    f"{self.BASE}/task/{task_id}/attachment",
                    headers={"Authorization": self.session.headers["Authorization"]},
                    files=files,
                    timeout=120,
                )
            if r.status_code in (200, 201):
                body = r.json()
                aid = body.get("id")
                aurl = body.get("url") or body.get("url_w_query") or ""
                if aurl:
                    return {"id": str(aid or ""), "url": str(aurl)}
                self.log.warning(
                    "Attachment to %s returned no usable url: %r",
                    task_id, body,
                )
                return None
            self.log.warning(
                "Could not attach %s to %s: HTTP %d %s",
                image_path.name, task_id, r.status_code, r.text[:200],
            )
            return None
        except Exception as e:  # never let attachment failure kill the run
            self.log.warning("Attachment exception on %s: %s", task_id, e)
            return None


# ---------------------------------------------------------------------------
# OpenAI client wrappers
# ---------------------------------------------------------------------------


class OpenAIImageClient:
    """Wrapper for OpenAI gpt-image-1 (generation) and gpt-4o (vision QA)."""

    def __init__(
        self,
        api_key: str,
        image_model: str,
        vision_model: str,
        logger: logging.Logger,
    ) -> None:
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key)
        self.image_model = image_model
        self.vision_model = vision_model
        self.log = logger

    def generate(self, prompt: str, size: str) -> bytes:
        """Generate one image. Returns PNG bytes. Raises on failure."""
        if size not in VALID_SIZES:
            raise ValueError(
                f"Invalid size {size!r}; must be one of {sorted(VALID_SIZES)}"
            )
        response = self.client.images.generate(
            model=self.image_model,
            prompt=prompt,
            size=size,
            quality="high",
            n=1,
        )
        if not response.data:
            raise RuntimeError("OpenAI returned no image data")
        item = response.data[0]
        # gpt-image-1 returns base64 by default
        if item.b64_json:
            return base64.b64decode(item.b64_json)
        # Some configurations return a URL instead
        if item.url:
            r = requests.get(item.url, timeout=120)
            r.raise_for_status()
            return r.content
        raise RuntimeError("OpenAI response had neither b64_json nor url")

    def qa_review(self, image_bytes: bytes,
                  archetype: str | None = None) -> dict[str, Any]:
        """Send the FINAL composited image to gpt-4o for brand-rules QA.
        Returns {'pass': bool, 'violations': [list of strings]}.

        Same scene-only QA prompt for every archetype — the model renders
        no text in any archetype, so the legacy text-quality branch has
        been retired. On hard error, returns
        {'pass': False, 'violations': ['QA call failed: ...']}.

        `archetype` is accepted for signature compatibility and logging
        but does not affect the prompt selection.
        """
        b64 = base64.b64encode(image_bytes).decode("ascii")
        qa_prompt = self._scene_only_qa_prompt()
        return self._run_qa(b64, qa_prompt)


    def _run_qa(self, b64: str, qa_prompt: str) -> dict[str, Any]:
        try:
            response = self.client.chat.completions.create(
                model=self.vision_model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": qa_prompt},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/png;base64,{b64}"
                        }},
                    ],
                }],
                response_format={"type": "json_object"},
                timeout=60,
            )
            raw = response.choices[0].message.content or "{}"
            parsed = json.loads(raw)
            return {
                "pass": bool(parsed.get("pass", False)),
                "violations": list(parsed.get("violations") or []),
            }
        except Exception as e:
            self.log.warning("QA call failed: %s", e)
            return {"pass": False, "violations": [f"QA call failed: {e}"]}


    def _scene_only_qa_prompt(self) -> str:
        return (
            "You are reviewing an AI-generated image for The Filipino "
            "Standard, a Philippine governance-reform commentary page. The "
            "pipeline generates text-free imagery only; ALL text (any "
            "headline overlay if present, plus the watermark) has been "
            "added deterministically AFTER generation. You may see overlay "
            "text in the image; that text is correct by construction - do "
            "NOT evaluate its spelling, formatting, or layout. Your job is "
            "to evaluate the SCENE the model produced underneath.\n\n"
            "Apply these five rules strictly:\n\n"
            "1. NO RECOGNIZABLE REAL-PERSON LIKENESSES. Generic types and "
            "caricatures are fine. A face that reads as a specific real "
            "individual (any politician, celebrity, business figure, or "
            "other public person) is a violation - this includes near-"
            "likeness 'looks like Marcos / Duterte / Robredo / Quiboloy' "
            "cases.\n"
            "2. AUTHENTIC FILIPINO SUBJECTS. If people appear, their facial "
            "features (Malay/mestizo features), clothing, and setting must "
            "read as specifically Filipino. NOT generic East-Asian, NOT "
            "generic Western. People-free scenes pass this rule trivially.\n"
            "3. NO INCIDENTAL RENDERED TEXT. Signs, documents, screens, "
            "name tags, licence plates, watermarks, page numbers - all "
            "such surfaces must be blank or illegibly abstract. Garbled, "
            "half-formed, or invented words anywhere on a prop are a "
            "violation. Pillow overlay text (which sits cleanly above the "
            "scene) is exempt - only flag text the MODEL rendered into the "
            "scene itself.\n"
            "4. NOT SENSATIONAL, EXPLOITATIVE, OR CLICKBAIT. Credible "
            "editorial register. No gore, no breathless framing, no "
            "exaggerated 'tabloid' staging, no exploitation of distress, "
            "no over-stylization that reads as a commercial ad rather than "
            "civic commentary.\n"
            "5. ROUGHLY MATCHES THE REQUESTED STYLE REGISTER. The scene "
            "should read in the style the prompt asked for (cinematic / "
            "documentary / oil painting / monochrome / mythic / surreal / "
            "vintage editorial cartoon / hopeful dawn). A cartoon arriving "
            "as a glossy photograph, or vice versa, is a violation.\n\n"
            "Reply ONLY with valid JSON in this exact shape:\n"
            "{\"pass\": true, \"violations\": []}\n"
            "OR\n"
            "{\"pass\": false, \"violations\": [\"specific violation 1\", "
            "\"specific violation 2\"]}\n\n"
            "If all rules are satisfied, return pass=true with an empty "
            "list. Otherwise return pass=false and list each violation "
            "concretely.\n\n"
            "CRITICAL: Each violation string must describe what is "
            "actually wrong in the image, not restate the rule. Bad "
            "violation strings (do NOT produce these): 'No recognizable "
            "real-person likenesses', 'Subjects must look authentically "
            "Filipino'. These are rule statements, not violations. Good "
            "violation strings: 'A face resembling President Marcos "
            "appears on the central podium', 'The crowd has generic "
            "East-Asian features rather than Filipino-specific features "
            "(no mestizo skin tones, wrong clothing for a Philippine "
            "setting)', 'A street sign in the background contains the "
            "garbled word \"MERLACO\"'. Each violation must say what you "
            "see, where, and why it breaks the rule - not just name the "
            "rule."
        )


    # ------------------------------------------------------------------
    # Text-integrity QA (dedicated stage, separate from scene QA)
    # ------------------------------------------------------------------

    def text_integrity_review(
        self,
        image_bytes: bytes,
        archetype: str,
        text_in_image: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Text-integrity check on the RAW MODEL OUTPUT (pre-Pillow).
        Returns {'pass': bool, 'violations': [strings]}.

        Same zero-tolerance text-integrity prompt for every archetype:
        the model is told to render text-free imagery, and any rendered
        letterforms in the raw model output are a hard violation.
        Pillow overlays are added AFTER this check runs.

        `archetype` and `text_in_image` are accepted for signature
        compatibility and sidecar logging but do not affect the prompt.
        """
        b64 = base64.b64encode(image_bytes).decode("ascii")
        qa_prompt = self._zero_text_integrity_prompt()
        return self._run_qa(b64, qa_prompt)


    def _zero_text_integrity_prompt(self) -> str:
        """Default + satirical_meme path: zero-tolerance text check on
        the raw model output. The Pillow watermark / Anton meme text are
        NOT in this image yet; treat any rendered text or quasi-text as
        a violation."""
        return (
            "You are inspecting an AI-generated image for HIDDEN TEXT. "
            "The pipeline generates text-free imagery; text is added by "
            "a separate Pillow step AFTER you see this image. THIS image "
            "is the raw model output BEFORE that step - it must contain "
            "ABSOLUTELY ZERO rendered text of any kind.\n\n"
            "Inspect EVERY region carefully, including:\n"
            "- The center / main subject area\n"
            "- Background props (signs, walls, screens, papers, name "
            "tags, license plates, banners, posters, store fronts)\n"
            "- The bottom strip (a watermark will be added here later; "
            "the model has been known to pre-paint text-shapes that "
            "later sit under the watermark scrim - flag them)\n"
            "- The top edge, side edges, and corners\n"
            "- Anything resembling letterforms even if the words aren't "
            "fully coherent\n\n"
            "ANY of the following is a hard violation:\n"
            "* Readable words, numbers, or letters anywhere\n"
            "* Quasi-readable letterforms or text-shaped marks (e.g. "
            "garbled half-letters that imply text, sequences of "
            "letter-shapes the eye reads as a word even if the word "
            "isn't real)\n"
            "* Watermarks, signatures, page numbers, store names, brand "
            "names, document text, phone screen UI, license plates, "
            "name tags, captions, hashtags - i.e. ALPHABETIC text\n"
            "* Stray letterforms that look like intentional letters "
            "even if not coherent words\n\n"
            "NOT TEXT (do NOT flag the following - they are decorative "
            "non-text elements and are permitted):\n"
            "* Heraldic symbols, civic emblems, ornamental seals, coats "
            "of arms, government insignia\n"
            "* Scales of justice, laurel wreaths, columns, sunbursts, "
            "stars, abstract medallions\n"
            "* Geometric ornaments, decorative borders, scrollwork, "
            "fleurons, vignette frames\n"
            "* Architectural details, banner shapes, ribbons (so long "
            "as no readable letters are rendered on them)\n"
            "* Flag iconography (stars, sun rays, stripes)\n"
            "Text means LETTERS, WORDS, and NUMBERS. Symbols and "
            "ornaments are not text. Only flag things you can read.\n\n"
            "Reply ONLY with valid JSON in this exact shape:\n"
            "{\"pass\": true, \"violations\": []}\n"
            "OR\n"
            "{\"pass\": false, \"violations\": [\"specific description "
            "1\", \"specific description 2\"]}\n\n"
            "CRITICAL: violation strings describe WHAT you see and "
            "WHERE, not the rule. Good: 'The shop sign on the left "
            "wall reads JOLY MART in red letters'. Good: 'Three "
            "garbled letterforms resembling word-fragments appear in "
            "the lower-right corner of the canvas, suggesting a "
            "watermark the model painted in'. Bad: 'No incidental "
            "text allowed'. Bad: 'The image must be text-free'."
        )


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


def parse_pht_date(task: dict[str, Any]) -> str:
    sp = field_value(task, FIELD_SCHEDULED_PUBLISH)
    if sp:
        try:
            ms = int(sp)
            utc_dt = dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc)
            return utc_dt.astimezone(PHT).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            pass
    return dt.datetime.now(PHT).strftime("%Y-%m-%d")


def slug_from_task_name(name: str) -> str:
    name = re.sub(r"^\d{4}-\d{2}-\d{2}\s+", "", name)
    name = re.sub(r"\s+(FB|IG|TH|RD)$", "", name, flags=re.IGNORECASE)
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return s[:60] or "untitled"


def status_name(task: dict[str, Any]) -> str:
    return (task.get("status") or {}).get("status", "").lower()


def get_archetype(task: dict[str, Any]) -> str:
    """Read the archetype dropdown value. Defaults to editorial_allegory."""
    v = field_value(task, FIELD_ARCHETYPE)
    if v is None:
        return "editorial_allegory"
    # ClickUp dropdown values come back either as a string (option id) or
    # sometimes as an int index, depending on the API version. Handle both.
    if isinstance(v, str) and v in OPTION_ID_TO_ARCHETYPE:
        return OPTION_ID_TO_ARCHETYPE[v]
    if isinstance(v, int) or (isinstance(v, str) and v.isdigit()):
        idx = int(v)
        ordered = [
            "editorial_allegory", "ph_vs_nz_split", "satirical_meme",
            "constitutional_quote", "pain_point",
        ]
        if 0 <= idx < len(ordered):
            return ordered[idx]
    # Last resort: if v is the name itself
    if isinstance(v, str) and v in ARCHETYPES:
        return v
    return "editorial_allegory"


def get_style(task: dict[str, Any], archetype: str) -> str:
    """Read the style dropdown value and resolve it to a curated-palette
    style name. Falls back to a sensible default per archetype if the field
    is empty or unrecognized. resolve_style_descriptor downstream handles
    every legacy style name via LEGACY_STYLE_MAPPING.
    """
    # Sensible per-archetype default in the new 8-entry curated palette
    # (STYLE_DESCRIPTORS). resolve_style_descriptor will accept any of these
    # and any legacy alias.
    fallback_by_archetype: dict[str, str] = {
        "editorial_allegory":   "mythic",
        "ph_vs_nz_split":       "cinematic",
        "satirical_meme":       "old_cartoon",  # also forced in _build_light_prompt
        "constitutional_quote": "oil_painting",
        "pain_point":           "moody_documentary",
    }
    fallback = fallback_by_archetype.get(archetype, "cinematic")
    v = field_value(task, FIELD_STYLE)
    if v is None:
        return fallback
    # Legacy dropdown option-id form
    if isinstance(v, str) and v in OPTION_ID_TO_STYLE:
        return OPTION_ID_TO_STYLE[v]
    # Direct curated-palette or legacy name string
    if isinstance(v, str) and v.strip():
        if v in STYLE_DESCRIPTORS or v in LEGACY_STYLE_MAPPING:
            return v
    return fallback


def get_text_in_image(task: dict[str, Any]) -> dict[str, Any]:
    """Read the text_in_image custom field as a JSON object. Returns {} on
    empty or malformed input."""
    v = field_value(task, FIELD_TEXT_IN_IMAGE)
    if not v or not str(v).strip():
        return {}
    try:
        parsed = json.loads(str(v))
        if isinstance(parsed, dict):
            return parsed
        return {}
    except json.JSONDecodeError:
        return {}


def is_eligible_for_image(task: dict[str, Any]) -> bool:
    """A task is eligible if:
    - Image URL is empty AND
    - Status is not PUBLISHED or NEEDS-REVISION AND
    - Either a visual_subject or a text_in_image payload exists.
    """
    url = field_value(task, FIELD_IMAGE_URL)
    if url and str(url).strip():
        return False
    if status_name(task) in {"published", "needs-revision"}:
        return False
    visual = field_value(task, FIELD_IMAGE_PROMPT)
    text_in = field_value(task, FIELD_TEXT_IN_IMAGE)
    if (not visual or not str(visual).strip()) and (not text_in or not str(text_in).strip()):
        return False
    return True


# ---------------------------------------------------------------------------
# Fact-check (heuristic, last-resort)
# ---------------------------------------------------------------------------


def load_key_facts() -> str:
    if KEY_FACTS_PATH.exists():
        try:
            return KEY_FACTS_PATH.read_text(encoding="utf-8")
        except Exception:
            return ""
    return ""


def fact_check_warnings(visual_subject: str, text_in_image: dict[str, Any],
                        caption: str, key_facts: str) -> list[str]:
    """Heuristic last-resort fact-check. The upstream skill is responsible for
    the primary fact-check against key_facts.md. This script's check catches a
    few well-known pitfalls. Returns a list of warning strings (empty if all
    clear). Warnings are non-fatal; they get logged and recorded in the sidecar.
    """
    warnings: list[str] = []
    combined_text = " ".join(filter(None, [
        visual_subject,
        json.dumps(text_in_image) if text_in_image else "",
        caption,
    ]))

    # Check 1: NZ$364 / P13,000 must be described as ONE dividend, not two
    if (re.search(r"NZ\$\s*364\b", combined_text)
            or re.search(r"P\s*13[,\s]?000\b", combined_text)):
        if re.search(r"two\s+dividends?", combined_text, re.IGNORECASE):
            warnings.append(
                "Possible factual error: 'NZ$364' / 'P13,000' references the "
                "Vector ONE dividend, not two. Verify against context/key_facts.md."
            )

    # Check 2: Meralco ownership percentages (MPIC ~50.4%, JG Summit ~29.5%).
    # Bridge uses [^%\n\d] so the gap between label and number cannot contain
    # digits or the % sign. This prevents the regex from greedily eating
    # "50.4" and backtracking to capture just "4" or "0.4".
    for label, target_pct, tolerance in [
        ("MPIC",         50.4, 1.5),
        ("JG Summit",    29.5, 1.5),
    ]:
        m = re.search(
            rf"{re.escape(label)}[^%\n\d]{{0,40}}(\d+(?:\.\d+)?)\s*%",
            combined_text, re.IGNORECASE,
        )
        if m:
            pct = float(m.group(1))
            if abs(pct - target_pct) > tolerance:
                warnings.append(
                    f"Possible factual error: {label} stake in Meralco is "
                    f"~{target_pct}%; got {pct}%. Verify against key_facts.md."
                )

    # Check 3: Entrust / Vector (~75.1%). Same [^%\n\d] bridge fix as above.
    if re.search(r"\bEntrust\b", combined_text) and re.search(r"\bVector\b", combined_text):
        m = re.search(
            r"Entrust[^%\n\d]{0,30}(\d+(?:\.\d+)?)\s*%", combined_text,
            re.IGNORECASE,
        )
        if m:
            pct = float(m.group(1))
            if abs(pct - 75.1) > 1.5:
                warnings.append(
                    f"Possible factual error: Entrust holds ~75.1% of Vector; "
                    f"got {pct}%. Verify against key_facts.md."
                )

    # Check 4: Em dash slipping into text_in_image (rendered text).
    # The character constant uses a Unicode escape so this source file
    # contains zero literal em dashes per the brand-rules contract.
    rendered_text = " ".join(str(v) for v in text_in_image.values() if v)
    if "\u2014" in rendered_text or " -- " in rendered_text:
        warnings.append(
            "Em dash detected in text_in_image. The brand rules forbid em "
            "dashes in rendered text; use hyphen or comma."
        )

    return warnings


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------


def save_outputs(
    image_bytes: bytes,
    sidecar: dict[str, Any],
    task: dict[str, Any],
) -> tuple[Path, Path]:
    """Write the rendered PNG + sidecar JSON to disk with a versioned
    filename. Prior renders are NEVER overwritten - each invocation gets
    its own timestamped output. The newest file is the live one; older
    files remain on disk for audit and recovery.

    Filename pattern:
        YYYY-MM-DD-<slug>.rYYYYMMDDTHHMMSS.png    (the render)
        YYYY-MM-DD-<slug>.rYYYYMMDDTHHMMSS.json   (the sidecar)

    The 'r' prefix on the render timestamp distinguishes it visually from
    the leading publish-date. If a same-second collision happens (extremely
    rare), the timestamp gains a millisecond suffix.
    """
    OUTPUT_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    date_str = parse_pht_date(task)
    slug = slug_from_task_name(task.get("name", "untitled"))
    now_pht = dt.datetime.now(PHT)
    render_ts = now_pht.strftime("rendered-%Y%m%dT%H%M%S")
    base = f"{date_str}-{slug}.{render_ts}"
    png_path  = OUTPUT_IMAGES_DIR / f"{base}.png"
    json_path = OUTPUT_IMAGES_DIR / f"{base}.json"

    # If a same-second collision occurs (unlikely but possible on a tight
    # retry loop), tack on milliseconds to keep the writes non-destructive.
    if png_path.exists() or json_path.exists():
        ms = now_pht.strftime("%f")[:3]
        base = f"{date_str}-{slug}.{render_ts}-{ms}"
        png_path  = OUTPUT_IMAGES_DIR / f"{base}.png"
        json_path = OUTPUT_IMAGES_DIR / f"{base}.json"

    png_path.write_bytes(image_bytes)
    json_path.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False),
                          encoding="utf-8")
    return png_path, json_path


def process_task(
    task: dict[str, Any],
    clickup: ClickUpClient,
    openai_client: OpenAIImageClient,
    key_facts: str,
    logger: logging.Logger,
    locked_tasks: dict[str, dict[str, Any]] | None = None,
) -> bool:
    """Generate, QA, and attach an image for one task. Returns True on success."""
    task_id   = task["id"]
    task_name = task.get("name", "(unnamed)")

    # === HARD LOCK CHECK ===
    # Tasks in config/locked_tasks.json are permanently protected. We refuse
    # to process them even with explicit --task invocation. This is a
    # non-overridable guard against the destructive re-run failure mode.
    locked = locked_tasks or {}
    if task_id in locked:
        entry = locked[task_id]
        logger.error(
            'Task %s "%s" is PERMANENTLY LOCKED and will NOT be processed. '
            'Reason: %s. To run the same archetype on fresh material, '
            'create a new task instead. To unlock, edit '
            'config/locked_tasks.json (intentional human action).',
            task_id, task_name, entry.get("reason", "(no reason recorded)"),
        )
        return False

    # === DRAFT-PRESERVATION RULE ===
    # If the task is in `draft` status at the start of this run, treat it as
    # an iteration/development task and preserve that status regardless of
    # QA outcome. The script will NOT auto-flip a draft task to approved on
    # success, nor to needs-revision on failure. Draft tasks stay draft
    # until explicitly blessed by a human (publisher won't act on drafts
    # anyway, so the protection is belt-and-braces).
    started_status = ((task.get("status") or {}).get("status") or "").lower()
    preserve_status = started_status == "draft"
    if preserve_status:
        logger.info(
            'Task %s started in `draft` status - script will preserve it as '
            'draft regardless of QA outcome (no auto-flip to approved or '
            'needs-revision).',
            task_id,
        )

    # Inputs
    visual_subject = (field_value(task, FIELD_IMAGE_PROMPT) or "")
    visual_subject = str(visual_subject).strip()
    text_in_image  = get_text_in_image(task)
    archetype      = get_archetype(task)
    spec           = ARCHETYPES[archetype]
    # Style: prefer the style custom field on the task; fall back to the
    # archetype's default style.
    style = get_style(task, archetype)
    # Size: prefer text_in_image["format"] override, else archetype default
    size = str(text_in_image.get("format") or spec["default_size"]).strip()
    if size not in VALID_SIZES:
        logger.warning(
            'Task %s "%s" - invalid size override %r; falling back to %s',
            task_id, task_name, size, spec["default_size"],
        )
        size = spec["default_size"]

    # Idempotency
    if (field_value(task, FIELD_IMAGE_URL) or "").strip():
        logger.info(
            'Task %s "%s" - Image URL already set, skipping',
            task_id, task_name,
        )
        return False
    if not visual_subject and not text_in_image:
        logger.info(
            'Task %s "%s" - neither Image Prompt (visual_subject) nor '
            'text_in_image are populated, skipping',
            task_id, task_name,
        )
        return False

    # Description (post caption, used for fact-check context only)
    caption_for_check = (
        task.get("text_content")
        or task.get("description")
        or ""
    )

    logger.info(
        'Task %s "%s" - archetype=%s, style=%s, size=%s, visual_subject=%d chars, '
        'text_in_image keys=%s',
        task_id, task_name, archetype, style, size,
        len(visual_subject), sorted(text_in_image.keys()),
    )

    # Pre-generation fact-check (last-resort heuristic; non-blocking)
    fc_warnings = fact_check_warnings(
        visual_subject, text_in_image, caption_for_check, key_facts,
    )
    for w in fc_warnings:
        logger.warning('Task %s fact-check: %s', task_id, w)

    # Single prompt path for every archetype: light prompt with no
    # text_in_image embedded. The model always produces a text-free scene;
    # Pillow stamps the burned-in text for the two text-bearing archetypes
    # (satirical_meme top/bottom, ph_vs_nz_split labels) and the watermark
    # for everyone.
    prompt = build_prompt(archetype, visual_subject, {}, style=style, size=size)

    # Attempt loop: initial + MAX_REGEN_RETRIES retries
    sidecar_attempts: list[dict[str, Any]] = []
    best_image_bytes: bytes | None = None
    best_qa: dict[str, Any] | None = None
    final_success = False

    for attempt_idx in range(1 + MAX_REGEN_RETRIES):
        logger.info(
            'Task %s - generation attempt %d/%d (model=%s)',
            task_id, attempt_idx + 1, 1 + MAX_REGEN_RETRIES,
            openai_client.image_model,
        )
        try:
            model_bytes = openai_client.generate(prompt, size)
        except Exception as e:
            logger.error('Task %s - generation failed on attempt %d: %s',
                         task_id, attempt_idx + 1, e)
            sidecar_attempts.append({
                "attempt": attempt_idx + 1,
                "ok": False,
                "error": f"generation failed: {e}",
            })
            continue

        # === Stage 1: Text-integrity QA on the RAW MODEL OUTPUT ===
        # Same zero-tolerance text prompt for every archetype.
        text_integrity = openai_client.text_integrity_review(
            model_bytes, archetype=archetype, text_in_image=text_in_image,
        )
        logger.info(
            'Task %s - attempt %d TEXT-INTEGRITY: pass=%s, violations=%s',
            task_id, attempt_idx + 1,
            text_integrity["pass"], text_integrity["violations"],
        )

        # === Pillow overlay - dispatcher in image_text_overlay.py picks the
        # right branch (watermark only for 3 archetypes; split labels for
        # ph_vs_nz_split; meme top/bottom for satirical_meme).
        try:
            final_bytes = apply_text_for_archetype_to_bytes(
                model_bytes, archetype, text_in_image,
            )
        except Exception as e:
            logger.warning(
                'Task %s - text overlay failed on attempt %d: %s '
                '(continuing with un-overlaid image)',
                task_id, attempt_idx + 1, e,
            )
            final_bytes = model_bytes

        # === Layout check: deterministic geometry, zero API cost.
        # Verifies text/watermark zones are within safe margins and do not
        # overlap. A failure is a CODE BUG (zone constants drifted); the
        # caller refuses to ship the image and quarantines the task.
        try:
            layout_check = verify_layout(
                Image.open(BytesIO(final_bytes)), archetype, text_in_image,
            )
        except Exception as e:
            layout_check = {
                "ok": False,
                "violations": [f"verify_layout crashed: {e}"],
                "zones": {},
                "canvas": [0, 0],
            }
        logger.info(
            'Task %s - attempt %d LAYOUT CHECK: ok=%s, violations=%s',
            task_id, attempt_idx + 1,
            layout_check["ok"], layout_check["violations"],
        )

        # === Stage 2: Scene QA on the FINAL COMPOSITED IMAGE ===
        scene_qa = openai_client.qa_review(final_bytes, archetype=archetype)
        logger.info(
            'Task %s - attempt %d SCENE QA: pass=%s, violations=%s',
            task_id, attempt_idx + 1,
            scene_qa["pass"], scene_qa["violations"],
        )

        # === Combined pass requires BOTH QA stages AND the layout check ===
        overall_pass = (
            bool(text_integrity["pass"])
            and bool(scene_qa["pass"])
            and bool(layout_check["ok"])
        )
        combined_violations: list[str] = []
        for v in (text_integrity.get("violations") or []):
            combined_violations.append(f"[text-integrity] {v}")
        for v in (scene_qa.get("violations") or []):
            combined_violations.append(f"[scene-qa] {v}")
        for v in (layout_check.get("violations") or []):
            combined_violations.append(f"[layout] {v}")
        sidecar_attempts.append({
            "attempt": attempt_idx + 1,
            "ok": True,
            "qa_pass": overall_pass,
            "qa_violations": combined_violations,
            "text_integrity": text_integrity,
            "scene_qa": scene_qa,
            "layout_check": layout_check,
        })
        best_image_bytes = final_bytes  # keep latest even if QA failed
        best_qa = {"pass": overall_pass, "violations": combined_violations}
        if overall_pass:
            final_success = True
            break

    # All attempts done
    if not best_image_bytes:
        logger.error('Task %s - no image was successfully generated', task_id)
        try:
            if not preserve_status:
                clickup.set_status(task_id, "needs-revision")
            else:
                logger.info(
                    'Task %s - skipping status move to needs-revision '
                    '(draft-preservation rule)', task_id,
                )
            clickup.add_comment(
                task_id,
                "Image generation failed: no image produced after "
                f"{1 + MAX_REGEN_RETRIES} attempts. See "
                f"logs/image-generation log for details.",
            )
        except requests.HTTPError:
            pass
        return False

    # Build sidecar
    sidecar = {
        "task_id": task_id,
        "task_name": task_name,
        "archetype": archetype,
        "style": style,
        "size": size,
        "image_model": openai_client.image_model,
        "vision_model": openai_client.vision_model,
        "visual_subject": visual_subject,
        "text_in_image": text_in_image,
        "fact_check_warnings": fc_warnings,
        "prompt": prompt,
        "attempts": sidecar_attempts,
        "final_qa_pass": bool(best_qa and best_qa.get("pass")),
        "final_qa_violations": (best_qa or {}).get("violations", []),
        "generated_at_pht": dt.datetime.now(PHT).isoformat(),
    }

    png_path, json_path = save_outputs(best_image_bytes, sidecar, task)
    logger.info('Task %s - saved %s', task_id, png_path.name)
    logger.info('Task %s - sidecar %s', task_id, json_path.name)

    if not final_success:
        # All attempts failed QA.
        violations_summary = "; ".join((best_qa or {}).get("violations") or [])
        if preserve_status:
            logger.warning(
                'Task %s - all %d attempts failed QA; preserving `draft` '
                'status (draft-preservation rule). Final violations: %s',
                task_id, 1 + MAX_REGEN_RETRIES, violations_summary,
            )
        else:
            logger.warning(
                'Task %s - all %d attempts failed QA; moving to '
                'NEEDS-REVISION', task_id, 1 + MAX_REGEN_RETRIES,
            )
        try:
            if not preserve_status:
                clickup.set_status(task_id, "needs-revision")
            clickup.add_comment(
                task_id,
                f"Image generation completed but vision QA failed after "
                f"{1 + MAX_REGEN_RETRIES} attempts. Final violations: "
                f"{violations_summary}. See sidecar JSON at "
                f"{json_path.name}. The image is at {png_path.name} if "
                "manual review or a Pillow patch (scripts/image_text_overlay.py) "
                "is appropriate.",
            )
        except requests.HTTPError as e:
            logger.warning('Could not transition task status: %s', e)
        return False

    # QA passed: attach the PNG to the task FIRST, then write the HOSTED
    # https URL of the attachment into FIELD_IMAGE_URL. This is the file://
    # gap fix: the publisher (and any other consumer) can now fetch the
    # image over HTTPS, regardless of where it runs, instead of needing
    # the generator's local disk to be visible.
    attach = clickup.attach_image(task_id, png_path)
    if not attach or not attach.get("url"):
        logger.error(
            'Task %s - attachment upload failed; refusing to write FIELD_IMAGE_URL. '
            'The publisher will fall back to scanning task attachments. '
            'Image still saved locally at %s for manual recovery.',
            task_id, png_path,
        )
        # Do NOT write a file:// URI. The publisher's fallback handles
        # missing Image URL by reading the task's attachments list.
        return True  # generation+QA succeeded; only the URL write failed

    hosted_url = attach["url"]
    try:
        clickup.set_custom_field(task_id, FIELD_IMAGE_URL, hosted_url)
        logger.info(
            'Task %s - SUCCESS - attachment_id=%s, FIELD_IMAGE_URL=%s',
            task_id, attach.get("id"), hosted_url,
        )
    except requests.HTTPError as e:
        logger.error('Task %s - could not write Image URL field: %s',
                     task_id, e)
        # Attachment is still there; publisher fallback will find it.
        return True
    return True


def cmd_pending(clickup, openai_client, key_facts, locked_tasks, logger) -> int:
    logger.info("=== --pending mode: scanning list %s ===", CLICKUP_LIST_ID)
    try:
        tasks = clickup.list_tasks()
    except requests.HTTPError as e:
        logger.error("Failed to list tasks: %s", e)
        return 1
    # Drop locked tasks BEFORE eligibility check so they never even enter
    # the candidate set for scheduled runs.
    pre_lock = len(tasks)
    tasks = [t for t in tasks if t.get("id") not in locked_tasks]
    if pre_lock != len(tasks):
        logger.info("Excluded %d locked task(s) from candidate set",
                    pre_lock - len(tasks))
    eligible = [t for t in tasks if is_eligible_for_image(t)]
    logger.info("Found %d total tasks; %d eligible for image generation",
                len(tasks), len(eligible))
    successes = 0
    for task in eligible:
        try:
            if process_task(task, clickup, openai_client, key_facts, logger,
                            locked_tasks=locked_tasks):
                successes += 1
        except Exception as e:
            logger.exception('Task %s unhandled error: %s',
                             task.get('id'), e)
    logger.info("=== --pending complete: %d/%d succeeded ===",
                successes, len(eligible))
    return 0


def cmd_task(task_id, clickup, openai_client, key_facts, locked_tasks, logger) -> int:
    logger.info("=== --task mode: %s ===", task_id)

    # Refuse locked tasks before any API or processing work.
    if task_id in locked_tasks:
        entry = locked_tasks[task_id]
        logger.error(
            'Task %s is PERMANENTLY LOCKED and will NOT be processed. '
            'Reason: %s. To run a fresh test of the same archetype, '
            'create a new task. To unlock, edit config/locked_tasks.json '
            '(intentional human action).',
            task_id, entry.get("reason", "(no reason recorded)"),
        )
        return 3  # distinct exit code so callers can detect refusal

    try:
        task = clickup.get_task(task_id)
    except requests.HTTPError as e:
        logger.error("Failed to fetch task %s: %s", task_id, e)
        return 1
    except RuntimeError as e:
        logger.error("Refusing to proceed: %s", e)
        return 2
    ok = process_task(task, clickup, openai_client, key_facts, logger,
                      locked_tasks=locked_tasks)
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate images for ClickUp tasks via OpenAI gpt-image-1."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pending", action="store_true",
                       help="Scan ClickUp list for tasks needing images.")
    group.add_argument("--task", metavar="TASK_ID",
                       help="Generate images for a specific ClickUp task.")
    args = parser.parse_args()

    logger = setup_logger()

    if not ENV_PATH.exists():
        logger.error(".env file not found at %s", ENV_PATH)
        return 10
    load_dotenv(ENV_PATH)

    openai_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    clickup_token = (os.environ.get("CLICKUP_API_TOKEN") or "").strip()
    image_model = (os.environ.get("OPENAI_IMAGE_MODEL") or "").strip() or DEFAULT_IMAGE_MODEL
    vision_model = (os.environ.get("OPENAI_VISION_MODEL") or "").strip() or DEFAULT_VISION_MODEL

    missing = [
        name for name, val in {
            "OPENAI_API_KEY": openai_key,
            "CLICKUP_API_TOKEN": clickup_token,
        }.items() if not val
    ]
    if missing:
        logger.error("Missing required env vars: %s", ", ".join(missing))
        return 11

    logger.info("Using image model: %s", image_model)
    logger.info("Using vision model (QA): %s", vision_model)
    logger.info("ClickUp list (locked): %s", CLICKUP_LIST_ID)

    try:
        openai_client = OpenAIImageClient(
            api_key=openai_key,
            image_model=image_model,
            vision_model=vision_model,
            logger=logger,
        )
    except Exception as e:
        logger.error("Could not initialize OpenAI client: %s", e)
        return 12

    clickup = ClickUpClient(api_token=clickup_token, logger=logger)
    key_facts = load_key_facts()
    if key_facts:
        logger.info("Loaded key_facts.md (%d chars) for fact-check context",
                    len(key_facts))
    else:
        logger.info("No key_facts.md found; fact-check uses heuristic rules only")

    locked_tasks = load_locked_task_ids()
    if locked_tasks:
        logger.info(
            "Loaded %d locked task(s) from %s: %s",
            len(locked_tasks), LOCKED_TASKS_PATH.name,
            sorted(locked_tasks.keys()),
        )
    else:
        logger.info("No locked tasks configured")

    if args.pending:
        return cmd_pending(clickup, openai_client, key_facts, locked_tasks, logger)
    return cmd_task(args.task, clickup, openai_client, key_facts, locked_tasks, logger)


if __name__ == "__main__":
    sys.exit(main())
