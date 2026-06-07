"""
_test_text_integrity_qa.py - One-shot validator for the path-aware text-integrity
QA stage added in scripts/generate_image.py.

Creates four synthetic test images and runs each through the new
text_integrity_review method. Reports pass/fail vs expected.

Four-corner test:
  1. Default/meme path:  clean blank-canvas PNG          -> expect PASS
  2. Default/meme path:  PNG with planted "SAMPLE TEXT"  -> expect FAIL
  3. CQ path:            PNG with canonical CQ text     -> expect PASS  (benchmark)
  4. CQ path:            PNG with misspelled+em-dash    -> expect FAIL

This is a single-purpose validation script. It does not write to ClickUp,
does not generate any images via gpt-image-1, does not touch any task on
the board. It only calls the gpt-4o vision endpoint for the QA evaluations.
"""
from __future__ import annotations

import truststore; truststore.inject_into_ssl()

import io
import logging
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import generate_image  # noqa: E402
from image_text_overlay import _resolve_font  # noqa: E402

load_dotenv(ROOT / ".env")

# Set up minimal logger
logging.basicConfig(level=logging.INFO,
                    format="[%(asctime)s] %(message)s")
log = logging.getLogger("text-integrity-test")


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def make_clean_blank() -> bytes:
    """Flat warm-grey canvas, no text. Default/meme expected PASS."""
    return _png_bytes(Image.new("RGB", (1024, 1024), (100, 90, 75)))


def make_with_sample_text() -> bytes:
    """Same canvas, with the words 'SAMPLE TEXT' drawn on it.
    Default/meme expected FAIL (model painted text in the scene)."""
    img = Image.new("RGB", (1024, 1024), (100, 90, 75))
    draw = ImageDraw.Draw(img)
    font = _resolve_font("display_serif", 96)  # Cinzel
    draw.text((300, 460), "SAMPLE TEXT", fill="white", font=font,
              stroke_width=3, stroke_fill="#0A0808")
    return _png_bytes(img)


# Canonical CQ text from the locked benchmark (86d30n0ne)
CQ_QUOTE_GOOD = (
    "Sovereignty resides in the people and all government authority "
    "emanates from them."
)
CQ_ATTRIBUTION_GOOD = "Article II Section 1 - 1987 Philippine Constitution"
CQ_FOOTER = "THE FILIPINO STANDARD"

CQ_QUOTE_MISSPELLED = (
    "Soverignty resides in the people and all government authority "
    "emanates from them."  # 'Soverignty' missing the 'e' after 'v'
)
CQ_ATTRIBUTION_EMDASH = (
    # Em dash (U+2014) injected between 'Section 1' and '1987'
    "Article II Section 1 " + chr(0x2014) + " 1987 Philippine Constitution"
)


def _draw_cq_image(quote: str, attribution: str) -> bytes:
    """Render a synthetic constitutional_quote-style image (portrait,
    centered serif text on a dim background) for QA testing.

    This is NOT going through the real pipeline; it's just a visual
    that puts the quote + attribution + footer into a frame for the
    QA model to read. The actual brand benchmark PNG is read separately
    below."""
    W, H = 1024, 1536
    img = Image.new("RGB", (W, H), (40, 30, 25))
    draw = ImageDraw.Draw(img)
    # Quote, centered, multiple lines
    q_font = _resolve_font("body_serif_italic", 56)
    a_font = _resolve_font("body_serif", 36)
    f_font = _resolve_font("display_serif", 42)

    def _wrap(text: str, font, max_w: int) -> list[str]:
        words = text.split()
        lines = []
        cur = []
        for w in words:
            test = " ".join(cur + [w])
            bbox = draw.textbbox((0, 0), test, font=font)
            if (bbox[2] - bbox[0]) <= max_w or not cur:
                cur.append(w)
            else:
                lines.append(" ".join(cur))
                cur = [w]
        if cur:
            lines.append(" ".join(cur))
        return lines

    def _centered(text: str, font, y: int, fill: str, stroke="#0A0808",
                  stroke_w: int = 2) -> int:
        bbox = draw.textbbox((0, 0), text, font=font)
        x = (W - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), text, font=font, fill=fill, stroke_width=stroke_w,
                  stroke_fill=stroke)
        return bbox[3] - bbox[1]

    # Lay quote out in the upper-middle band
    quote_lines = _wrap(quote, q_font, int(W * 0.85))
    y = 420
    for line in quote_lines:
        h = _centered(line, q_font, y, "#F5EBD1")
        y += int(h * 1.45)

    y += 40
    # Attribution
    for line in _wrap(attribution, a_font, int(W * 0.85)):
        h = _centered(line, a_font, y, "#C9B27E")
        y += int(h * 1.30)

    # Footer near bottom
    _centered(CQ_FOOTER, f_font, H - 110, "#E8D9B0", stroke_w=1)

    return _png_bytes(img)


