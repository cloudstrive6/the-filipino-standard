---
name: image-generation
description: Interactive companion for The Filipino Standard's image generation pipeline. The heavy lifting (scanning ClickUp for tasks needing images, calling OpenAI gpt-image-1, running the vision QA pass, attaching the image to the task) is handled by the `scripts/generate_image.py` worker fired on a schedule by Task Scheduler. This skill exists for the human moments: regenerating one image when a content tweak lands, generating an image for a fresh task that should not wait for the next scheduled run, debugging a failed generation, sanity-checking an Image Prompt / archetype / text_in_image payload before letting the script render it, deciding whether a near-miss image deserves a Pillow patch via `scripts/image_text_overlay.py` or a full regeneration. Use whenever the user asks to generate an image, make a graphic, create the visual, regenerate the image, "give me the picture for task X," "render the image for this post now," "the prompt looks weird, can you check it," "run image gen on this," "patch the brand footer," or "the headline is a typo, can you fix it." Do NOT use this skill to write Image Prompts (visual subjects) or text_in_image JSON from scratch; Content Creation produces those. Do NOT re-implement the script's logic inline; invoke `scripts/generate_image.py` via Bash and trust it to handle the OpenAI calls, QA, file saving, ClickUp update, and retry logic.
---

# Image Generation (interactive companion)

You are the **interactive companion** to The Filipino Standard's image generation pipeline. The script `scripts/generate_image.py` does the actual rendering and QA work on a schedule. Your job is to handle the human moments where someone needs to intervene without waiting for the next scheduled run, plus the small set of judgment calls (patch vs regenerate, prompt review, archetype selection) that benefit from human-in-the-loop reasoning.

Pipeline position:

1. Research and Trending produces the brief
2. Content Creation writes the post copy AND the `archetype`, `Image Prompt` (visual subject), and `text_in_image` JSON, then creates the ClickUp task on list `901614911598`
3. **`scripts/generate_image.py`** (fired on schedule by `TFS Image Generator`) generates the image, runs vision QA, saves outputs, updates ClickUp
4. Publisher publishes the task at its scheduled time

This skill plugs in at step 3 when a human wants to act on a specific task right now, or to decide on a near-miss after QA flagged it.

---

## How the script works (so you know what you are delegating to)

`scripts/generate_image.py` lives at `Z:\Business Empire\The Filipino Standard\scripts\generate_image.py`. Two modes:

```
py scripts/generate_image.py --pending          Scan ClickUp for tasks needing images
py scripts/generate_image.py --task 86d2z9gz3   Generate for one specific task
```

What it does per task:

1. **Reads inputs from ClickUp custom fields** on list `901614911598`:
   - `archetype` (dropdown, field `ff14a5f5-9124-4a92-95c0-44a33dde7ee7`): which composition template to use
   - `style` (dropdown, field `d91c15b8-95dc-47f2-aa70-6d58effa7b01`): rendering style (flat_editorial, cinematic_realistic, hyperreal_dramatic, editorial_cartoon, documentary_photo). If empty, falls back to the archetype's default style.
   - `Image Prompt` (long text, field `f74ba9b7-c635-48b8-a762-5ccb093eeeaa`): the **visual subject**, a 1-2 sentence scene description
   - `text_in_image` (long text, field `c265f867-bf8a-4be1-bf07-0777fc58cfa0`): a JSON object with the exact text to render (headline, subhead, left_label, left_stat, right_label, right_stat, quote, attribution, footer)
   - The task description: the post caption (used for fact-check context, not embedded in the prompt directly)
2. **Builds the prompt**, routed by archetype:
   * **constitutional_quote** (legacy / locked benchmark path): full prescriptive STYLE + COMPOSITION + SUBJECT + TEXT IN IMAGE + LEGACY_BRAND_RULES. The model renders the framed/decorated quote with integrated typography. Locked task `86d30n0ne` is the brand benchmark for this path.
   * **everything else** (default + satirical_meme): **the new light prompt**. One short paragraph: style descriptor + brand identity + theme summary + no-text + Filipino authenticity + credibility + aspect ratio. No panel-by-panel scene scaffolding. No prescriptive composition. The agent picks a path and a style; the model is trusted to make the visual decisions.
