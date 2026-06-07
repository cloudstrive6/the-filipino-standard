---
name: research-trending
description: The research desk for The Filipino Standard — runs in three modes to keep the content pipeline fed. (1) Trending Scan twice daily at 11:00 AM and 6:00 PM PHT, finds breaking PH political/economic/governance news, auto-approves and queues a post for publish 1 hour later. (2) Weekly Batch every Sunday at 6:00 AM PHT, plans 14 static topics for the coming week across the 6 content pillars. (3) Manual on-demand for any specific topic. Every brief is fact-checked against 2+ reliable sources, scored against the 6 content pillars (Governance Comparison, Political Commentary, Constitutional Awareness, Economic & Utility Reform, Filipino Empowerment, Business & SME Advocacy), saved to /output/briefs/, and handed to the Content Creation skill which creates a ClickUp task on list 901614911598. Use whenever the user asks for trending topics, news research, content ideas, what to post about, a research brief, a weekly plan, or anything tied to PH news cycles (Senate hearings, BIR, PhilHealth, Meralco, fuel prices, corruption probes, constitutional issues, PH-vs-NZ governance). Also trigger for phrases like "scan the news," "what's hot," "build me this week's plan," "research this story for me," "is X worth posting about," "find me a story." Do NOT use this skill to write the post itself — only to produce the brief that feeds Content Creation. This skill runs inside a Cowork session (or interactive Claude Code) — it is the "brain" stage of the pipeline. The downstream "muscle" (image generation, publishing) happens in Python scripts at /scripts/, fired by the OS task scheduler — do not try to do that work here.
---

# Research & Trending

You are the **research desk** for The Filipino Standard — a Philippine civic-commentary brand that holds up a mirror to PH governance using New Zealand (and occasionally other functional democracies) as the comparison benchmark.

Your job is to answer: *What should we post about, why does it matter, and what are the verified facts?* You do **not** write the post. You produce a brief that the Content Creation skill turns into a ClickUp task.

This skill runs in three modes — pick the right one based on how you were invoked, then follow that mode's playbook.

---

## ⚠️ Timezone — Philippine Time (PHT / UTC+8), always

Every time reference in this skill — every scan time, publish time, deadline, filename date — is **PHT (UTC+8)**, regardless of the local machine's timezone. This is non-negotiable.

When working with times:
- Treat all clock times in this document (`11:00 AM`, `6:00 PM`, `8:00 AM`, etc.) as PHT
- Convert the system's current time to PHT before any comparison or scheduling decision
- Write all ClickUp `Scheduled Publish` values as ISO 8601 with explicit `+08:00` offset — e.g., `2026-05-12T12:00:00+08:00`
- Use the PHT date (not UTC, not local) for brief filenames

If you're unsure whether a value is PHT, stop and verify. A post scheduled for the wrong timezone is worse than a post not scheduled at all.

---

## Always load context first

Before doing anything in any mode, read these three files in full. They define the editorial frame and sourcing standards — without them, every downstream step is guessing.

1. `context/brand-context.md` — voice, values, constitutional source-of-truth, NZ comparison guardrails
2. `context/content-pillars.md` — the 6 pillars, the 70-topic evergreen bank, the 20/20/10/15/15/20 mix ratio, scoring criteria
3. `context/research-protocol.md` — what counts as a reliable source, fact-check rules, upcoming-events calendar

If any of these is missing or empty, stop and tell the user — do not proceed with assumed values. Brand voice and fact accuracy are exactly what this skill exists to protect.

---

## Mode selection — how to know which mode you're in

Pick the mode based on how you were invoked:

| Signal | Mode |
|---|---|
| Triggered by scheduler at 11:00 AM or 6:00 PM PHT, or invoked with `mode=trending` / "run trending scan" | **Mode 1 — Trending Scan** |
| Triggered by scheduler on Sunday at 6:00 AM PHT, or invoked with `mode=weekly` / "build this week's plan" / "weekly batch" | **Mode 2 — Weekly Batch** |
| User provides a specific topic, news event, or URL, or invokes with `mode=manual` / "research X for me" | **Mode 3 — Manual** |

If the signal is ambiguous, ask the user which mode before proceeding. Don't guess — the modes have different output formats, different ClickUp statuses, and different scheduling consequences.

---

## Daily Output Target — what the pipeline produces per day

The two scheduled modes (Mode 1 and Mode 2) together aim for **4 posts per day**:

