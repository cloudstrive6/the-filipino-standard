"""
image_text_overlay.py - Deterministic text rendering for The Filipino Standard.

The model (gpt-image-1) renders text-free imagery. EVERY text element in the
final image - headline, labels, quote, attribution, meme top/bottom text,
brand footer - is rendered here, deterministically, per archetype. This
permanently eliminates the text failure class (clipping, case errors, decimal
mistakes, dropped text, garbled prop text).

Public surface (used by scripts/generate_image.py):
  - apply_text_for_archetype_to_bytes(png_bytes, archetype, text_in_image)
        Main entry point. Returns PNG bytes with the per-archetype text
        layout applied and the brand footer stamped.
  - apply_text_for_archetype(img, archetype, text_in_image)
        Same, but on a PIL Image.
  - apply_brand_footer(img, ...) / apply_brand_footer_to_bytes(png_bytes, ...)
        Just the footer, exposed for back-compat and ad-hoc patching.

Per-archetype layout map:
  - pain_point          : headline (top) + footer
  - editorial_allegory  : headline (top) + footer
  - ph_vs_nz_split      : headline (top) + left_label + right_label + footer
  - constitutional_quote: quote (centered) + attribution (below) + footer
  - satirical_meme      : top_text (top) + bottom_text (mid-bottom) + footer

Footer typography:
  - Cinzel (variable, wght=700) - Trajan-style inscriptional Roman capitals
  - Warm cream / parchment fill (#E8D9B0)
  - Soft bottom gradient scrim (transparent fading to ~80% dark) so the
    footer reads on ANY palette: bright interiors, cool split-screens, dark
    documentary, orange cartoon. No hard solid bar.
  - Wide letter-spacing for that engraved-on-stone feel.

CLI (legacy patching tool, preserved):
  py image_text_overlay.py --input X --output Y --text "..." --position bottom-right
  py image_text_overlay.py --input X --output Y --spec spec.json
"""
from __future__ import annotations

import argparse
import json
import sys
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


SAFE_PADDING = 40  # pixels from any edge for anchored positions in the CLI

# ---------------------------------------------------------------------------
# Layout zones — single source of truth used by BOTH the renderers and
# verify_layout. All ratios are fractions of canvas height (or width as
# noted) so the same constants work at every supported aspect ratio.
# ---------------------------------------------------------------------------

# Satirical meme: top_text band, bottom_text band
ZONE_MEME_TOP_TOP_RATIO    = 0.04
ZONE_MEME_TOP_BOTTOM_RATIO = 0.20
ZONE_MEME_BOTTOM_TOP_RATIO    = 0.66
ZONE_MEME_BOTTOM_BOTTOM_RATIO = 0.83

# Ph-vs-NZ split: left/right labels. y_center sits ABOVE the watermark scrim.
ZONE_SPLIT_LABEL_Y_CENTER_RATIO = 0.80
ZONE_SPLIT_LABEL_FONT_H_RATIO   = 0.038

# Brand watermark scrim + text (matches apply_brand_footer's defaults).
ZONE_WATERMARK_SCRIM_TOP_RATIO   = 0.84
ZONE_WATERMARK_TEXT_BASELINE_RATIO = 0.962
ZONE_WATERMARK_TEXT_H_RATIO      = 0.032

# Safety margin every text bbox must clear from any canvas edge.
SAFE_MARGIN_PX = 24

# ---------------------------------------------------------------------------
# Brand font registry
# ---------------------------------------------------------------------------
#
# Each FONTS value is an ordered list of candidate paths; the first that
# loads wins. The bundled assets live under <repo>/assets/fonts/. System
# fallbacks come second so the pipeline degrades gracefully on machines
# where the bundled fonts went missing.

_THIS_FILE = Path(__file__).resolve()
_PROJECT_ROOT = _THIS_FILE.parent.parent
_FONT_DIR = _PROJECT_ROOT / "assets" / "fonts"