3. **Runs a heuristic last-resort fact-check** on the prompt inputs and caption. Common factual pitfalls are caught (NZ$364 / P13,000 dividend count, Meralco ownership percentages, Entrust / Vector percentage). Warnings are logged into the sidecar but are non-blocking; the script still generates.
4. **Generates** the image via OpenAI `gpt-image-1` at the archetype's default size (or an override). Three valid sizes: `1024x1024`, `1024x1536`, `1536x1024`. On the light-prompt path the model produces text-free imagery only; on the constitutional_quote legacy path it renders text directly.
5. **Applies overlays via Pillow** (`scripts/image_text_overlay.py::apply_text_for_archetype_to_bytes`), dispatched by archetype:
   * **constitutional_quote**: skipped entirely (the model already rendered text into the image; overlay would destroy it).
   * **satirical_meme**: top_text + bottom_text in Anton + the Cinzel watermark. Zones (4-20%, 66-83%, 84-100%) never overlap by construction.
   * **everything else (default)**: **the Cinzel watermark only.** No headline overlay, no label overlay. The argument lives in the FB caption.
6. **Vision QA** via OpenAI `gpt-4o`, also archetype-routed:
   * **constitutional_quote**: legacy QA with text-quality rules (em dash, spelling, voice, numeric format, etc.) plus subject rules.
   * **everything else**: the new five-rule scene-only QA - no real-person likenesses, authentic Filipino subjects, no incidental rendered text in the scene, not sensational/exploitative/clickbait, scene roughly matches the requested style register.
7. **Retry on QA failure**: up to 2 regenerations after the initial attempt (3 total tries). The last attempt's image and QA result are kept regardless of pass status. Each regenerated image gets a fresh full overlay pass.
8. **On final QA pass**: writes the Image URL custom field (as a `file://` URI pointing to the local versioned PNG), attaches the PNG as a ClickUp task attachment so reviewers see it inline.
9. **On all-attempts-failed QA**: keeps the latest PNG and a complete sidecar JSON, moves the task to `NEEDS-REVISION`, adds a comment summarizing the final violations and pointing at the sidecar so a human can decide patch-vs-regenerate.
10. **Outputs** per task in `/output/images/`:
   - `YYYY-MM-DD-<slug>.png` (the final image, latest attempt)
   - `YYYY-MM-DD-<slug>.json` (sidecar: exact prompt, archetype, size, attempt history, every QA result, fact-check warnings)

Idempotency is built in: re-running on a task with Image URL already set is a no-op.

---

## Archetype and style are now decoupled (two separate dropdowns)

The pipeline used to bake the visual style into the archetype template. That coupling is gone. Now there are two independent dimensions:

- **Archetype** (composition): the structural layout, what is in the frame, how the elements are arranged. Read from the `archetype` custom field.
- **Style** (rendering): how the image is rendered, the medium, the lighting, the visual register. Read from the `style` custom field. Defaults per archetype if the field is empty; explicitly overridable per task.

Final prompt assembly:

```
STYLE template
COMPOSITION (from archetype)
SUBJECT (from Image Prompt / visual_subject)
TEXT IN IMAGE block (from text_in_image JSON)
BRAND RULES APPENDIX (always appended)
```

### The 5 archetypes (composition only)

| Archetype | Default size | Default style | When to use |
|---|---|---|---|
| **editorial_allegory** | 1024x1024 (square) | hyperreal_dramatic | Single-panel argument carrying a structural metaphor (institutions, governance, broader systems) |
| **ph_vs_nz_split** | 1536x1024 (landscape) | cinematic_realistic | A direct PH-vs-NZ comparison post (or any side-by-side contrast) |
| **satirical_meme** | 1024x1024 (square) | editorial_cartoon | Sharp punchline, satirical but not cruel. Political commentary that earns the visual joke. |
| **constitutional_quote** | 1024x1536 (portrait) | flat_editorial | A quote-driven post that frames a 1987 Constitution clause or other civic text |
| **pain_point** | 1024x1024 (square) | documentary_photo | An ordinary friction moment (Meralco bill, RFID queue, registration wall, BIR window). The SME or household is the hero. |

### The 5 styles (rendering only)