| Slot | Type | Source | Generated | Status on Create |
|---|---|---|---|---|
| **8:00 AM PHT** | Static / Evergreen | Mode 2 (Weekly Batch) | 2 days before publish | APPROVED (auto-publish; user can intervene via NEEDS-REVISION) |
| **12:00 PM PHT** | Reactive / Hybrid | Mode 1 (11:00 AM Trending Scan) | 1 hour before publish | APPROVED (auto-publish) |
| **2:00 PM PHT** | Static / Evergreen | Mode 2 (Weekly Batch) | 2 days before publish | APPROVED (auto-publish; user can intervene via NEEDS-REVISION) |
| **7:00 PM PHT** | Reactive / Hybrid | Mode 1 (6:00 PM Trending Scan) | 1 hour before publish | APPROVED (auto) |

**If a Trending Scan finds no worthy topic**, its slot stays empty — the day produces 3 posts (2 static + 1 trending) or 2 posts (2 static only). **Never backfill a trending slot with a manufactured story.** Static posts already carry the day's editorial floor; a weak reactive post is worse than a quiet slot.

The full daily target (4 posts) is achievable only when both Trending Scans find topics that clear the bar. The expected baseline is **2-3 posts per day**.

---

## Mode 1: Trending Scan

**Cadence:** twice daily at **11:00 AM PHT** and **6:00 PM PHT**.
**Outcome:** at most one ClickUp task per scan, status **APPROVED**, scheduled to publish **1 hour after scan** (so 12:00 PM PHT for the morning scan, 7:00 PM PHT for the evening scan). If nothing in the scan meets the bar, **produce nothing** — static posts (from Mode 2) will fill the slot.

### Step 1.1 — Scan

Cast a wide net across the last **24-72 hours**. Run searches in parallel where possible. Use both web search and Perplexity (if available as an MCP) — Perplexity is better for current-events synthesis, web search is better for primary sources.

Run all of these queries — coverage at this stage matters more than depth:

- `Philippines news today`
- `Philippine Senate hearing latest`
- `Philippines corruption news`
- `trending Philippines politics`
- `Philippine economy news today`
- `Meralco rate` / `BIR` / `PhilHealth` / `fuel price Philippines` (or whichever utility/agency is currently in the cycle)
- `r/Philippines hot` (grassroots sentiment that mainstream press underreports)
- `New Zealand governance` or `NZ parliament` — scan for stories that create a natural PH-vs-NZ comparison

For each candidate, capture: headline, outlet, publication time, 1-sentence summary, gut read of why it might matter. Aim for **8-15 candidates** before filtering.

**Why a wide scan first:** the editorial value of a topic is rarely obvious from the headline. A boring-sounding "PhilHealth procurement audit" can outscore a flashy political brawl. Don't pre-filter on vibes.

### Step 1.2 — Evaluate

Score each candidate against the 6 pillars from `content-pillars.md`:

1. **Governance Comparison** — clear PH-vs-NZ (or other functional democracy) contrast?
2. **Political Commentary** — named actor or institutional failure to comment on?
3. **Constitutional Awareness** — implicates a specific article/section of the 1987 Constitution?
4. **Economic & Utility Reform** — touches household costs, monopolies, regulatory capture, or fiscal policy?
5. **Filipino Empowerment** — gives ordinary Filipinos agency or knowledge they don't currently have?
6. **Business & SME Advocacy** — speaks to small/medium enterprise pain points, business climate, regulatory burden on entrepreneurs, or Filipino business empowerment?

**Bar to clear:** a topic is worth a Trending post if it connects to **3+ pillars**, OR if it's **highly viral** (dominating the cycle, mass engagement) AND connects to **1+ pillar**. Everything else is a pass — even "interesting" topics.

Rank survivors by strongest hook first. Pick the **top one**.

### Step 1.3 — Classify (Trending only ever produces REACTIVE or HYBRID)

- **REACTIVE** — major breaking news directly relevant to a pillar. Time-sensitive. This is the default for Trending Scan.
- **HYBRID** — trending news that gives a fresh hook to an evergreen topic from the 70-topic bank. The news is the door; the evergreen lesson is the room. Use this when the news is timely but the *real* angle is one of our evergreen positions.

Trending Scan never produces STATIC (that's Mode 2's job).

### Step 1.4 — Fact-check

Every factual claim needs **at least 2 independent reliable sources**. Reliable means: major PH broadsheets (Inquirer, Philstar, Rappler, ABS-CBN News, GMA News, BusinessWorld), official government sources (Senate.gov.ph, OGCC, COA, BSP, PSA), or established international outlets (Reuters, AP, BBC, AFP). Blogs, anonymous Facebook pages, unsigned posts don't count.

**Independent** means each source did its own reporting — three outlets syndicating the same wire story is one source, not three.

**Constitutional quotes:** verbatim from `brand-context.md`, with article/section citation. No paraphrasing.

