# Deploying TFS automation to GitHub Actions (PC-free publishing)

This moves the **publishing automation** (publisher, Gemini image generator,
status poller) off your PC and onto GitHub Actions cron. Content is still
written the way you do it today; only the posting/imaging/polling runs in the
cloud.

> **Scope:** Layer A only (the deterministic Python automation). The content
> *writing* agents are not part of this.

---

## What runs in the cloud

| Workflow | Script | Cron (UTC) | Replaces Windows task |
|---|---|---|---|
| `TFS Publisher` | `publisher.py --monitor` | every 15 min | TFS Publisher Monitor |
| `TFS Image Generator` | `generate_image_gemini.py --pending` | every 15 min (offset 5) | TFS Image Generator |
| `TFS Status Poller` | `status_poller.py` | every 10 min | TFS Status Poller |

The scripts compute Manila time internally, so UTC runners are fine.

---

## One-time setup

### 1. Create the repo and push (public repo = free unlimited Actions minutes)

```powershell
cd "Z:\Business Empire\The Filipino Standard"
git init
git add .
git commit -m "TFS pipeline + GitHub Actions automation"
```

Confirm `.env` is NOT staged (it must stay secret):

```powershell
git status --short | Select-String ".env"   # should print nothing
```

Then create a **public** repo and push (using the GitHub CLI):

```powershell
gh repo create the-filipino-standard --public --source . --remote origin --push
```

(Or create the repo on github.com and `git remote add origin <url>; git push -u origin main`.)

### 2. Add the secrets

In the repo: **Settings → Secrets and variables → Actions → New repository secret**.
Copy each value from your local `.env`:

- `CLICKUP_API_TOKEN`
- `POSTFORME_API_KEY`
- `POSTFORME_PROJECT_ID`
- `POSTFORME_FB_PAGE_ID`
- `POSTFORME_IG_ACCOUNT_ID`
- `POSTFORME_THREADS_ACCOUNT_ID`
- `PUBLISHER_LIVE_MODE`  → set to `true` to actually post (anything else = dry run)
- `GEMINI_API_KEY`

CLI alternative (run from the repo folder):

```powershell
gh secret set CLICKUP_API_TOKEN
gh secret set POSTFORME_API_KEY
# ...repeat for each; it prompts for the value (never echoed)
```

### 3. Test before trusting it

1. **Actions** tab → each workflow → **Run workflow** (manual trigger).
2. Start with `PUBLISHER_LIVE_MODE` unset/`false` (dry run) and read the logs.
3. When the publisher dry-run looks right, set `PUBLISHER_LIVE_MODE=true`.

### 4. ⚠️ Turn OFF the Windows scheduled tasks

Once GitHub Actions is verified, **disable the three Windows tasks** so they
don't run alongside the cloud ones:

```powershell
Disable-ScheduledTask -TaskName "TFS Publisher Monitor"
Disable-ScheduledTask -TaskName "TFS Image Generator"
Disable-ScheduledTask -TaskName "TFS Status Poller"
Disable-ScheduledTask -TaskName "TFS Daily Cleanup"
```

Running both at once risks a rare double-publish (the per-platform idempotency
check usually prevents it, but two simultaneous runs can slip through).

---

## Things to know

- **Cron is best-effort.** GitHub may delay or occasionally skip scheduled runs
  under load. The publisher's 6-hour staleness window absorbs normal delays.
- **GitHub disables schedules after 60 days of repo inactivity.** A commit (or
  the manual button) re-arms them. Pushing content files periodically keeps it alive.
- **Secrets never leave GitHub.** `.env` is git-ignored; the workflows inject
  secrets only as masked environment variables.
- **The self-heal needs `output/posts/*.md`.** Those files are kept tracked (not
  ignored). If you write content on your PC, commit the new post files so the
  cloud status-poller can repair a brief-leak; otherwise it falls back to
  quarantining (the safety net still holds).
- **Public repo caveat.** Your scripts, skills, and context become publicly
  visible (secrets do not). If that's not acceptable, switch to a private repo
  (you'll pay for Actions minutes over the 2,000/month free tier) or a small
  always-on host.