| Style | What it produces | Best fits |
|---|---|---|
| **flat_editorial** | Flat editorial vector look, limited muted palette, clean line work over flat color | Constitutional pieces, infographic-feeling content, anything where text legibility is paramount |
| **cinematic_realistic** | Cinematic editorial photography, natural lighting, shallow depth of field, slightly desaturated, looks like a still from a documentary film | PH-vs-NZ contrasts, anything where atmosphere does the lifting |
| **hyperreal_dramatic** | Hyperrealistic digital illustration, dramatic golden-hour or overcast lighting, high detail, painterly but lifelike | Allegory, institutional metaphors, anything that needs visual weight |
| **editorial_cartoon** | Satirical editorial cartoon, slightly exaggerated, ink-and-wash, expressive but not childish | Political commentary, satirical takes, single-panel punchlines |
| **documentary_photo** | Documentary photojournalism, candid, natural available light, no posing, looks like a real news photograph | Pain points, on-the-ground friction moments, authentically Filipino settings |

**Mix across the week.** Any archetype can be rendered in any style. The defaults pair archetype-to-style sensibly, but the editor can override per task (e.g., to render a constitutional_quote in cinematic_realistic for a specific dramatic effect, or to render a pain_point in editorial_cartoon for a satirical twist). Vary the mix so the feed doesn't feel monotone.

**Sizing strategy: mix across the week.** Different archetypes have different default sizes, so a varied weekly mix produces a natural variety of aspect ratios on the feed. Don't override unless there's a strong reason.

---

## Deterministic text rendering (model = scene only, Pillow = all text)

**Principle:** `gpt-image-1` generates the scene. Pillow renders every text element. This permanently ends the text failure class - clipping, case-normalization (`SMEs` -> `SMES`), decimal errors (`50.4%` -> `50,4%`), footer collision with bottom_text on memes, dropped text, garbled invented text on prop surfaces. The model is asked for one thing (a text-free scene); we render the rest.

This evolved through three stages:
1. Originally the model rendered both scene AND text. Repeated text failures: clipping, case errors, decimals, dropped footer, garbled props.
2. Footer was made deterministic. Solved the missing-footer case but other text-quality failures persisted, and the satirical_meme bottom_text collided with the footer bar.
3. **Current:** ALL text deterministic. The principle is uniform - if it's text, Pillow handles it.

### Architecture

```
gpt-image-1 returns text-free PNG bytes
        |
        v
apply_text_for_archetype_to_bytes(image_bytes, archetype, text_in_image)
  Dispatch by archetype to per-archetype layout:
    - pain_point         : add_headline (top reserved zone)        + footer
    - editorial_allegory : add_headline (top reserved zone)        + footer
    - ph_vs_nz_split     : add_headline + add_split_labels         + footer
    - constitutional_quote: add_quote_and_attribution (centered)    + footer
    - satirical_meme     : add_meme_top_text + add_meme_bottom_text + footer
  All zones are deterministic and known to not collide with each other.
        |
        v
gpt-4o QA receives the final composited image
  QA evaluates ONLY the scene the model produced. Pillow-rendered text is
  recognized as correct by construction; the QA prompt explicitly instructs
  the model not to evaluate the overlay text's spelling/layout.
```

### Brand typography

The brand uses a two-family type system, fully deterministic.

| Element | Font | Notes |
|---|---|---|
| Headline / labels / meme text | **Cinzel Bold** (variable, wght=700) | Trajan-style inscriptional Roman capitals. White fill with thin dark stroke for legibility on any background. Bundled under `assets/fonts/Cinzel.ttf`; system fallbacks: Trajan Pro, Times, Georgia. |
| Constitutional quote body | **EB Garamond Italic** | Old-style serif that pairs with Cinzel. Bundled under `assets/fonts/EBGaramond-Italic.ttf`; system fallbacks: Georgia Italic, Times Italic. |
| Constitutional quote attribution | **EB Garamond Regular** | Same family as the quote body, regular weight, slightly tracked, warm gold fill. |
| Brand footer (`THE FILIPINO STANDARD`) | **Cinzel Bold**, warm cream `#E8D9B0` | On a soft bottom gradient scrim (transparent fading to ~80% dark), so the footer stays legible against ANY underlying palette - bright interiors, cool split-screens, dark documentary, orange cartoon. Wide letter-spacing for the engraved-on-stone feel. |

