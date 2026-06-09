"""
factcheck.py - Independent fact-check gate (Gemini + Google Search grounding).

A DIFFERENT model than the writer (the writer is Claude; this is Gemini with live
Google Search) re-verifies the caption's load-bearing factual claims against
current web results and returns PASS / FAIL. It is FAIL-CLOSED: any error, missing
key, or unverifiable load-bearing claim => FAIL (do not publish). Used as a
mandatory gate in publish_direct.py before any live post.

CLI (for testing):
  py factcheck.py --caption "..."        -> prints findings; exit 0 = PASS, 2 = FAIL
  py factcheck.py --caption-file post.txt

Env (.env): GEMINI_API_KEY  (optional FACTCHECK_MODEL, default gemini-2.5-flash)
"""
from __future__ import annotations

import truststore
truststore.inject_into_ssl()

import argparse
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

# Transient HTTP statuses worth retrying (overload / rate-limit / gateway).
# Everything else is treated as a hard fail (fail-closed).
_TRANSIENT = {408, 429, 500, 502, 503, 504}
_RETRY_DELAYS = (2, 5, 12)  # 1 initial try + 3 retries; ~19s worst-case added

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"

_PROMPT = (
    "You are a STRICT, independent fact-checker for The Filipino Standard, a "
    "Philippine governance and news-commentary page. The social media post below "
    "is about to be PUBLISHED. Using Google Search, verify EVERY load-bearing "
    "factual claim: statistics, percentages, peso/dollar figures, dates, named "
    "events, agencies, bills, court rulings, and any attributed quote. Confirm "
    "each is accurate and current.\n\n"
    "Rules:\n"
    "- If any load-bearing claim is false, fabricated, mis-stated, or you cannot "
    "find independent corroboration, the post FAILS.\n"
    "- Opinion, framing, and rhetoric are fine - you judge FACTS only.\n"
    "- Be conservative: when a factual claim cannot be verified, FAIL.\n\n"
    "Respond in EXACTLY this format:\n"
    "VERDICT: PASS\n"
    "(or)\n"
    "VERDICT: FAIL\n"
    "Then 2-6 bullet lines, one per claim checked: the claim and the result "
    "(verified + source / not found / contradicted).\n\n"
    "POST TO CHECK:\n\n{caption}"
)


def check_caption(caption: str, logger=None) -> tuple[bool, str]:
    """Return (passed, human-readable report). Fail-closed on any error."""
    key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not key:
        return False, "FACT-CHECK ERROR: GEMINI_API_KEY missing (fail-closed)."
    model = (os.environ.get("FACTCHECK_MODEL") or "").strip() or "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "contents": [{"parts": [{"text": _PROMPT.format(caption=caption)}]}],
        "tools": [{"google_search": {}}],
    }
    # Retry transient overloads (e.g. Gemini HTTP 503) with backoff so a brief
    # outage doesn't needlessly block a good post. Still FAIL-CLOSED: if every
    # attempt fails, we refuse to publish.
    r = None
    last_err = ""
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            r = requests.post(
                url,
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json=body, timeout=120,
            )
        except requests.RequestException as e:
            r, last_err = None, f"request error: {e}"
        else:
            if r.status_code == 200:
                break
            if r.status_code not in _TRANSIENT:
                # Non-transient (e.g. 400/401/403) - no point retrying.
                return False, f"FACT-CHECK ERROR HTTP {r.status_code} (fail-closed): {r.text[:400]}"
            last_err = f"HTTP {r.status_code}: {r.text[:200]}"
        if attempt < len(_RETRY_DELAYS):
            if logger is not None:
                logger.warning("Fact-check transient error (%s); retry %d/%d in %ds...",
                               last_err, attempt + 1, len(_RETRY_DELAYS), _RETRY_DELAYS[attempt])
            time.sleep(_RETRY_DELAYS[attempt])
        else:
            return False, f"FACT-CHECK ERROR after {len(_RETRY_DELAYS)} retries (fail-closed): {last_err}"
    try:
        data = r.json()
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, ValueError, TypeError) as e:
        return False, f"FACT-CHECK ERROR parsing response (fail-closed): {e}"
    if not text:
        return False, "FACT-CHECK ERROR: empty response (fail-closed)."
    upper = text.upper()
    passed = ("VERDICT: PASS" in upper) and ("VERDICT: FAIL" not in upper)
    return passed, text


def main() -> int:
    p = argparse.ArgumentParser(description="Independent fact-check gate (Gemini + Search).")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--caption")
    g.add_argument("--caption-file")
    args = p.parse_args()
    load_dotenv(ENV_PATH)
    caption = (args.caption if args.caption is not None
               else Path(args.caption_file).read_text(encoding="utf-8")).strip()
    passed, report = check_caption(caption)
    print(report)
    print(f"\n=== FACT-CHECK: {'PASS' if passed else 'FAIL'} ===")
    return 0 if passed else 2


if __name__ == "__main__":
    sys.exit(main())
