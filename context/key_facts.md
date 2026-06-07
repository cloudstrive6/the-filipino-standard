# Key Facts and Terminology

> The single source of truth for verified numbers, names, and brand terminology used across The Filipino Standard. Every post and image generation prompt references this file. Update here first; downstream skills and scripts pull from here.

---

## Terminology

### MSMEs, NOT SMEs

Use **MSMEs** (Micro, Small, and Medium Enterprises), NOT "SMEs". The Micro tier covers sari-sari stores, palengke vendors, tricycle drivers, freelancers, and online sellers, which together represent the overwhelming bulk of Philippine business by count. Source: aligned with DTI classifications and NZ MFAT trade references. For all-caps headlines per the case convention, write **MSMES**.

Why the distinction matters for this brand:
- The Filipino economy is dominated by micro-enterprises, not the small-to-medium-firm shape implied by "SMEs"
- Talking only about "SMEs" excludes the majority of working Filipinos who run informal or near-informal businesses
- DTI's own published classifications use MSMEs as the canonical term
- It's the more precise word and the more inclusive frame; the brand uses both

When in doubt:
- Headlines and labels in images: `MSMES` (all caps, per the headline case convention)
- Captions and body copy: `MSMEs` (mixed case, standard editorial)
- Avoid `SME` / `SMEs` entirely unless directly quoting a source that uses that term

---

## Numbers under fact-check protocol

These are the recurring numbers the heuristic fact-check pass in `scripts/generate_image.py` watches for. Always cross-check against this table before letting a post quote one of them.

| Fact | Correct value | Common error |
|---|---|---|
| Vector dividend (per household, one payment) | NZ$364 / P13,000 | Sometimes claimed as "two dividends" - it is one payment per year |
| Meralco - MPIC stake | ~50.4% | Often stated as 51% or 49%; verify the decimal |
| Meralco - JG Summit stake | ~29.5% | Often stated as 30%; verify the decimal |
| Entrust ownership of Vector | ~75.1% | Often rounded to 75%; if precision matters, use 75.1% |

---

## How to extend this file

When adding a new fact:
1. Add it under the appropriate section (or create one)
2. Cite the source inline (DTI release, statute, news outlet + date)
3. If it has a common-error variant the brand has been bitten by, document it in the right-hand column so the heuristic fact-check can catch it
4. Notify Content Creation and Image Generation skills if the new fact materially changes any standing copy