The gradient scrim replaced the original hard solid bar after the cream-text-on-anything use case made the bar look heavy and pasted-on. The scrim's quadratic ease-in means the top edge of the scrim is barely visible and the bottom edge is opaque enough for reliable contrast.

### Per-archetype zones (no collisions, guaranteed)

| Archetype | Text zones |
|---|---|
| pain_point | Headline at canvas 5-22% vertical; footer at 84-100% (soft gradient). Middle 22-83% is the model's scene. |
| editorial_allegory | Same as pain_point. |
| ph_vs_nz_split | Headline at top 5-22%; left_label and right_label centered in each half at ~82% vertical; footer scrim 84-100%. |
| constitutional_quote | Quote + attribution in central band 18-80% (portrait); footer scrim 84-100%. |
| satirical_meme | top_text at 4-20%; **bottom_text at 66-83%** (architecturally never overlaps the footer scrim at 84-100%); footer scrim 84-100%. The satirical_meme bottom-text vs footer collision was the proximate trigger for the move to fully-deterministic text. |

### Implications for prompt assembly

- ALL text keys in `text_in_image` (headline, top_text, bottom_text, left_label, right_label, left_stat, right_stat, quote, attribution, footer) are stripped before `build_prompt` runs. None of them reach the model.
- `_text_overlay_block` no longer enumerates the text. It returns a strict "NO TEXT" instruction.
- The brand rules appendix dropped all text-quality rules (em dash, currency, voice, subject-verb, numeric format, layout margin). What remains: Filipino-authentic subjects, no real politicians, no logos, zero-text reinforcement.
- The vision QA prompt was simplified accordingly: scene-only checks plus a "the model's underlying scene must be completely text-free; the Pillow overlay layer is correct by construction and not your concern" instruction.

### Implications for Content Creation

- The Content Creation skill's contract is unchanged: write the `text_in_image` JSON keys as before, see them appear in the final image.
- The HEADLINE CASE CONVENTION rule (write headlines in their final case, e.g. `MSMES` not `MSMEs`) is now SEMANTIC only - Pillow will render whatever string is provided, so `MSMEs` mixed-case would render literally as `MSMEs`. The convention still matters for editorial reasons (all-caps headlines are brand-canonical), just not for technical reasons anymore.
- Constitutional quote text is rendered verbatim. There is no risk of the model paraphrasing or "cleaning up" the quote.

### Configuration knobs

The per-archetype layout parameters live in `scripts/image_text_overlay.py`:

| Function | What it does |
|---|---|
| `apply_text_for_archetype(img, archetype, text_in_image)` | Top-level dispatcher. Calls the archetype-specific functions, then the footer. |
| `apply_brand_footer(img, text, ...)` | Cinzel cream-on-scrim footer. Uniform across all archetypes - resist per-archetype customization here, it breeds bugs. |
| `add_headline(img, text)` | Top reserved zone, Cinzel, white+stroke. Used by pain_point, editorial_allegory, ph_vs_nz_split. |
| `add_meme_top_text(img, text)` / `add_meme_bottom_text(img, text)` | Larger Cinzel, with the bottom_text deliberately ending at 83% canvas height so it cannot overlap the 84%+ footer scrim. |
| `add_split_labels(img, left, right)` | ph_vs_nz_split. Each label centered horizontally within its panel half, at 82% vertical. |
| `add_quote_and_attribution(img, quote, attribution)` | constitutional_quote. EBGaramond italic for quote, EBGaramond regular for attribution. |

If a future change is needed (different scrim opacity, different headline color, different stroke), edit the function defaults. Centralize - don't sprinkle per-call config.

---

## What the model sees (per path)

### Default + satirical_meme path: a single light prompt

The model is no longer fed a multi-section prompt with composition templates, style templates, and an appendix. It receives ONE short paragraph from `_build_light_prompt`:

```
Create a scroll-stopping, attention-grabbing {STYLE_DESCRIPTOR} image for
The Filipino Standard, a Philippine governance-reform commentary page.
The image should visually complement this post without being literal or
clickbait: {THEME}. NO text anywhere in the image - a watermark is added
separately. If people appear they must read as authentically Filipino
(Malay/mestizo features, Filipino attire and setting, not generic
East-Asian or Western). Credible and editorial, never sensational,
exploitative, or caricatured into a real recognizable person. {ASPECT}.
```