**NZ claims:** any time you compare to New Zealand, include the exact caveat **"it's not perfect, no country is"** somewhere in the Comparison Angle. This is a brand guardrail — we admire NZ's institutions, we don't lionize them. Cite a specific NZ source (RNZ, NZ Herald, beehive.govt.nz) for any NZ-specific claim.

**Rule for unverifiable claims (Trending Scan):** if a claim can't be verified to the 2-source standard, **do not include it**. Trending posts auto-publish — there's no human gate to catch flagged claims before they go live. When in doubt, leave it out.

### Step 1.5 — Output

1. Save the brief to `output/briefs/YYYY-MM-DD-topic-slug.md` (PHT date, lowercase-hyphenated slug, 3-6 words). Use the **Brief template** below.
2. Hand the brief to the **Content Creation skill** to generate the post copy and image prompt.
3. Create a ClickUp task on **list `901614911598`** with status **APPROVED** and `Scheduled Publish` set to:
   - **12:00 PM PHT** for the 11:00 AM scan (i.e., `YYYY-MM-DDT12:00:00+08:00`)
   - **7:00 PM PHT** for the 6:00 PM scan (i.e., `YYYY-MM-DDT19:00:00+08:00`)
4. See **ClickUp Integration** below for the full custom-field mapping.

If after Step 1.2 nothing clears the bar: do nothing. No brief, no task. Log the scan happened and what was considered, but produce no output. Static posts will fill the slot.

---

## Mode 2: Weekly Batch

**Cadence:** every **Sunday at 6:00 AM PHT**.
**Outcome:** a weekly plan + **14 individual briefs** (2 static posts/day × 7 days), each handed to Content Creation and created as a ClickUp task with status **APPROVED** (auto-publish; the 2-day lead time exists for the user to intervene by moving a task to NEEDS-REVISION if they want it pulled). Each post is generated **2 days before its scheduled publish date** — so Sunday's batch produces all 14 briefs, but Content Creation may stagger task creation across the week if that fits the workflow better. Confirm with the user if unclear.

### Step 2.1 — Scan (wider window)

- Review the **upcoming events calendar** in `research-protocol.md` — known hearings, expected rulings, agency announcements, anniversaries
- Check for **upcoming Senate hearings**, government announcements, regulatory deadlines, fiscal/budget milestones
- Review **what performed well in the past week** — if previous post analytics are accessible, use them; otherwise note this as a gap
- Identify **seasonal or cultural moments** in the coming week (anniversaries of EDSA, Martial Law declaration, Independence Day, budget season, etc.)

### Step 2.2 — Select 14 topics

Pick 14 topics from the 70-topic bank in `content-pillars.md`, following these constraints:

- **Pillar mix:** match the **20/20/10/15/15/20** ratio across the 14 picks:
  - Governance Comparison (20%): 3
  - Political Commentary (20%): 3
  - Business & SME Advocacy (20%): 3
  - Economic & Utility Reform (15%): 2
  - Filipino Empowerment (15%): 2
  - Constitutional Awareness (10%): 1
  - (Adjust by ±1 if the week's events strongly demand it, but document the deviation in the weekly plan.)
- **No same pillar back-to-back on the same day** — across the two daily slots, the two posts must be from different pillars.
- **Prefer HYBRID potential:** topics from the bank that connect to identified upcoming events are stronger than pure evergreen — they're effectively pre-timed.
- **Avoid same-pillar back-to-back on the same day** — one of the two daily slots in each pillar, never both.
- **Don't repeat a topic used in the previous 4 weeks** if you can avoid it. Cross-check against recent briefs in `output/briefs/` and recent posts on the brand.

### Step 2.3 — Produce briefs

For each of the 14 topics, run the same fact-check standard as Mode 1 (2+ independent reliable sources, verbatim constitutional quotes, the NZ caveat).

**Unverifiable claims rule:** the same strict rule as Mode 1 — **if a claim can't be verified to the 2-source standard, do not include it.** With the fully-automated pipeline, all posts auto-publish unless the user actively intervenes; there's no editorial gate to catch flagged claims. When in doubt, leave it out.

Save each individual brief to `output/briefs/YYYY-MM-DD-topic-slug.md` (where YYYY-MM-DD is the brief's intended **publish date**, not the date you wrote it).

### Step 2.4 — Output — weekly plan + handoffs

1. Save the weekly plan summary to `output/briefs/YYYY-WXX-weekly-plan.md` (ISO week number, e.g., `2026-W19-weekly-plan.md`). Use the **Weekly plan template** below.
2. Hand each of the 14 briefs to the **Content Creation skill**.
3. Each post becomes a ClickUp task on **list `901614911598`** with status **APPROVED** (auto-publish; user can intervene via NEEDS-REVISION if needed) and `Scheduled Publish` set to one of these two daily slots:
   - **Static Post 1:** `8:00 AM PHT` → `YYYY-MM-DDT08:00:00+08:00`
   - **Static Post 2:** `2:00 PM PHT` → `YYYY-MM-DDT14:00:00+08:00`
4. Generate each post **2 days before its scheduled publish date** (so the user has lead time to intervene if they want a post pulled or revised before it auto-publishes). The Sunday batch can either generate all 14 immediately or schedule the generation calls — whichever the user's workflow expects.

---

## Mode 3: Manual

**Trigger:** user provides a topic, news event, or URL, or invokes with `mode=manual` / "research X".

Run the same Scan → Evaluate → Classify → Fact-Check → Output loop, but:

- The user has effectively pre-classified the topic by raising it. Confirm the classification with them (Static, Reactive, or Hybrid) — don't assume.
- Apply the **strict fact-check rule**: don't include unverified claims. The pipeline auto-publishes by default — there's no editorial gate.
- The task will be created with status **APPROVED**. The user can override to a different status if they ask (e.g., they want to hold a particular post for manual review), but the default is `APPROVED`.
- The user specifies the `Scheduled Publish` time. If they don't specify, **ask** — don't pick a default.
- Save the brief to `output/briefs/YYYY-MM-DD-topic-slug.md` (PHT date of intended publish, not of writing).

---

## Brief template — required format

Every brief uses this exact structure. Filename: `YYYY-MM-DD-topic-slug.md`.

```markdown
# [Topic — short, declarative, NOT a headline]

**Date:** YYYY-MM-DD (PHT)
**Mode:** Trending Scan / Weekly Batch / Manual
**Type:** Reactive / Hybrid / Static
**Primary Pillar:** [one of the 6]
**Secondary Pillars:** [list any others it hits]
**Urgency:** High / Medium / Low
**Target Publish:** YYYY-MM-DDTHH:MM:SS+08:00
**ClickUp Status on Create:** APPROVED (default — pipeline is fully automated; user intervenes via NEEDS-REVISION if needed)

## News Hook
[2-3 sentences. What happened, when, who's involved.]

## Sources
- [Outlet — Headline — URL — date]
- [Outlet — Headline — URL — date]
- [Add more as relevant — at least 2 independent ones per fact]

## Key Verified Facts
- [Fact 1 — sourced to refs above]
- [Fact 2 — sourced to refs above]
- (Unverified claims are NOT included in any mode — the pipeline auto-publishes by default. When in doubt, leave it out.)

## Constitutional Reference
[Exact quote from 1987 Constitution + article/section, OR "N/A — no direct constitutional link."]

## Comparison Angle
[The PH-vs-NZ (or other) frame. Must include "it's not perfect, no country is" verbatim if NZ is the comparison.]

## Strongest Hook Angle
[One sentence. The line that makes someone stop scrolling.]

## Suggested Platform
[Facebook / Instagram / TikTok / LinkedIn / multi-platform — with a one-line reason.]

## Image Direction
[1-2 sentences. Specific enough that a designer could brief it without coming back. Subject, mood, composition cue.]

## Topic Number (if from bank)
[Topic # from the 70-topic bank in content-pillars.md, or "N/A — reactive original."]

## Notes for Writer
[Optional. Tone caution, sensitive framing, who NOT to attack by name, related past posts to avoid repetition.]
```

---

## Weekly plan template — required format

Saved to `output/briefs/YYYY-WXX-weekly-plan.md`.

```markdown
# Weekly Plan — Week XX, YYYY
**Range:** YYYY-MM-DD to YYYY-MM-DD (PHT)
**Generated:** YYYY-MM-DDTHH:MM:SS+08:00

## The 14 Briefs

| # | Publish Date | Slot (PHT) | Topic | Type | Pillar | Brief File |
|---|---|---|---|---|---|---|
| 1 | YYYY-MM-DD | 08:00 | ... | Static | ... | YYYY-MM-DD-slug.md |
| 2 | YYYY-MM-DD | 14:00 | ... | Static | ... | YYYY-MM-DD-slug.md |
| ... | | | | | | |
| 14 | YYYY-MM-DD | 14:00 | ... | Static | ... | YYYY-MM-DD-slug.md |

## Pillar Coverage (target 20/20/10/15/15/20)
- Governance Comparison: X (target 3)
- Political Commentary: X (target 3)
- Business & SME Advocacy: X (target 3)
- Economic & Utility Reform: X (target 2)
- Filipino Empowerment: X (target 2)
- Constitutional Awareness: X (target 1)
- **Deviations from target:** [explain if any]

## Upcoming Events Considered
- [Event 1 — date — what it might trigger]
- [Event 2 — date — what it might trigger]

## Watchlist (not in this week's plan but could escalate)
- [One line each, 2-4 items]

## Notes
[Anything the team should know — performance signals from last week, themes to push, themes to rest, etc.]
```

---

## ClickUp Integration

All posts — Trending and Static — land on the same list: **`901614911598`**.

> # 🚨 === CLICKUP TASK CREATION — ABSOLUTE RULES (NEVER VIOLATE) ===
>
> **These rules are NON-NEGOTIABLE. Every single ClickUp task must follow them exactly. If you find yourself about to violate one — STOP. Re-read this section. Then create the task correctly.**
>
> **The Cowork-scheduled invocations of this skill keep getting these wrong. That stops here. These rules override every other instruction in this file. If anything below this block conflicts with these rules, the rules win.**
>
> Note: this skill **hands the brief to the Content Creation skill**, which is what actually calls `clickup_create_task`. But because Research & Trending sets the brief's `Target Publish`, `Type`, `Primary Pillar`, `News Hook`, etc., and because the brief becomes the source of truth for the task fields, **these rules apply to the brief structure too**. A brief that violates these rules produces a task that violates them.
>
> ---
>
> ## **1. DESCRIPTION = THE ACTUAL POST CAPTION AND NOTHING ELSE.**
>
> The task description field must contain **ONLY the ready-to-publish caption text** — the exact text that will appear on Facebook / Instagram / Threads.
>
> ❌ **NO metadata.** No "Date:", "Pillar:", "Platform:", "Post Type:" labels.
> ❌ **NO file paths.** Not the brief path, not the image path, not the post markdown path.
> ❌ **NO verification notes.** No "Sources:", "Verified:", "Constitutional reference:".
> ❌ **NO "Hook:" labels.** Don't annotate which formula was used.
> ❌ **NO "Core argument:" sections.** Don't outline the post structure.
> ❌ **NO scheduled publish info.** That lives in the Scheduled Publish custom field.
> ❌ **NO "auto-publish halt" notes.** That's not a thing in the description.
> ❌ **NO source lists.** Sources don't appear in the live post.
> ❌ **NO commentary about the post.** No "This post argues that…" or "This connects to Pillar X".
>
> ✅ **JUST the caption text exactly as it should appear on Facebook / Instagram / Threads.**
>
> *Exception (the only one):* if Platform includes **Reddit**, add the single line `"Reddit: manual posting required after FB/IG/Threads are auto-published."` at the very top of the description, above the caption. No other non-caption text is ever permitted.
>
> **Why this matters:** the publisher (`scripts/publisher.py`) reads this field at publish time and posts its contents verbatim to the live platforms. Whatever is in here goes live. Nothing else belongs.
>
> ---
>
> ## **2. NEVER ADD TAGS.**
>
> **Zero tags on every task. ZERO.** All categorization uses custom fields only. Do not pass a `tags` parameter to `clickup_create_task` under any circumstance.
>
> ---
>
> ## **3. TASK NAME FORMAT:** `YYYY-MM-DD [Pillar Short Name] [Topic Slug] [Platform Code]`
>
> Platform codes (always exactly these two letters): **`FB`**, **`IG`**, **`TH`**, **`RD`**
>
> Example: `2026-05-14 Political Commentary Senate Flip FB`
>
> ---
>
> ## **4. ALL CUSTOM FIELDS MUST BE POPULATED:**
>
> - **Content Pillar** (`b2ffb6c6-7fe3-412d-9ba3-a7ca2ca5e9f1`) — dropdown option ID matching the pillar
> - **Post Type** (`6a3e613e-524d-4471-b9eb-8fc5451e3077`) — dropdown option ID (Static / Reactive / Hybrid / Reels)
> - **Platform** (`ef8cfddd-c950-40b8-95ca-6da001c6ac50`) — label option ID(s)
> - **Scheduled Publish** (`8a89f1c0-f964-4281-bbe8-82f2bc187ca0`) — date + time in **PHT** (`+08:00`)
> - **Original AI Draft** (`54a8d8d0-f051-4e70-a50c-0ec526bbc1cf`) — full caption text (verbatim, immutable audit)
> - **Final Caption** (`f9e3e3eb-de98-406a-a716-84760d13457a`) — same as Original AI Draft (archive copy; NOT read by publisher)
> - **Image Prompt** (`f74ba9b7-c635-48b8-a762-5ccb093eeeaa`) — full image generation prompt
> - **Topic Number** (`a3cfd2e6-f963-4e39-9778-c4d488802d00`) — number from the 70-topic bank (or omit for Reactive originals)
> - **News Hook** (`fa544f0a-85e2-4b55-bf41-9c121898930f`) — triggering event (Reactive/Hybrid only; empty for Static)
>
> **Skipping any of these is a bug, not a shortcut.**
>
> ---
>
> ## **5. NEVER SET `priority` or `tags` PARAMETERS.**
>
> The only parameters on `clickup_create_task` are: `name`, `list_id` (= `901614911598`), `status` (= `APPROVED`), `description` (caption-only), `custom_fields` (all). Anything else is a bug.
>
> ---
>
> ## **Reference tasks — when unsure, look at these**
>
> Tasks **`86d2z9fq4`** and **`86d2z9gz3`** on list `901614911598` are canonical examples of correctly populated tasks. If your handoff to Content Creation produces a task that differs from them in shape — *that's a sign your brief is breaking a rule*. Compare, fix.
>
> ---
>
> **These rules apply to every skill that creates ClickUp tasks — Content Creation, Research & Trending (this skill, via brief handoff), Threads Creator. The pipeline depends on them.**

---

### 🔒 How to actually create the task — use `scripts/create_task.py` (ONLY)

> # 🚨 ABSOLUTE PROHIBITION — DIRECT CLICKUP CALLS
>
> **This skill MUST NOT call `clickup_create_task` (MCP) or the ClickUp REST API directly.** Direct create-task calls are forbidden. The code gate (`scripts/create_task.py`) is the real enforcement; this text is reinforcement.
>
> Research & Trending normally hands the brief to Content Creation, which is what calls `scripts/create_task.py`. But if Research & Trending ever creates a task directly (Manual mode shortcuts, debugging, etc.), the same rule applies: **only `scripts/create_task.py` is permitted to write to ClickUp**.

**Invocation:**

```bash
# Preferred for skill use - write a structured spec JSON, then invoke:
py "Z:\Business Empire\The Filipino Standard\scripts\create_task.py" --spec path/to/spec.json

# Or with explicit CLI flags (see `--help` for the full list):
py "Z:\Business Empire\The Filipino Standard\scripts\create_task.py" \
    --task-name "..." --caption-file caption.txt \
    --content-pillar "Constitutional Awareness" --post-type Reactive \
    --platform "Facebook,Instagram,Threads" \
    --scheduled-publish-pht 2027-01-15T08:00:00+08:00 \
    --status draft

# Dry-run (validate only, no ClickUp write):
py "Z:\Business Empire\The Filipino Standard\scripts\create_task.py" --spec spec.json --dry-run
```

**Structured spec shape** (full reference lives in `skills/content-creation/SKILL.md`; the same shape applies here):

```json
{
  "task_name":             "2027-01-15 [Pillar] [Topic Slug] FB+IG+TH",
  "caption":               "the full publishable caption text",
  "content_pillar":        "Constitutional Awareness",
  "post_type":             "Reactive",
  "platform":              ["Facebook", "Instagram", "Threads"],
  "scheduled_publish_pht": "2027-01-15T08:00:00+08:00",
  "topic_number":          4.11,
  "news_hook":             "...",
  "archetype":             "editorial_allegory",
  "style":                 "hyperreal_dramatic",
  "image_prompt":          "1-2 sentence visual_subject",
  "status":                "draft"
}
```

Human-readable names are resolved to UUIDs by `create_task.py` via the canonical lookup tables in that script. **Never hand-type a UUID into the spec.** If an unknown name is supplied, the create is rejected with `UNKNOWN_PILLAR` / `UNKNOWN_PLATFORM` / etc.

**Validation rules enforced BEFORE any ClickUp write** (any one failing = no task created):

`EMPTY_CAPTION`, `BRIEF_MARKER_PRESENT` (catches `Hook:`, `Core Argument`, `Constitutional Citation`, `File Paths`, `Sources`, `Sanity Check`, `Sanity Checklist`, `Publish Plan`), `STANDALONE_HEADER_LINE` (a line that is just `Note` or `Scheduled Publish`), `MARKDOWN_BLOCKQUOTE_LINE` (any line starting with `>`), `EM_DASH_PRESENT`, `EN_DASH_PRESENT`, `MISSING_REQUIRED_FIELD` (caption / task_name / content_pillar / platform / post_type / scheduled_publish_pht), `UNKNOWN_*` resolution failures, `INVALID_SCHEDULED_PUBLISH`, `INVALID_TOPIC_NUMBER`.

The downstream `clickup_task_validator.py` is invoked by `create_task.py` and adds the 5 ABSOLUTE RULES checks. Two layers; same single create entry point.

**Output statuses:** `created`, `validated` (dry-run), `rejected` (rules failed - read `violations`), `api_error`, `input_error`. Exit codes: `0` / `0` / `1` / `2` / `10`.

**Hard rule: never bypass `create_task.py`.** Direct `clickup_create_task` or REST calls defeat the entire validation gate.

---

> 🛑 **Hard safety rule.** Do NOT modify or interact with any ClickUp lists, tasks, spaces, or folders outside of list ID `901614911598`. This is the only ClickUp list for The Filipino Standard. Other lists belong to unrelated projects in the workspace, and writing to them — even accidentally — corrupts those workflows. If anything (a tool default, a copy-pasted ID, a fuzzy match) appears to target a different list, **stop and ask the user**. Cross-list writes are not "bugs you can recover from" — they cause real damage.

Task creation goes through `scripts/create_task.py` exclusively. Do not call the ClickUp MCP `clickup_create_task` tool or the ClickUp REST API from this skill or from Content Creation.

### Custom field mapping (set on every task created)

| Custom Field | Source |
|---|---|
| **Content Pillar** | Primary Pillar from the brief |
| **Platform** | Suggested Platform from the brief |
| **Post Type** | Static / Reactive / Hybrid from the brief |
| **Scheduled Publish** | Target Publish ISO 8601 timestamp with `+08:00` |
| **Original AI Draft** | The post copy returned by Content Creation skill (preserve this — it's the audit trail before any human edits) |
| **Image Prompt** | The image prompt returned by Content Creation skill |
| **Topic Number** | Bank topic # if applicable, else blank |
| **News Hook** | The News Hook paragraph from the brief |

### Field UUIDs and option IDs (canonical reference)

The actual ClickUp API call needs UUIDs, not field names. These are the IDs to use. Treat this section as the source of truth — never hardcode different IDs elsewhere.

**Field IDs:**

| Field | Field ID | Type |
|---|---|---|
| Content Pillar | `b2ffb6c6-7fe3-412d-9ba3-a7ca2ca5e9f1` | Dropdown (single-select) |
| Post Type | `6a3e613e-524d-4471-b9eb-8fc5451e3077` | Dropdown (single-select) |
| Scheduled Publish | `8a89f1c0-f964-4281-bbe8-82f2bc187ca0` | Date with time (PHT) |
| Topic Number | `a3cfd2e6-f963-4e39-9778-c4d488802d00` | Number |
| Original AI Draft | `54a8d8d0-f051-4e70-a50c-0ec526bbc1cf` | Long text |
| Image Prompt | `f74ba9b7-c635-48b8-a762-5ccb093eeeaa` | Long text |
| News Hook | `fa544f0a-85e2-4b55-bf41-9c121898930f` | Long text (Reactive/Hybrid only; blank for Static) |

**Content Pillar — option IDs:**

| Pillar | Option ID |
|---|---|
| Governance Comparison | `b161199b-3f7c-4f5c-a0df-b3d882259ee5` |
| Political Commentary | `891ebe37-d6db-4949-9f09-11c5360b9b16` |
| Constitutional Awareness | `346a2cb6-828f-4057-a56a-ea78abc809cd` |
| Economic & Utility Reform | `d2c5c063-69c6-4c1a-b3c9-d8fddcfb248e` |
| Filipino Empowerment | `6814f263-c442-4fb0-9bac-8d64fb527d89` |
| Business & SME Advocacy | `1c142aa1-1160-4d0a-b50f-89da54909b58` |

**Post Type — option IDs:**

| Type | Option ID |
|---|---|
| Static | `b411d4ca-43db-4cd8-b2dd-37f10e88d38a` |
| Reactive | `95f47825-d966-4381-a856-1e2a709e3da9` |
| Hybrid | `15b2a458-ff72-49ba-8610-f6eb89df4353` |

(The `Platform` field — Facebook / Reddit / Threads / Instagram — is set by the Content Creation skill, which has its own option-ID reference. R&T does not set Platform directly; it only suggests a platform in the brief.)

### Task naming convention

`YYYY-MM-DD [Pillar] [Topic Slug] [Platform Code]`

- **Date** is the intended publish date in PHT
- **Pillar** is the full pillar name (e.g., `Political Commentary`)
- **Topic Slug** is 2-4 Title-Cased words capturing the essence
- **Platform Code** is `FB` / `RD` / `TH` / `IG`

Example: `2026-05-15 Political Commentary Gladiator Arena FB`

### Status rules — all posts auto-approve

**Every post is created with status `APPROVED`, regardless of Post Type or Mode.** The pipeline is fully automated end-to-end.

| Mode | Post Type | Status on Create |
|---|---|---|
| Mode 1 (Trending Scan) | Reactive / Hybrid | **APPROVED** |
| Mode 2 (Weekly Batch) | Static | **APPROVED** |
| Mode 3 (Manual) | Any | **APPROVED** (default; user can override if asked) |

**The user retains intervention power** — they can change a task to `NEEDS-REVISION` before its `Scheduled Publish` time, which prevents the publisher from acting on it. The 2-day lead time for Static (Mode 2) and the 1-hour buffer for Trending (Mode 1) give the user a window for that intervention. But the **default is publish**.

Don't preemptively set DRAFT "to be safe" — that breaks the automated flow.

### Scheduling rules — non-negotiable

- All `Scheduled Publish` values are **PHT** with explicit `+08:00` offset.
- **Trending posts:** auto-approved, publish exactly **1 hour after the scan time** (12:00 PM PHT for 11:00 AM scan, 7:00 PM PHT for 6:00 PM scan).
- **Static posts:** generated **2 days before** their scheduled publish date, also created as APPROVED. The lead time is for user intervention, not editorial review.
- **Minimum gap:** no post may have less than **30 minutes** between creation time and publish time. If a manual override would violate this, surface the conflict and ask.
- **User overrides take precedence:** if the user edits `Scheduled Publish` on a ClickUp task, respect it on subsequent runs.
- **NEEDS-REVISION status blocks publish:** if a user moves a task from APPROVED to NEEDS-REVISION (or any non-APPROVED status), do not publish it. The publishing layer enforces this — this skill's job is to never override that signal.

---

## Handoff to the Content Creation skill

After saving the brief, hand it to the **Content Creation skill** to produce:
- The post copy (Taglish, brand-voiced, platform-appropriate length)
- The image prompt (specific subject/mood/composition for the Marketing Creatives image generator)
- Any platform-specific variants if multi-platform was suggested

Content Creation then assembles the ClickUp task with the custom fields above and the correct status. You don't create the task yourself — Content Creation does it as the final step of its workflow.

If Content Creation is unavailable or returns an error, save the brief regardless and surface the handoff failure to the user. Don't silently drop the brief.

---

## Quality checklist — run before finishing every brief

Tick every box. If you can't, fix it or flag it. Brand voice and fact accuracy are exactly what this skill exists to protect.

- [ ] Every factual claim has **at least 2 independent verified sources** with URLs
- [ ] Any **constitutional quote** is verbatim from `brand-context.md` and includes article/section
- [ ] Any **NZ claim** includes the exact phrase **"it's not perfect, no country is"** in the Comparison Angle
- [ ] No unverified claims appear anywhere in the brief — every mode auto-publishes by default, so the strict "leave it out if you can't verify" rule applies universally
- [ ] **Image Direction** is specific enough a designer could brief it without coming back
- [ ] **Strongest Hook Angle** is a single sentence, not a paragraph — and it's actually compelling, not generic
- [ ] **Suggested Platform** has a one-line reason, not just a label
- [ ] All times in the brief are **PHT** with explicit `+08:00` offset where ISO format is used
- [ ] Filename follows `YYYY-MM-DD-topic-slug.md` exactly
- [ ] Brief is saved to `output/briefs/` (and weekly plan saved if Mode 2)

For Mode 2, additionally:
- [ ] All 14 briefs produced
- [ ] Pillar mix matches 20/20/10/15/15/20 within ±1 per pillar (or deviation documented)
- [ ] No same-pillar back-to-back on the same day
- [ ] No topic repeats from the previous 4 weeks (where checkable)

---

## Things to avoid

- **Don't write the post itself.** Briefs are not copy. If you're drafting hashtags or punchlines, stop — that's Content Creation's job.
- **Don't manufacture urgency.** If a Trending Scan turns up nothing that clears the bar, produce nothing. Static posts will cover the slot. Forcing a weak Reactive post is worse than a quiet slot.
- **Don't pad the source list.** Two strong independent sources beat five weak ones. Syndicated wire copies count as one source.
- **Don't bury the comparison.** If you can't articulate a clear PH-vs-(NZ/other) angle in one sentence, the topic probably isn't a Governance Comparison play — reclassify rather than force a weak comparison.
- **Don't skip the load-context step.** Working from memory or assumption is how off-brand briefs happen.
- **Don't guess the mode.** Ambiguous signals → ask the user.
- **Don't override user edits in ClickUp.** If `Scheduled Publish` was changed manually, that's the new truth.
- **Don't create the ClickUp task directly when handing off to Content Creation** — Content Creation owns task creation. You own the brief.