def main() -> int:
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        print("ERROR: OPENAI_API_KEY missing from .env", file=sys.stderr)
        return 1

    client = generate_image.OpenAIImageClient(
        api_key=api_key,
        image_model=generate_image.DEFAULT_IMAGE_MODEL,
        vision_model=generate_image.DEFAULT_VISION_MODEL,
        logger=log,
    )

    # ---- Generate the four test PNGs (write to disk for review) ----
    out = ROOT / "output" / "text_integrity_test"
    out.mkdir(parents=True, exist_ok=True)

    cases = []

    # 1. Default/meme clean
    clean = make_clean_blank()
    (out / "01-default-clean.png").write_bytes(clean)
    cases.append(("01-default-clean", "pain_point", clean, None, True))

    # 2. Default/meme with planted text
    dirty = make_with_sample_text()
    (out / "02-default-dirty.png").write_bytes(dirty)
    cases.append(("02-default-dirty", "pain_point", dirty, None, False))

    # 3. CQ with canonical text (benchmark-quality content)
    cq_good = _draw_cq_image(CQ_QUOTE_GOOD, CQ_ATTRIBUTION_GOOD)
    (out / "03-cq-canonical.png").write_bytes(cq_good)
    cases.append((
        "03-cq-canonical", "constitutional_quote", cq_good,
        {"quote": CQ_QUOTE_GOOD,
         "attribution": CQ_ATTRIBUTION_GOOD,
         "footer": CQ_FOOTER},
        True,
    ))

    # 4. CQ with planted misspelling + em dash
    cq_bad = _draw_cq_image(CQ_QUOTE_MISSPELLED, CQ_ATTRIBUTION_EMDASH)
    (out / "04-cq-misspelled-emdash.png").write_bytes(cq_bad)
    cases.append((
        "04-cq-misspelled-emdash", "constitutional_quote", cq_bad,
        {"quote": CQ_QUOTE_GOOD,
         "attribution": CQ_ATTRIBUTION_GOOD,
         "footer": CQ_FOOTER},
        False,
    ))

    # 5. (Bonus) read-only check against the restored locked benchmark PNG
    #    to confirm the CQ check doesn't false-positive on the real brand
    #    benchmark. This does NOT modify the task or the file.
    bench_path = ROOT / "output" / "images" / \
        "2026-05-16-constitutional-awareness-test-sovereignty-quote.png"
    if bench_path.exists():
        bench_bytes = bench_path.read_bytes()
        cases.append((
            "05-cq-brand-benchmark-readonly", "constitutional_quote",
            bench_bytes,
            {"quote": CQ_QUOTE_GOOD,
             "attribution": CQ_ATTRIBUTION_GOOD,
             "footer": CQ_FOOTER},
            True,
        ))
        print(f"INFO: also testing against the locked benchmark "
              f"({bench_path.name}, READ-ONLY)")
    else:
        print("INFO: brand benchmark PNG not found, skipping bonus check")

    # ---- Run each case ----
    print()
    print("=" * 95)
    results = []
    for name, archetype, img_bytes, tii, expect_pass in cases:
        print(f"--- {name}  (archetype={archetype}, expect_pass={expect_pass}) ---")
        res = client.text_integrity_review(img_bytes, archetype=archetype,
                                            text_in_image=tii)
        got_pass = bool(res.get("pass"))
        outcome = "OK" if got_pass == expect_pass else "MISMATCH"
        print(f"  pass: {got_pass}  (expected: {expect_pass})  -> {outcome}")
        for v in res.get("violations", []) or []:
            print(f"  violation: {v}")
        results.append((name, expect_pass, got_pass, outcome))
        print()

    print("=" * 95)
    print("SUMMARY")
    print("=" * 95)
    for name, exp, got, outcome in results:
        print(f"  {outcome:<10} {name:<40} expected={exp}, got={got}")
    failed = sum(1 for _, exp, got, _ in results if got != exp)
    print()
    print(f"Result: {len(results) - failed}/{len(results)} matched expectations")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