FONTS: dict[str, list[str]] = {
    # Sans-serif body, regular and bold - the legacy overlay path uses these
    "body": [
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
    ],
    "body_bold": [
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf",
    ],
    # Sans-serif display (legacy)
    "display": [
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ],
    "display_bold": [
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
    ],
    # Display serif - the brand's monumental face. Cinzel (Trajan-style
    # Roman capitals) is the canonical choice; fallbacks degrade to system
    # serifs in roughly descending Trajan-likeness.
    "display_serif": [
        str(_FONT_DIR / "Cinzel.ttf"),
        r"C:\Windows\Fonts\trajanpro.ttf",
        r"C:\Windows\Fonts\TRAJANPRO-BOLD.ttf",
        r"C:\Windows\Fonts\times.ttf",
        r"C:\Windows\Fonts\georgia.ttf",
    ],
    # Body serif - old-style serif paired with the display capitals
    "body_serif": [
        str(_FONT_DIR / "EBGaramond.ttf"),
        r"C:\Windows\Fonts\GARA.TTF",
        r"C:\Windows\Fonts\georgia.ttf",
        r"C:\Windows\Fonts\times.ttf",
    ],
    "body_serif_italic": [
        str(_FONT_DIR / "EBGaramond-Italic.ttf"),
        r"C:\Windows\Fonts\GARAIT.TTF",
        r"C:\Windows\Fonts\georgiai.ttf",
        r"C:\Windows\Fonts\timesi.ttf",
    ],
    # ---- Per-archetype display typefaces (overlays only; footer stays Cinzel)
    # pain_point headline: Oswald Bold (condensed display, photo-friendly)
    "headline_pain_point": [
        str(_FONT_DIR / "Oswald.ttf"),
        str(_FONT_DIR / "ArchivoBlack.ttf"),
        r"C:\Windows\Fonts\impact.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
    ],
    # ph_vs_nz_split headline + panel labels: Archivo Black (high-clarity)
    "headline_split": [
        str(_FONT_DIR / "ArchivoBlack.ttf"),
        str(_FONT_DIR / "Inter.ttf"),
        r"C:\Windows\Fonts\impact.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
    ],
    # satirical_meme top_text + bottom_text: Anton (impact-poster condensed)
    "headline_meme": [
        str(_FONT_DIR / "Anton.ttf"),
        str(_FONT_DIR / "BebasNeue.ttf"),
        r"C:\Windows\Fonts\impact.ttf",
    ],
    # constitutional_quote body: Playfair Display Bold (elegant, monumental)
    "quote_bold": [
        str(_FONT_DIR / "PlayfairDisplay.ttf"),
        r"C:\Windows\Fonts\georgia.ttf",
        r"C:\Windows\Fonts\times.ttf",
    ],
    # constitutional_quote attribution: Playfair Display SemiBold (smaller)
    "quote_attribution": [
        str(_FONT_DIR / "PlayfairDisplay.ttf"),
        r"C:\Windows\Fonts\georgia.ttf",
        r"C:\Windows\Fonts\times.ttf",
    ],
}

# Variable-font axis presets per FONTS key. Applied after load if the font
# supports the wght axis; ignored on static fonts.
FONT_VARIATIONS: dict[str, list[float]] = {
    "display_serif":         [700.0],          # Cinzel Bold
    "body_serif":            [400.0],          # EBGaramond Regular
    "body_serif_italic":     [400.0],          # EBGaramond Italic Regular
    "headline_pain_point":   [700.0],          # Oswald Bold (wght axis)
    "headline_split":        [20.0, 800.0],    # Inter at [opsz=20, wght=800] ExtraBold; no-op on ArchivoBlack (static)
    "quote_bold":            [700.0],          # PlayfairDisplay Bold
    "quote_attribution":     [500.0],          # PlayfairDisplay Medium
    # Anton / BebasNeue (headline_meme) are static - no variation needed
}


def _resolve_font(font_path: str | None, font_size: int) -> ImageFont.ImageFont:
    """Best-effort font loader. Accepts a FONTS key (e.g. 'display_serif')
    or a literal path. Falls back to PIL's bundled default if everything
    fails. Applies variable-font axes if the key is registered."""
    candidates: list[str] = []
    variation: list[float] | None = None
    if font_path:
        if font_path in FONTS:
            candidates.extend(FONTS[font_path])
            variation = FONT_VARIATIONS.get(font_path)
        else:
            candidates.append(font_path)
    # Generic fallbacks - tried last
    candidates.extend([
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\tahoma.ttf",
    ])
    for c in candidates:
        try:
            f = ImageFont.truetype(c, font_size)
            if variation is not None:
                try:
                    f.set_variation_by_axes(variation)
                except (OSError, AttributeError):
                    pass  # static font or unsupported Pillow version
            return f
        except (OSError, IOError):
            continue
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------


def _normalize_text(text: str) -> str:
    """Strip em / en dashes per brand rules. The brand never renders them."""
    return text.replace("—", "-").replace("–", "-")