That is the whole prompt. The agent supplied the style and the theme; the model handles the rest. `satirical_meme` forces `STYLE_DESCRIPTOR` to the `old_cartoon` value regardless of what's on the task.

### constitutional_quote path: legacy untouched

The legacy path still emits the full prescriptive prompt (STYLE + COMPOSITION + SUBJECT + the explicit TEXT IN IMAGE listing + LEGACY_BRAND_RULES with the em-dash / currency / voice / numeric rules). This is what produces the framed, decorated, integrated-typography output that Pillow overlay cannot match. The locked task `86d30n0ne` is the canonical example of this path's output.

---

## Fact-check protocol (do this BEFORE you ask the script to render)

The script has a last-resort heuristic check, but the primary responsibility is yours. Verify any number, name, or percentage in the `Image Prompt` (visual subject), `text_in_image` JSON, or caption against `context/key_facts.md`. Pay particular attention to:

| Fact | Correct value | Common error |
|---|---|---|
| NZ$364 / P13,000 | **One** Vector dividend payment | Sometimes claimed as "two dividends" |
| Meralco MPIC stake | ~50.4% | Often stated as 51% or 49%; verify the decimal |
| Meralco JG Summit stake | ~29.5% | Often stated as 30%; verify the decimal |
| Entrust ownership of Vector | ~75.1% | Often rounded to 75%; if precision matters, use 75.1% |

If you spot a violation BEFORE rendering, fix the inputs (description, Image Prompt, or text_in_image JSON) and then invoke the script. If you spot it AFTER rendering, see "Patch vs regenerate" below.

The script's heuristic check will flag these specific patterns automatically but treats them as warnings, not blockers. The warnings go into the sidecar JSON.

---

## Vision QA checklist (what the script enforces, what you should also eyeball)

After generation, the script asks `gpt-4o` to evaluate the image against the brand rules. The QA call returns a structured `{pass: bool, violations: [strings]}`. The script keeps the latest attempt regardless and retries up to 2 more times if the QA fails.

When you're reviewing manually (either before flipping a task back to APPROVED after a NEEDS-REVISION, or sanity-checking a passed image), use this checklist. It mirrors the QA prompt:

### Default + satirical_meme path (five rules):

- [ ] **No recognizable real-person likenesses.** Generic types are fine; specific identifiable faces are not.
- [ ] **Authentic Filipino subjects** where people appear. Malay/mestizo features, Filipino attire and setting; not generic East-Asian, not generic Western.
- [ ] **No incidental rendered text in the scene.** Signs, docs, phone screens, plates, name tags, watermarks must be blank or illegibly abstract. Pillow overlays on top (watermark, meme text) are correct by construction and exempt from this check.
- [ ] **Not sensational, exploitative, or clickbait.** Credible editorial register.
- [ ] **Roughly matches the requested style register.** Cinematic should read as a film still, oil_painting as painterly, monochrome as stark B&W, etc.

### satirical_meme additionally:

- [ ] **Top_text and bottom_text are legible** and clear of the watermark scrim (deterministic by zone construction - if they collide, the script has a bug).
- [ ] **The scene reads as a 1930s rubber-hose editorial cartoon** (old_cartoon is auto-forced for this archetype).

### constitutional_quote (legacy path):

- [ ] **The quote text is rendered correctly** (model-rendered, so spelling and casing matter).
- [ ] **The attribution is rendered correctly.**
- [ ] **The footer is rendered correctly.**
- [ ] **Civic composition** - centered, decorated, monumental.

If any box is unchecked, regenerate (preferred) or patch (only for trivial misses).

---

## Patch vs regenerate (the judgment call)

In the new architecture, **text issues are not a patch case** - they are not even possible. All text is rendered deterministically from `text_in_image` by Pillow. If a headline reads wrong, fix the `text_in_image.headline` value and re-run; the next render will be correct. Text typos cannot survive a re-run because the same JSON gets re-applied.

What CAN go wrong in the model's scene generation: wrong politician likeness, generic East-Asian instead of Filipino subjects, composition/palette mismatch, hallucinated text on background props, accidentally invented signage. None of these are patchable; they require regeneration with a tightened visual_subject and/or composition.

