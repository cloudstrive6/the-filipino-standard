# Hand-off Note - Threads Mid-Morning - 2026-05-29

## Topic chosen
**Pillar 5 (Filipino Empowerment)** + secondary **Pillar 4 (Economic & Utility Reform)**
Filipina caregivers exported to aging Asia-Pacific economies (Singapore, Japan, Australia) while the Philippines itself enters a domestic care crisis (PIDS study coverage May 26-29, 2026).

A different angle and different pillar from the morning Threads slot (which ran Pillar 2: plunder / impeachment court at 7:00 AM PHT), so no overlap.

## Files written
- Post: `Z:\Business Empire\The Filipino Standard\output\posts\2026-05-29-filipina-caregivers-aging-asia-threads-mid-morning.md`
- Brief: `Z:\Business Empire\The Filipino Standard\output\briefs\2026-05-29-filipina-caregivers-aging-asia-threads-mid-morning.md`
- Spec (validator-ready): `Z:\Business Empire\The Filipino Standard\output\posts\2026-05-29-filipina-caregivers-aging-asia-threads-mid-morning.spec.json`
- ClickUp payload (rule-compliant, ready for downstream): `Z:\Business Empire\The Filipino Standard\output\posts\2026-05-29-filipina-caregivers-aging-asia-threads-mid-morning.clickup-payload.json`

## ClickUp task status
**NOT yet created in ClickUp.** Reason: this Cowork session does not have the `clickup_create_task` MCP connector available, and the sanctioned `scripts/create_task.py` path fails from the sandbox with a proxy 403 talking to api.clickup.com (the documented sandbox network limitation, per memory `tfs-clickup-publishing-gotchas`).

What was done instead:
1. Built a structured spec at `output/posts/2026-05-29-filipina-caregivers-aging-asia-threads-mid-morning.spec.json`.
2. Ran `python3 scripts/create_task.py --spec ... --dry-run` and got `status: validated`. The payload passes the 5 ABSOLUTE RULES locally.
3. Ran the real create; ClickUp REST returned 403 via the sandbox proxy as expected.
4. Wrote a rule-compliant ClickUp payload JSON to `output/posts/...clickup-payload.json`, matching the existing pattern used by today's morning FB post (`2026-05-29-ombudsman-plunder-flood-control-fb.clickup-payload.json`).

**Next step required (outside sandbox):** push the payload to ClickUp list `901614911598` via the `clickup_create_task` MCP connector in a session that has it available, OR run `python3 scripts/create_task.py --spec ...spec.json` from an environment with direct internet egress.

## Scheduled Publish
**2026-05-29 10:00 PHT (Asia/Manila)** = ISO `2026-05-29T10:00:00+08:00` = epoch_ms `1780020000000`.

Per memory, pass as **numeric epoch_ms** on the CREATE path to avoid NZ-vs-PHT timezone reinterpretation. Both formats are included in the payload's `custom_fields`.

## Task setup at creation time
- name: `Threads Post: Filipina caregivers and Asia's aging crisis`
- status: `APPROVED` (fully automated; no manual review required)
- priority: `normal`
- tags: `["threads", "mid-morning", "pillar-5"]`
- list: `901614911598`

## Publisher reminder
The Publisher script at `Z:\Business Empire\The Filipino Standard\scripts\publisher.py` handles the actual publish to Threads at the scheduled instant, provided the task lands in ClickUp with status `APPROVED` and a future Scheduled Publish value. Changing the task status to `NEEDS-REVISION` before publish halts auto-publish for that task.

## Quality gates (verified via script)
- Caption length: 340 chars (Threads ideal: under 500). PASS.
- Em dashes in caption: 0. PASS.
- First-person pronouns: 0. PASS.
- Tagalog closer ("Ganun kalala."): on pre-cleared safe list per brand-context.md Section 4. PASS.
- Individual politicians named: none. PASS.
- Pillar diversity vs today's earlier posts: Pillar 5/4, no overlap with already-published Pillar 1, 2, 3 posts today. PASS.

## Time check
At hand-off time: 2026-05-29 05:32 PHT. Scheduled Publish at 10:00 PHT = ~4.5 hours of runway. Well clear of the 6-hour `STALE_PUBLISH_HOURS` quarantine window in the publisher.