def _measure_tracked_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    tracking_px: int = 0,
) -> int:
    """Width of `text` when rendered with `tracking_px` extra space between
    every character pair (same algorithm `_draw_centered_with_tracking` uses).
    Cheaper than measuring char-by-char with bbox calls because we don't
    care about the per-glyph rectangle - just the running total."""
    bbox = draw.textbbox((0, 0), text, font=font)
    base = bbox[2] - bbox[0]
    return base + tracking_px * max(0, len(text) - 1)


def _wrap_text_to_width(
    text: str, font: ImageFont.ImageFont, draw: ImageDraw.ImageDraw,
    max_width: int, tracking_px: int = 0,
) -> list[str]:
    """Word-wrap `text` to lines that fit `max_width` pixels (tracking-aware)."""
    words = text.split()
    lines: list[str] = []
    cur: list[str] = []
    for w in words:
        test = " ".join(cur + [w])
        if _measure_tracked_width(draw, test, font, tracking_px) <= max_width or not cur:
            cur.append(w)
        else:
            lines.append(" ".join(cur))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))
    return lines


def _draw_centered_with_tracking(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    fill: str,
    y_anchor: int,
    canvas_w: int,
    tracking_px: int = 0,
    stroke_width: int = 0,
    stroke_fill: str | None = None,
    anchor: str = "lt",
) -> None:
    """Draw `text` horizontally centered with manual per-character tracking.

    `y_anchor` is the y coordinate the anchor mode interprets:
      - "lt" (left/top): every glyph's bbox TOP is placed at y_anchor.
        Glyphs with different ascender heights end up on different
        baselines - cap tops, lowercase ascender tops, and dot tops all
        line up, but the visual baseline wanders. This is fine for ALL
        CAPS text (which has uniform ascender height) but produces an
        artificial "hard line across the tops" + wandering baseline
        when used on mixed-case body text.
      - "ls" (left/baseline): every glyph's BASELINE is placed at
        y_anchor. This is the typographically correct mode for mixed-
        case body text - all characters share one baseline, ascenders
        and descenders extend naturally above/below it. Use this for
        body serif rendering (e.g. the constitutional_quote
        attribution).

    Letter-spacing is achieved by drawing each glyph at a computed x;
    PIL has no native letter-spacing knob.
    """
    widths: list[int] = []
    for ch in text:
        if ch == " ":
            sb = draw.textbbox((0, 0), "M", font=font)
            widths.append(max(1, int((sb[2] - sb[0]) * 0.55)))
        else:
            cb = draw.textbbox((0, 0), ch, font=font)
            widths.append(cb[2] - cb[0])
    total_w = sum(widths) + tracking_px * max(0, len(text) - 1)
    x = (canvas_w - total_w) // 2
    full_bbox = draw.textbbox((0, 0), text, font=font)
    # When stroke_width is 0, omit the stroke params from the PIL call
    # entirely. Defensive against any version-specific Pillow side-effect
    # from passing stroke parameters at all.
    use_stroke = (stroke_width or 0) > 0
    # Fallback computation for legacy Pillow (anchor= unsupported): convert
    # the anchor + y_anchor into an equivalent top-y for an anchorless draw.
    if anchor == "ls":
        try:
            ascent, _ = font.getmetrics()
        except AttributeError:
            ascent = int(getattr(font, "size", 24) * 0.8)
        legacy_top = y_anchor - ascent
    else:  # "lt" or anything else - treat as top
        legacy_top = y_anchor - full_bbox[1]
    for ch, cw in zip(text, widths):
        try:
            if use_stroke:
                draw.text(
                    (x, y_anchor), ch, font=font, fill=fill, anchor=anchor,
                    stroke_width=stroke_width, stroke_fill=stroke_fill,
                )
            else:
                draw.text(
                    (x, y_anchor), ch, font=font, fill=fill, anchor=anchor,
                )
        except (TypeError, ValueError):
            if use_stroke:
                draw.text(
                    (x, legacy_top), ch, font=font, fill=fill,
                    stroke_width=stroke_width, stroke_fill=stroke_fill,
                )
            else:
                draw.text(
                    (x, legacy_top), ch, font=font, fill=fill,
                )
        x += cw + tracking_px