> 📌 **The legacy Pillow patch tool (`scripts/image_text_overlay.py` CLI) is still available** for ad-hoc one-off overlays outside the standard pipeline. It is not part of the per-task render flow. Reach for it rarely - usually you want to update `text_in_image` and re-run instead.

**Regenerate (not patch) for everything pipeline-related:**

- Wrong politician likeness
- Subjects don't read as authentically Filipino
- Composition or palette mismatch with the archetype
- Model leaked text into the underlying scene (e.g., legible storefront sign behind a subject)
- Anything that materially changes the visual argument

**The only path to fixing a text element is to fix the `text_in_image` JSON on the task and re-run.** A re-run with a corrected JSON re-applies the deterministic overlay; the text will be right by construction.

The script never auto-patches. Patching is always a deliberate human action. To patch:

```
py scripts/image_text_overlay.py \
    --input  Z:\\Business Empire\\The Filipino Standard\\output\\images\\YYYY-MM-DD-slug.png \
    --output Z:\\Business Empire\\The Filipino Standard\\output\\images\\YYYY-MM-DD-slug-patched.png \
    --text   "The Filipino Standard" \
    --position bottom-right \
    --font-size 24 \
    --color "#222222"
```

Or use a JSON spec for multiple overlays. See the script's docstring for the full CLI and the spec shape.

After patching, update the task's `Image URL` to point at the patched PNG and re-run the vision QA manually (open the patched image and walk the checklist above).

---

## When to invoke this skill vs let the scheduler handle it

| Scenario | Action |
|---|---|
| Content Creation just created a task and the user wants the image NOW, not after the next scheduled `TFS Image Generator` cycle (every 15 min) | Run the script in `--task` mode for that task ID |
| User dislikes the rendered image, wants a fresh attempt | Clear the Image URL field on the task, then re-run `--task` |
| A scheduled `--pending` run failed for a task (now in NEEDS-REVISION) and the user wants to debug | Read the sidecar JSON, decide patch vs regenerate, act |
| Routine batch generation across many waiting tasks | Just point the user at the Task Scheduler entry. That's what `TFS Image Generator` is for. |
| User wants to inspect a prompt before paying for a generation | Read the relevant Content Creation outputs, walk the brand rules, suggest fixes BEFORE running the script |

---

## Always load context first

For any prompt-review or quality-judgment work, read these:

1. `context/brand-context.md`: recognize off-brand imagery before letting the script render it
2. `context/content-creation-guide.md` (image prompt strategy section): what each pillar's visual register should look like
3. `context/key_facts.md` (if present): the source of truth for numbers used in fact-check

You don't need to load these to merely invoke the script (the script and Content Creation already encoded these rules). Load them when the user is asking you to review a prompt or judge a generated image.

---

## ClickUp safety rule

> The script is hardcoded to list `901614911598` and refuses to operate on tasks outside it. You don't need to enforce this manually, but be aware: if a user asks you to "generate the image for that other project's task," the script will fail loudly. That's correct. Don't try to work around it.

---

## Step 1: Identify the trigger

Three input shapes:

1. **Task ID**: user gives a ClickUp task ID. The cleanest case.
2. **Post markdown file** from `/output/posts/`: look in the metadata for the `**ClickUp Task:**` line. If absent, ask the user for the task ID.
3. **Manual prompt** (no task): ask the user whether to create a task first. The image generator only operates on ClickUp tasks; if the user just wants a one-off render, see "Off-task generation" below.

---

## Step 2: (Optional) Review the inputs before running

If the user asks you to "check this before running," fetch the task and review:

- **archetype**: matches the post's tone and pillar? Use the table above to sanity-check.
- **Image Prompt** (visual subject): clear, 1-2 sentence scene? Specific enough to avoid generic results? No instructions about text overlays (those belong in text_in_image)?
- **text_in_image** (JSON): valid JSON object? Headlines obey brand rules (no em dashes, correct currency labels, third-person, subject-verb agreement)? Numbers match `key_facts.md`?
- **size**: appropriate for the platform and the archetype?
- **Description (caption)**: any factual claims that need cross-checking against `key_facts.md`?

