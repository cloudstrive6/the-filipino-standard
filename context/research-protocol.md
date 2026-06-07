# The Filipino Standard - Research Protocol

> How the AI research agent (and any human collaborator) should research, fact-check, and prepare a topic before content writing begins. Nothing gets written without going through this protocol.

---

## 1. The 3 Content Types

Every TFS post falls into one of three categories. The category determines the speed, format, and structure.

### 1.1 Reactive (publish within hours)

Triggered by breaking news that intersects our pillars: a major government scandal, a viral political moment, a court ruling, a Meralco rate hike announcement, a typhoon response, a Senate hearing clip.

- **Speed:** Within 2-6 hours of the news breaking
- **Format:** Short, sharp, opinion-led, single platform (usually FB or Threads first)
- **Risk:** Highest. Facts are still settling. Use extra source verification.
- **Tone:** Calm, not hot-take. We are not a meme page racing for clicks. We are the page that says it well, not the page that says it first.

### 1.2 Hybrid (24-48 hours)

A news event with an evergreen angle. Example: A Meralco rate hike (reactive trigger) becomes a deeper post on Entrust dividends, electric cooperatives, and the Constitution on public utilities (evergreen angle).

- **Speed:** 24-48 hours from news event
- **Format:** Long-form FB post, often cross-posted to Reddit with structure added
- **Strength:** This is our **highest-impact category.** We connect the news cycle to the deeper story we've been telling all along.
- **Goal:** Make the reader say *"this isn't a one-off scandal. It's a pattern."*

### 1.3 Static (scheduled from the topic bank)

Pulled from the 70-topic bank in `content-pillars.md`. Pre-planned. Pre-researched. Not tied to news.

- **Speed:** Scheduled in advance, posted on calendar
- **Format:** Any platform. Often the most polished, since there's time.
- **Use:** Fills the calendar between reactive and hybrid posts so we never go silent. Builds the long-arc narrative.

---

## 2. The 5-Step Research Process

Every topic, regardless of type, goes through these 5 steps before content writing starts.

### Step 1 - Scan the inputs

Each research run starts with a structured scan of these sources:

**A. Trending PH news (political)**
- Rappler, Inquirer, ABS-CBN News, GMA News, PhilStar
- Senate / Congress press briefings
- Malacanang transcripts (when relevant)

**B. PH economic and utility news**
- BusinessWorld, BusinessMirror
- Energy Regulatory Commission (ERC) announcements
- Meralco investor disclosures (PSE filings)
- DOE, DTI, BSP announcements

**C. Filipino social media pulse**
- What is trending on PH Twitter / X
- Top posts on r/Philippines and r/BusinessPH in the last 24 hours
- TikTok PH political commentary creators
- FB political pages (NOT to copy, only to know what is in the air)

**D. International rankings and indices (monthly minimum)**
- Global Life-Work Balance Index
- Corruption Perceptions Index (Transparency International)
- World Press Freedom Index
- Ease of Doing Business (where still measured)
- Human Development Index
- Worldwide Governance Indicators

**E. NZ comparison angles**
- RNZ, NZ Herald, Stuff for governance stories
- Entrust, Vector, and the other 25 trusts' announcements
- NZ government press releases on policy that would be a PH-comparable story

The output of Step 1 is a list of 3-10 candidate stories or angles for the week.

### Step 2 - Evaluate relevance against the 5 pillars

For each candidate, ask:

1. Which pillar does this fit into? (If none, kill it.)
2. Does it intersect more than one pillar? (Cross-pillar = higher leverage.)
3. Have we covered this in the last 60 days? (Avoid repetition unless the news justifies a callback.)
4. Does the angle reinforce or contradict our existing brand frame?
5. Is there a clean, defensible factual claim at the center?

Kill any candidate that fails 1, 4, or 5. Deprioritize those that fail 2 or 3.

### Step 3 - Determine post type and platform

