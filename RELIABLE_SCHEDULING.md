# Reliable scheduling — external cron → GitHub workflow_dispatch

## Why
GitHub's built-in `schedule:` (cron) event is best-effort. On private/free-tier
repos it delays runs by hours and silently **drops** most of them (we measured
~5–6 of 18 expected Threads runs/day, all off-schedule). There is no catch-up.

The fix: an **external scheduler** calls GitHub's `workflow_dispatch` REST API at
each slot. A dispatch fires **immediately** — none of the cron delay/drop problem.
The GitHub `schedule:` trigger has been disabled in both workflows; dispatch is
now the only trigger.

---

## Step 1 — Create a GitHub fine-grained Personal Access Token (you do this)

1. https://github.com/settings/personal-access-tokens/new
2. **Token name:** `tfs-dispatch`
3. **Resource owner:** `cloudstrive6`
4. **Expiration:** 1 year (or max).
5. **Repository access:** *Only select repositories* → `cloudstrive6/the-filipino-standard`
6. **Permissions → Repository permissions:**
   - **Actions:** **Read and write**   ← the only one that matters
   - (Metadata: Read-only is auto-added)
7. Generate, then **copy the token** (starts `github_pat_…`). You'll paste it into
   cron-job.org in Step 3. Keep it secret — it can only trigger Actions on this one repo.

---

## Step 2 — Create a free cron-job.org account
https://cron-job.org → sign up (free tier allows plenty of jobs).
Set your account timezone to **Asia/Manila** under Settings so the schedules below
read in PHT directly.

---

## Step 3 — Create the cron jobs

You'll create jobs that each send a `POST` to GitHub. Common settings for **every** job:

- **Request method:** `POST`
- **Request headers** (Advanced → Headers):
  - `Accept: application/vnd.github+json`
  - `Authorization: Bearer github_pat_…`   ← your Step-1 token
  - `X-GitHub-Api-Version: 2022-11-28`
- **Request body:** `{"ref":"main"}`
- **Expected response:** HTTP **204** = success (GitHub returns 204 No Content).

### Job A — Threads (hourly, 6am–midnight PHT)
- **URL:**
  `https://api.github.com/repos/cloudstrive6/the-filipino-standard/actions/workflows/agent-threads.yml/dispatches`
- **Schedule (PHT):** every hour at minute 0, hours **6–23** (and 0 if you want midnight).
  - cron-job.org custom: minutes `0`, hours `6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23`, every day.

### Job B — Facebook + Instagram (3× a day PHT)
- **URL:**
  `https://api.github.com/repos/cloudstrive6/the-filipino-standard/actions/workflows/agent-fb-ig.yml/dispatches`
- **Schedule (PHT):** **08:00, 12:00, 19:00** every day.
  - cron-job.org custom: minutes `0`, hours `8,12,19`, every day.

> Tip: stagger Threads off the FB/IG minutes if you like (e.g. Threads at minute 0,
> FB/IG at minute 5) — both workflows share a concurrency group and will serialize,
> so a few minutes apart keeps the log commits clean.

---

## Step 4 — Test
In cron-job.org, open Job A → **"Run now"**. Within a few seconds a new run should
appear under the repo's **Actions** tab (event = `workflow_dispatch`). If you get
**HTTP 404** the token lacks Actions:write or the repo/owner is wrong; **401** = bad
token; **204** = success.

You can also test from a terminal (replace TOKEN):
```
curl -X POST -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer TOKEN" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/cloudstrive6/the-filipino-standard/actions/workflows/agent-threads.yml/dispatches \
  -d '{"ref":"main"}'
```

---

## Notes
- **Live posting** still requires the repo variable `TFS_LIVE=true` AND the agent
  fix (passing `--live`) — both already in place / committed.
- To pause everything: disable the cron-job.org jobs (or set `TFS_LIVE` to anything
  but `true` to drop back to dry-run while still exercising the pipeline).
- Cost: dispatch-triggered runs use the same free Actions minutes as before; the
  external scheduler is free. No GitHub-side change in billing.