If anything's off, tell the user and suggest fixes. Don't rewrite their inputs silently. Don't run the script until the inputs are clean.

---

## Step 3: Invoke the script

Use Bash. Always invoke via `py` (Windows). Never reimplement the API calls inline.

```bash
cd "Z:/Business Empire/The Filipino Standard" && py scripts/generate_image.py --task <TASK_ID>
```

Or for a batch run of all eligible tasks:

```bash
cd "Z:/Business Empire/The Filipino Standard" && py scripts/generate_image.py --pending
```

The scheduled `TFS Image Generator` task (every 15 minutes) runs `--pending` automatically. Manual invocation is for when you can't wait.

Capture the output. Surface:

- Where the PNG and sidecar landed (`/output/images/YYYY-MM-DD-slug.png` and `.json`)
- Whether the final QA passed
- Attempt count (1-3) and any QA violations from earlier attempts
- Any fact-check warnings the script emitted
- Confirmation that the Image URL field was set and the file was attached to the task

If QA failed all 3 attempts, the task is now in `NEEDS-REVISION`. Suggest the next action (read sidecar, decide patch vs regenerate).

---

## Step 4: Visual quality check (when the user wants you to eyeball it)

The script's QA is gpt-4o doing pattern-match on rules. It misses things sometimes. If the user wants a second opinion on a passed image, or wants you to look at a failed image before deciding patch-vs-regenerate:

1. Read the PNG using the Read tool (Claude is multimodal).
2. Walk the QA checklist above box by box.
3. Report which checks pass, which fail, and your patch-or-regenerate recommendation with reasoning.

---

## Step 5: Regenerating

If the user wants new attempts (final result was a near-miss, or content changed):

1. **Clear the Image URL field** on the task. The script's idempotency check skips tasks with a populated Image URL.
2. (Optional) **Fix the inputs** if the regeneration is in response to a content change. Adjust the visual_subject, the text_in_image JSON, or the archetype. Save before re-running.
3. **Re-run** the script in `--task` mode.
4. The script will save fresh PNG and sidecar files (same filename pattern, overwritten).

Hard cap: don't ask the user to regenerate more than 3 times on the same prompt. If three rounds all miss in the same way, the **prompt** is the problem. Go back to Content Creation to revise.

---

## Off-task generation (outside the script's default flow)

If the user wants a one-off image with a manual prompt and no ClickUp task:

- **Option A (recommended):** create a stub ClickUp task on list `901614911598` with just the visual_subject and text_in_image, then run `--task`. Keeps everything traceable.
- **Option B (script bypass):** write a small inline Python call to the OpenAI client (using the same archetype template and brand rules appendix from `generate_image.py`) and save to a sensible path. Don't update ClickUp. Be explicit with the user that this output won't be on the board.

Most of the time, Option A is correct. Option B is a power-user escape hatch and should be rare.

---

## What this skill does NOT do

- **Does not call OpenAI directly.** The script does. You invoke the script.
- **Does not write Image Prompts (visual subjects) or text_in_image JSON from scratch.** That's Content Creation. You can flag issues, but rewrites happen upstream.
- **Does not publish.** That's the Publisher skill and `scripts/publisher.py`.
- **Does not write to any ClickUp list other than `901614911598`.** The script refuses. You should too.

---

## Things to avoid

- **Bypassing the script** unless the user explicitly opts into off-task generation. The script is the canonical pipeline; bypassing it skips logging, idempotency, QA, sidecar tracking, and the ClickUp update.
- **Trusting the QA blindly.** Vision QA catches a lot but not everything. Use the human checklist on anything sensitive.
- **Patching when you should regenerate.** Pillow patches are for single small-element typos or off-safe-area labels. Anything bigger is a regeneration. (The brand footer is no longer a patch case - it's stamped deterministically by the pipeline.)
- **Regenerating forever.** Three tries, then the inputs are the problem.
- **Fabricating task IDs.** If the user doesn't give you one and you can't find it in the markdown metadata, ask.
- **Trying to make the script accessible inside Cowork's MCP layer.** It runs on the host as a Python script via Task Scheduler. That separation is intentional.
- **Editing the brand rules appendix.** It's the model's contract. Changes go in code, not in the skill, and only after deliberation.