def _bottom_gradient_scrim(
    width: int, height: int,
    scrim_height_ratio: float = 0.18,
    start_alpha: int = 0,
    end_alpha: int = 205,
    color: tuple[int, int, int] = (10, 8, 8),
) -> Image.Image:
    """Return an RGBA overlay matching `width`x`height` with a soft vertical
    gradient occupying the bottom `scrim_height_ratio` of the canvas. The
    gradient eases quadratically so the top edge of the scrim is barely
    perceptible and the bottom edge is opaque enough to make any cream text
    legible regardless of the underlying palette."""
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    scrim_h = max(1, int(height * scrim_height_ratio))
    draw = ImageDraw.Draw(overlay)
    for i in range(scrim_h):
        t = i / max(1, scrim_h - 1)
        # ease-in quadratic: gentler at the top of the scrim
        eased = t * t
        alpha = int(round(start_alpha + (end_alpha - start_alpha) * eased))
        y = height - scrim_h + i
        draw.line(
            [(0, y), (width - 1, y)],
            fill=(color[0], color[1], color[2], alpha),
        )
    return overlay


# ---------------------------------------------------------------------------
# Brand footer (deterministic, archetype-agnostic)
# ---------------------------------------------------------------------------


def apply_brand_footer(
    img: Image.Image,
    text: str = "THE FILIPINO STANDARD",
    fonts_key: str = "display_serif",
    text_color: str = "#E8D9B0",   # warm parchment / cream
    scrim_color: tuple[int, int, int] = (10, 8, 8),
    scrim_height_ratio: float = 0.16,
    scrim_end_alpha: int = 205,
    text_height_ratio: float = 0.032,
    tracking_ratio: float = 0.20,
    text_baseline_ratio: float = 0.962,
) -> Image.Image:
    """Stamp the brand footer at the bottom of the image.

    The footer is rendered as Trajan-style Roman capitals (Cinzel) in a warm
    cream against a soft bottom gradient scrim. The scrim ensures legibility
    on ANY palette - bright interiors, cool split-screens, dark documentary
    photographs, orange editorial cartoon. The hard-bar approach was retired
    after the cream-text-on-anything use case made it look heavy and
    pasted-on.
    """
    text = _normalize_text(text).strip()
    if not text:
        return img
    base = img.convert("RGBA").copy()
    W, H = base.size

    scrim = _bottom_gradient_scrim(
        W, H,
        scrim_height_ratio=scrim_height_ratio,
        end_alpha=scrim_end_alpha,
        color=scrim_color,
    )
    composed = Image.alpha_composite(base, scrim)

    draw = ImageDraw.Draw(composed)
    font_size = max(12, int(H * text_height_ratio))
    font = _resolve_font(fonts_key, font_size)
    tracking_px = max(0, int(font_size * tracking_ratio))

    # Center the text vertically near the bottom of the canvas
    bbox = draw.textbbox((0, 0), text, font=font)
    text_h = bbox[3] - bbox[1]
    y_center = int(H * text_baseline_ratio)
    y_top = y_center - text_h // 2

    _draw_centered_with_tracking(
        draw, text, font, text_color, y_top, W, tracking_px,
    )
    return composed.convert("RGB")


def apply_brand_footer_to_bytes(
    png_bytes: bytes,
    text: str = "THE FILIPINO STANDARD",
    **kwargs: Any,
) -> bytes:
    """Convenience wrapper: bytes -> PNG bytes."""
    img = Image.open(BytesIO(png_bytes))
    out = apply_brand_footer(img, text=text, **kwargs)
    buf = BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Per-archetype text helpers
# ---------------------------------------------------------------------------