Based on the candidate's nature:

| Trigger | Type | Primary platform | Secondary |
|---|---|---|---|
| Breaking news, <12 hours old | Reactive | Threads or FB short | (none, then Reddit if it has legs) |
| News + evergreen angle | Hybrid | FB long-form | Reddit (with structure), Threads (clip) |
| Topic-bank topic | Static | FB long-form | Reddit, Threads |
| Personal experience / story | Static / Hybrid | Reddit AMA-style | FB rewrite |

Decisions made here are passed downstream to `platform-guide.md` for formatting.

### Step 4 - Fact-check protocol (NON-NEGOTIABLE)

**Rule of 2:** Every load-bearing fact needs at least **2 independent reputable sources**.

**Source priority (highest to lowest):**

1. **Primary sources / official documents**
   - Government websites: gov.ph, dof.gov.ph, erc.ph, coa.gov.ph, congress.gov.ph, senate.gov.ph
   - NZ equivalents: govt.nz, beehive.govt.nz, stats.govt.nz, ird.govt.nz
   - Official reports: COA audits, ERC orders, ETNZ statistics, PSA data
   - Company disclosures: PSE filings (Meralco, MPIC, JG Summit), NZX filings (Vector)
   - The 1987 Constitution itself for any constitutional claim
2. **Reputable news (broadsheet / wire / public broadcaster)**
   - PH: Rappler, Inquirer, BusinessWorld, ABS-CBN News, GMA News
   - NZ: RNZ, NZ Herald, Stuff
   - International: Reuters, AP, BBC, Financial Times, Bloomberg
3. **Reputable specialist outlets**
   - Energy: Wood Mackenzie, IEA reports, BusinessMirror energy desk
   - Economic: World Bank, IMF, ADB country reports
4. **Reputable indices**
   - The full methodology must be public. We cite the index AND the year.

**Do NOT cite:**
- Anonymous Facebook pages or Telegram channels
- TikTok claims without a verifiable source
- AI-generated summaries (including from us, including from search assistants)
- Wikipedia as a primary source (acceptable as a starting point only, follow the citations)

**If a fact cannot be verified to 2+ reputable sources, the fact is removed or rewritten without the specific claim.** Vague allusions ("some estimates suggest") are not acceptable either - we either have the number or we don't make the claim.

### Step 5 - Produce the research output (handoff document)

The research agent's deliverable is a single markdown brief saved to `/raw/briefs/{YYYY-MM-DD}-{slug}.md` with this exact structure:

```markdown
# Research Brief - [Working Title]

## Topic
[One sentence on what this post is about]

## Type
Reactive | Hybrid | Static

## Pillar
[Primary pillar number and name] (+ secondary pillar if applicable)

## News Hook (if any)
[1-2 lines: what event in the news cycle this responds to, with date]

## Sources (minimum 2 per claim)
- [Source 1, full URL or citation]
- [Source 2, full URL or citation]
- [Source 3, ...]

## Key Facts (verified)
- Fact 1: [exact number / claim], sourced to [source(s)]
- Fact 2: [...]
- Fact 3: [...]

## Constitutional reference (if applicable)
Article __, Section __: "[exact text]"

## Suggested platforms
[Primary] + [Secondary, if any]

## Image direction
[1-3 sentences: what kind of image, what tone, what NOT to include. No fixed template, per `content-creation-guide.md`.]

## Open questions / risks
[Anything the writer needs to know - contested facts, sensitive framing, timing risks]
```

This brief is the only thing the content writer needs. If a writer is asking "where did this number come from?", the brief failed Step 4.

---

## 3. Upcoming Events Calendar (always monitor)

These events are predictable. The research agent should pre-stage brief skeletons for each.

### Annual recurring (PH)

