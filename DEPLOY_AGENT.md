# Deploying the autonomous agent (ClickUp-free direct publishing)

This runs the **research → write → image → publish** agents on GitHub Actions cron,
posting straight to Facebook / Instagram / Threads via Post for Me. **No ClickUp.**

> **Use a PRIVATE repo** — the agent reads your strategy (`skills/`, `context/`) at
> runtime. Private Actions minutes are free under 2,000/month; ~21 lean runs/day
> should fit inside that.

## Schedule
| Workflow | Platforms | When (PHT) | Cron (UTC) |
|---|---|---|---|
| `agent-threads.yml` | Threads | hourly, 6 AM–11 PM (~18/day) | `0 22,23,0-15 * * *` |
| `agent-fb-ig.yml` | Facebook + Instagram | 8 AM / 12 PM / 7 PM | `0 0,4,11 * * *` |

Each run executes `agent/post_agent.md` for exactly one post.

## Setup

### 1. Auth on your Max plan (no API spend)
```powershell
claude setup-token
```
Add the result as the secret **`CLAUDE_CODE_OAUTH_TOKEN`**.

### 2. Secrets (repo → Settings → Secrets and variables → Actions → Secrets)
- `CLAUDE_CODE_OAUTH_TOKEN`
- `GEMINI_API_KEY`
- `POSTFORME_API_KEY`, `POSTFORME_PROJECT_ID`, `POSTFORME_FB_PAGE_ID`,
  `POSTFORME_IG_ACCOUNT_ID`, `POSTFORME_THREADS_ACCOUNT_ID`

### 3. The live switch (repo → Settings → Secrets and variables → Actions → **Variables**)
- Create variable **`TFS_LIVE`**. Leave it **unset / `false`** for the dry-run rollout.
  Set it to **`true`** only when you're ready for real posts. There's a double gate:
  `publish_direct.py` refuses to post unless `--live` is passed **and** `TFS_LIVE=true`.

### 4. Push the private repo
```powershell
gh repo create the-filipino-standard --private --source . --remote origin --push
```

## Dry-run rollout (do this first)
1. With `TFS_LIVE` unset, trigger each workflow from the **Actions** tab (Run workflow).
2. The agent researches, writes, makes the image, and **logs the intended post** to
   `logs/drafts.md` (also uploaded as a run artifact, and committed back to the repo).
   Nothing is published.
3. Read `logs/drafts.md` for a day or two. Check: factual accuracy, brand voice, varied
   Tagalog beats, the <140-char FB hooks, no repeats.
4. **First run is also where you tune the `claude` invocation** in the workflows
   (the headless `--allowedTools` / `--dangerously-skip-permissions` / `--max-turns`
   flags and the OAuth env var can shift between Claude Code CLI versions).

## Go live
When the drafts are consistently good, set the **`TFS_LIVE`** variable to `true`.
To pause at any time, set it back to `false` (or disable the workflows).

## Note on the old ClickUp workflows
`tfs-publisher.yml` / `tfs-image-generator.yml` / `tfs-status-poller.yml` belong to the
older ClickUp model. They can publish the existing ClickUp-scheduled backlog during the
transition, but once you're fully on the direct agent you can delete them.