def add_headline(
    img: Image.Image,
    text: str,
    fonts_key: str = "display_serif",
    zone_top_ratio: float = 0.05,
    zone_bottom_ratio: float = 0.22,
    font_size_ratio: float = 0.072,
    tracking_ratio: float = 0.08,
    color: str = "#FFFFFF",
    stroke_width_ratio: float = 0.04,
    stroke_fill: str = "#0A0808",
) -> Image.Image:
    """Render the headline in a top reserved zone, centered, with editorial
    tracking and a thin dark stroke for legibility on any background."""
    text = _normalize_text(text).strip()
    if not text:
        return img
    out = img.convert("RGB").copy()
    draw = ImageDraw.Draw(out)
    W, H = out.size

    font_size = max(20, int(H * font_size_ratio))
    font = _resolve_font(fonts_key, font_size)
    tracking_px = int(font_size * tracking_ratio)

    max_text_width = int(W * 0.88)
    lines = _wrap_text_to_width(text, font, draw, max_text_width, tracking_px)

    # Shrink-to-fit (tracking-aware) so the widest tracked line stays inside the safe area
    for _ in range(8):
        widest = max(
            _measure_tracked_width(draw, ln, font, tracking_px) for ln in lines
        )
        if widest <= max_text_width or font_size <= 24:
            break
        font_size = int(font_size * 0.92)
        font = _resolve_font(fonts_key, font_size)
        tracking_px = int(font_size * tracking_ratio)
        lines = _wrap_text_to_width(text, font, draw, max_text_width, tracking_px)

    mg = draw.textbbox((0, 0), "Mg", font=font)
    line_h = int((mg[3] - mg[1]) * 1.18)
    total_text_h = line_h * len(lines)
    zone_top = int(H * zone_top_ratio)
    zone_bottom = int(H * zone_bottom_ratio)
    start_y = zone_top + max(0, (zone_bottom - zone_top - total_text_h) // 2)

    stroke_w = max(1, int(font_size * stroke_width_ratio))

    for i, line in enumerate(lines):
        y = start_y + i * line_h
        _draw_centered_with_tracking(
            draw, line, font, color, y, W, tracking_px,
            stroke_width=stroke_w, stroke_fill=stroke_fill,
        )
    return out


def add_meme_top_text(
    img: Image.Image, text: str,
    fonts_key: str = "headline_meme",
    font_size_ratio: float = 0.090,
    tracking_ratio: float = 0.02,
) -> Image.Image:
    """Meme top-text. Sits in the upper zone of the canvas. Anton/Bebas Neue
    by default - condensed display poster face."""
    return add_headline(
        img, text,
        fonts_key=fonts_key,
        zone_top_ratio=ZONE_MEME_TOP_TOP_RATIO,
        zone_bottom_ratio=ZONE_MEME_TOP_BOTTOM_RATIO,
        font_size_ratio=font_size_ratio,
        tracking_ratio=tracking_ratio,
    )


def add_meme_bottom_text(
    img: Image.Image,
    text: str,
    fonts_key: str = "display_serif",
    zone_top_ratio: float = ZONE_MEME_BOTTOM_TOP_RATIO,
    zone_bottom_ratio: float = ZONE_MEME_BOTTOM_BOTTOM_RATIO,
    font_size_ratio: float = 0.078,
    tracking_ratio: float = 0.06,
    color: str = "#FFFFFF",
    stroke_width_ratio: float = 0.04,
    stroke_fill: str = "#0A0808",
) -> Image.Image:
    """Meme bottom-text. The zone ends at 83% canvas height with a clear
    buffer above the bottom-of-canvas footer scrim (which starts at ~84%).
    The two never overlap by construction - this is the architectural fix
    for the satirical_meme bottom-text collision."""
    text = _normalize_text(text).strip()
    if not text:
        return img
    out = img.convert("RGB").copy()
    draw = ImageDraw.Draw(out)
    W, H = out.size

    font_size = max(20, int(H * font_size_ratio))
    font = _resolve_font(fonts_key, font_size)
    tracking_px = int(font_size * tracking_ratio)
    max_text_width = int(W * 0.88)
    lines = _wrap_text_to_width(text, font, draw, max_text_width, tracking_px)

    for _ in range(8):
        widest = max(
            _measure_tracked_width(draw, ln, font, tracking_px) for ln in lines
        )
        if widest <= max_text_width or font_size <= 24:
            break
        font_size = int(font_size * 0.92)
        font = _resolve_font(fonts_key, font_size)
        tracking_px = int(font_size * tracking_ratio)
        lines = _wrap_text_to_width(text, font, draw, max_text_width, tracking_px)

    mg = draw.textbbox((0, 0), "Mg", font=font)
    line_h = int((mg[3] - mg[1]) * 1.18)
    total_text_h = line_h * len(lines)
    zone_top = int(H * zone_top_ratio)
    zone_bottom = int(H * zone_bottom_ratio)
    start_y = zone_top + max(0, (zone_bottom - zone_top - total_text_h) // 2)

    stroke_w = max(1, int(font_size * stroke_width_ratio))

    for i, line in enumerate(lines):
        y = start_y + i * line_h
        _draw_centered_with_tracking(
            draw, line, font, color, y, W, tracking_px,
            stroke_width=stroke_w, stroke_fill=stroke_fill,
        )
    return out


def add_split_labels(
    img: Image.Image,
    left_label: str | None = None,
    right_label: str | None = None,
    fonts_key: str = "display_serif",
    y_center_ratio: float = ZONE_SPLIT_LABEL_Y_CENTER_RATIO,
    font_size_ratio: float = ZONE_SPLIT_LABEL_FONT_H_RATIO,
    tracking_ratio: float = 0.22,
    color: str = "#FFFFFF",
    stroke_width_ratio: float = 0.10,
    stroke_fill: str = "#0A0808",
) -> Image.Image:
    """Place ph_vs_nz_split panel labels at the bottom of each half. Labels
    are centered within their half and sit ABOVE the bottom footer scrim.
    Used for the ph_vs_nz_split archetype only — two short labels, one per
    panel, sourced verbatim from text_in_image.left_label / right_label."""
    out = img.convert("RGB").copy()
    draw = ImageDraw.Draw(out)
    W, H = out.size
    font_size = max(16, int(H * font_size_ratio))
    font = _resolve_font(fonts_key, font_size)
    tracking_px = int(font_size * tracking_ratio)
    stroke_w = max(1, int(font_size * stroke_width_ratio))

    def _draw_label_in_half(
        label: str, half_left: int, half_right: int,
    ) -> None:
        label = _normalize_text(label).strip()
        if not label:
            return
        # Char widths with tracking
        widths: list[int] = []
        for ch in label:
            if ch == " ":
                sb = draw.textbbox((0, 0), "M", font=font)
                widths.append(max(1, int((sb[2] - sb[0]) * 0.55)))
            else:
                cb = draw.textbbox((0, 0), ch, font=font)
                widths.append(cb[2] - cb[0])
        total_w = sum(widths) + tracking_px * max(0, len(label) - 1)
        half_center_x = (half_left + half_right) // 2
        x = half_center_x - total_w // 2
        fb = draw.textbbox((0, 0), label, font=font)
        y_top = int(H * y_center_ratio) - (fb[3] - fb[1]) // 2
        for ch, cw in zip(label, widths):
            try:
                draw.text(
                    (x, y_top), ch, font=font, fill=color, anchor="lt",
                    stroke_width=stroke_w, stroke_fill=stroke_fill,
                )
            except (TypeError, ValueError):
                draw.text(
                    (x, y_top - fb[1]), ch, font=font, fill=color,
                    stroke_width=stroke_w, stroke_fill=stroke_fill,
                )
            x += cw + tracking_px

    if left_label:
        _draw_label_in_half(left_label, 0, W // 2)
    if right_label:
        _draw_label_in_half(right_label, W // 2, W)
    return out


# ---------------------------------------------------------------------------
# Per-archetype dispatcher
# ---------------------------------------------------------------------------


def apply_text_for_archetype(
    img: Image.Image,
    archetype: str,
    text_in_image: dict[str, Any] | None,
) -> Image.Image:
    """Apply the post-generation overlays for `archetype`, then stamp the
    universal watermark.

    Simplified text policy (one row per archetype, the entire system):

      * editorial_allegory    : watermark only. No headline on image.
      * pain_point            : watermark only. No headline on image.
      * constitutional_quote  : watermark only. The verbatim quote and
                                attribution live in the post caption, NOT
                                on the image. The bespoke quote/attribution
                                rendering has been retired.
      * ph_vs_nz_split        : left_label + right_label + watermark.
                                Two short labels from text_in_image.
      * satirical_meme        : top_text + bottom_text + watermark.
                                Unchanged — this is the one archetype that
                                is intrinsically a text format.

    Returns the rendered PIL Image. For the deterministic layout check, see
    verify_layout(image, archetype, text_in_image) — it runs separately.
    """
    tii = dict(text_in_image or {})

    if archetype == "satirical_meme":
        # Anton condensed sans, top/bottom zones structurally separated from
        # the watermark scrim by the constants above. Either field may be
        # empty; the renderer no-ops when text is blank.
        if tii.get("top_text"):
            img = add_meme_top_text(
                img, tii["top_text"],
                fonts_key="headline_meme",
                font_size_ratio=0.090,
                tracking_ratio=0.02,
            )
        if tii.get("bottom_text"):
            img = add_meme_bottom_text(
                img, tii["bottom_text"],
                fonts_key="headline_meme",
                font_size_ratio=0.090,
                tracking_ratio=0.02,
            )

    elif archetype == "ph_vs_nz_split":
        # Two short labels, one per panel, sit just above the watermark scrim.
        # Either may be empty — add_split_labels no-ops per missing label.
        left_label  = (tii.get("left_label")  or "").strip()
        right_label = (tii.get("right_label") or "").strip()
        if left_label or right_label:
            img = add_split_labels(
                img,
                left_label=left_label or None,
                right_label=right_label or None,
            )

    # All other archetypes (editorial_allegory, pain_point, constitutional_quote)
    # are watermark-only. No `if` branch is needed — they fall through to
    # the watermark below.

    # Watermark: Cinzel cream on bottom gradient scrim, uniform across every
    # archetype that reaches this dispatcher. This is the brand wordmark and
    # is the same code path that has worked from day one.
    footer_text = (tii.get("footer") or "").strip() or "THE FILIPINO STANDARD"
    img = apply_brand_footer(img, text=footer_text)
    return img


def apply_text_for_archetype_to_bytes(
    png_bytes: bytes,
    archetype: str,
    text_in_image: dict[str, Any] | None,
) -> bytes:
    """Convenience wrapper: bytes -> PNG bytes. Layout-check is performed by
    verify_layout(), called separately by generate_image.py against the
    rendered PIL Image."""
    img = Image.open(BytesIO(png_bytes))
    out = apply_text_for_archetype(img, archetype, text_in_image)
    buf = BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Deterministic layout check (no API cost)
# ---------------------------------------------------------------------------
#
# Burned-in text policy is constants-only at this point: meme top/bottom and
# split labels are placed via ZONE_* ratios defined at the top of this file.
# verify_layout asserts that the zones for the archetype-in-question:
#   - sit fully within the canvas with a SAFE_MARGIN_PX gap from every edge,
#   - do not overlap each other,
#   - do not overlap the watermark scrim zone.
#
# This is pure coordinate math — no vision call. If verify_layout fails,
# that is a CODE BUG (someone changed a zone constant without updating the
# rest), not a content issue. The caller is expected to refuse to ship the
# image and quarantine the task with a clear error.


def verify_layout(
    img: Image.Image,
    archetype: str,
    text_in_image: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return {"ok": bool, "violations": [str], "zones": {name: bbox}}.

    Only the two text-bearing archetypes (satirical_meme, ph_vs_nz_split)
    have non-trivial zones. Watermark-only archetypes succeed when the
    watermark scrim zone fits within the canvas.
    """
    W, H = img.size
    margin = SAFE_MARGIN_PX
    zones: dict[str, tuple[int, int, int, int]] = {}
    violations: list[str] = []

    tii = dict(text_in_image or {})

    # Watermark scrim — always present. Use the SCRIM band as its bbox so
    # other text zones must clear the full scrim region, not just the
    # baseline.
    wm_top = int(H * ZONE_WATERMARK_SCRIM_TOP_RATIO)
    zones["watermark_scrim"] = (0, wm_top, W, H)

    if archetype == "satirical_meme":
        if tii.get("top_text"):
            y0 = int(H * ZONE_MEME_TOP_TOP_RATIO)
            y1 = int(H * ZONE_MEME_TOP_BOTTOM_RATIO)
            zones["meme_top"] = (margin, y0, W - margin, y1)
        if tii.get("bottom_text"):
            y0 = int(H * ZONE_MEME_BOTTOM_TOP_RATIO)
            y1 = int(H * ZONE_MEME_BOTTOM_BOTTOM_RATIO)
            zones["meme_bottom"] = (margin, y0, W - margin, y1)
    elif archetype == "ph_vs_nz_split":
        # Label band: a slim horizontal stripe centred at y_center, with a
        # height roughly equal to the font glyph height. We use 1.5x the
        # font-h ratio as a conservative bbox height so we capture the
        # full set including descenders + stroke.
        label_band_h = max(8, int(H * ZONE_SPLIT_LABEL_FONT_H_RATIO * 1.5))
        y_center = int(H * ZONE_SPLIT_LABEL_Y_CENTER_RATIO)
        y0 = y_center - label_band_h // 2
        y1 = y_center + label_band_h // 2
        if tii.get("left_label"):
            zones["split_left_label"] = (margin, y0, W // 2 - margin, y1)
        if tii.get("right_label"):
            zones["split_right_label"] = (W // 2 + margin, y0, W - margin, y1)
    # editorial_allegory, pain_point, constitutional_quote: watermark only.

    # Edge-margin check on every non-watermark zone
    for name, (l, t, r, b) in zones.items():
        if name == "watermark_scrim":
            continue  # the scrim is designed to run to the bottom edge
        if l < margin or t < margin or r > W - margin or b > H - margin:
            violations.append(
                f"{name} bbox ({l},{t},{r},{b}) violates {margin}px safe "
                f"margin on canvas {W}x{H}"
            )

    # Pairwise overlap check across all zones (including watermark)
    items = list(zones.items())
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            n1, b1 = items[i]
            n2, b2 = items[j]
            if not (b1[2] <= b2[0] or b2[2] <= b1[0]
                    or b1[3] <= b2[1] or b2[3] <= b1[1]):
                violations.append(f"{n1} bbox {b1} overlaps {n2} bbox {b2}")

    return {
        "ok": len(violations) == 0,
        "violations": violations,
        "zones": {k: list(v) for k, v in zones.items()},
        "canvas": [W, H],
    }


# ---------------------------------------------------------------------------
# Legacy patch tool - CLI overlay (preserved for ad-hoc fixes)
# ---------------------------------------------------------------------------


def _resolve_position(
    position: str | list[int],
    img_size: tuple[int, int],
    text_size: tuple[int, int],
) -> tuple[int, int]:
    """Translate a named anchor (or explicit [x, y]) into pixel coordinates."""
    w, h = img_size
    tw, th = text_size
    if isinstance(position, list) and len(position) == 2:
        return int(position[0]), int(position[1])
    p = str(position).lower()
    if p == "top-left":      return (SAFE_PADDING, SAFE_PADDING)
    if p == "top-center":    return ((w - tw) // 2, SAFE_PADDING)
    if p == "top-right":     return (w - tw - SAFE_PADDING, SAFE_PADDING)
    if p == "center":        return ((w - tw) // 2, (h - th) // 2)
    if p == "bottom-left":   return (SAFE_PADDING, h - th - SAFE_PADDING)
    if p == "bottom-center": return ((w - tw) // 2, h - th - SAFE_PADDING)
    if p == "bottom-right":  return (w - tw - SAFE_PADDING, h - th - SAFE_PADDING)
    raise ValueError(f"Unknown position: {position!r}")


def apply_overlay(
    img: Image.Image,
    text: str,
    position: str | list[int],
    font_size: int = 24,
    color: str = "#222222",
    font_path: str | None = None,
    stroke_width: int = 0,
    stroke_fill: str | None = None,
) -> Image.Image:
    """Apply one ad-hoc text overlay (legacy CLI patch tool)."""
    text = _normalize_text(text)
    if not text:
        return img
    out = img.convert("RGBA").copy()
    draw = ImageDraw.Draw(out)
    font = _resolve_font(font_path, font_size)
    bbox = draw.textbbox(
        (0, 0), text, font=font, stroke_width=stroke_width or 0,
    )
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x, y = _resolve_position(position, out.size, (text_w, text_h))
    draw.text(
        (x, y), text, font=font, fill=color,
        stroke_width=stroke_width or 0, stroke_fill=stroke_fill,
    )
    return out


def apply_overlays_from_spec(
    img: Image.Image, spec: dict[str, Any],
) -> Image.Image:
    out = img
    for ov in (spec.get("overlays") or []):
        out = apply_overlay(
            out,
            text=ov.get("text", ""),
            position=ov.get("position", "bottom-right"),
            font_size=int(ov.get("font_size", 24)),
            color=ov.get("color", "#222222"),
            font_path=ov.get("font"),
            stroke_width=int(ov.get("stroke_width", 0) or 0),
            stroke_fill=ov.get("stroke_fill"),
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply ad-hoc text overlays to a generated PNG. "
                    "Use sparingly; the pipeline's per-archetype dispatcher "
                    "is the canonical path.",
    )
    parser.add_argument("--input",  required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--text")
    parser.add_argument("--position", default="bottom-right")
    parser.add_argument("--font-size", type=int, default=24)
    parser.add_argument("--color", default="#222222")
    parser.add_argument("--font")
    parser.add_argument("--spec", type=Path)
    args = parser.parse_args()

    if not args.input.exists():
        print(f"ERROR: input not found: {args.input}", file=sys.stderr)
        return 2
    img = Image.open(args.input)

    if args.spec:
        if not args.spec.exists():
            print(f"ERROR: spec not found: {args.spec}", file=sys.stderr)
            return 2
        spec = json.loads(args.spec.read_text(encoding="utf-8"))
        out = apply_overlays_from_spec(img, spec)
    elif args.text:
        position: str | list[int] = args.position
        if "," in str(args.position):
            try:
                xs, ys = str(args.position).split(",", 1)
                position = [int(xs.strip()), int(ys.strip())]
            except ValueError:
                pass
        out = apply_overlay(
            img,
            text=args.text,
            position=position,
            font_size=args.font_size,
            color=args.color,
            font_path=args.font,
        )
    else:
        print("ERROR: provide either --text or --spec.", file=sys.stderr)
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if out.mode == "RGBA":
        bg = Image.new("RGB", out.size, "white")
        bg.paste(out, mask=out.split()[3])
        out = bg
    out.save(args.output, "PNG")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