- **SONA (4th Monday of July)** - The State of the Nation Address. Reactive + Hybrid posts on what's promised vs delivered.
- **Budget season (August-December)** - GAA deliberations. Constitutional, transparency, and pork-barrel angles.
- **EDSA Anniversary (February 25)** - People power, sovereignty, the right to assemble.
- **Independence Day (June 12)** - Patriotism reframe, what independence actually means.
- **Bonifacio Day (November 30)** - Empowerment, "demanding better" angle.
- **Tax deadlines (April 15 ITR, quarterly filings)** - BIR vs IRD comparison.
- **PH National Elections (May, every 3 years; next: May 2028 national)** - Heaviest content load.
- **Local elections (Barangay / SK)** - Vote-buying, dynasty angles.

### Annual recurring (NZ / international)

- **Entrust dividend announcement (typically September-October)** - Hybrid post anchor.
- **NZ Budget Day (May)** - Comparison angle if news cycle aligns.
- **International Anti-Corruption Day (December 9)** - Strong pillar 2 + 3 alignment.
- **World Press Freedom Day (May 3)** - Pillar 3.
- **Global Life-Work Balance Index release (typically late in the year)** - Hybrid post anchor.

### Event-triggered (monitor, react)

- **Meralco rate announcements** (monthly, around the 5th-10th)
- **ERC orders / hearings**
- **COA audit report releases** (usually annual cycle, typically August-October)
- **Typhoon season (June-November)** - Disaster response comparison angle (Pillar 1.5)
- **International rankings releases** (vary by index - CPI in January, HDI rolling)
- **Senate hearings on utilities, corruption, or constitutional reform**

When any of these events occur, the research agent immediately produces or updates the relevant brief.

---

## 4. Research Frequency

| Cadence | What |
|---|---|
| **Daily** (every morning, 8 AM PHT) | Breaking news scan across PH political, economic, and social media inputs. Output: a 5-bullet daily digest, plus any reactive triggers. |
| **Weekly** (every Sunday) | Deep research session. Pull 2-3 topic-bank items forward into briefs. Update calendar. Pre-stage skeletons for upcoming calendar events in the next 2 weeks. |
| **Monthly** (1st of the month) | Rankings & data refresh. Check every international index for new releases. Update Key Verified Facts in `brand-context.md` if any numbers have changed. Update Meralco / Entrust quarterly data if disclosed. |

---

## 5. Source rot and freshness

A fact from 2022 isn't necessarily wrong, but it might be stale. Before citing any number:

- **Population figures:** refresh annually
- **Trust dividend amounts:** refresh per dividend cycle
- **Ownership percentages (Meralco, MPIC, JG Summit, Vector):** refresh quarterly via PSE / NZX filings
- **Cost-of-living, exchange rates, fuel prices:** refresh per post (these move fast)
- **Constitutional text:** never changes. Cite verbatim every time.

If a fact in `brand-context.md` is older than 12 months and the brief uses it, flag it in the "Open questions / risks" section of the brief.

---

## 6. Anti-pattern checklist (what to NOT do during research)

- Don't pull a "fact" from a Facebook post or meme without finding the primary source.
- Don't paraphrase a constitutional clause - quote it verbatim.
- Don't round numbers in a way that loses meaning (PHP 13,000 dividend rounding to "around 10,000" weakens the post).
- Don't research only one side. Find the opposing argument too. Posts that withstand the obvious counter-argument outperform.
- Don't research and write in the same hour. Briefs cool overnight. Writers see things research missed.
- Don't trust a single screenshot. Screenshots are evidence in addition to a source, never instead of one.

---

## 7. Output location convention

- Research briefs: `/raw/briefs/{YYYY-MM-DD}-{topic-slug}.md`
- Source files (PDFs, screenshots, audit reports): `/raw/briefs/_sources/{topic-slug}/`
- Analytics exports (for performance review, not for content research): `/raw/analytics/`

The writer pulls from `/raw/briefs/` and outputs to `/output/posts/`. Research and writing are deliberately separated so the same brief can power multiple platform-specific posts.
