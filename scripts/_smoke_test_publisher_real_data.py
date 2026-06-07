"""Publisher safety-guard smoke test against ONE REAL throwaway task.

Purpose: verify that the publisher's parsing of real ClickUp shapes — the
epoch-millis Scheduled Publish value, the real Platform-labels array — is
correct, and that is_cleared_to_publish + check_publish_guards correctly
hold the task when its schedule is a far-future placeholder.

Scope: list 901614911598 ONLY. PUBLISHER_LIVE_MODE must be false. Never
touches HelloNorg, 86d30n0ne, or config/locked_tasks.json (read-only).

Flow:
  1. Create one throwaway DRAFT task on the list via scripts/create_task.py,
     with a clean caption and Scheduled Publish in 2027 (the placeholder year).
  2. Fetch it back via the ClickUp REST API.
  3. Print the raw + parsed values for Scheduled Publish and Platform — this
     is the actual verification of real-shape parsing.
  4. Run is_cleared_to_publish on the as-fetched task (status=draft).
  5. Patch an IN-MEMORY copy to status=scheduled and re-run is_cleared_to_publish
     to demonstrate the placeholder-year HOLD reason fires with the real
     parsed sched value.
  6. Run process_publishable_task(real task) in dry mode through publisher.py.
     Expect: no ClickUp writes, no Post for Me calls, return False.
  7. DELETE the throwaway task via ClickUp REST. Verify it's gone.

Zero real publishes. Zero status flips on the live brand task list. The
only mutations are create + delete of one task we own end-to-end.
"""
import truststore; truststore.inject_into_ssl()

import datetime as dt
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import publisher  # noqa: E402
from publisher import (  # noqa: E402
    CLICKUP_LIST_ID,
    FIELD_PLATFORM,
    FIELD_SCHEDULED_PUBLISH,
    STATUS_SCHEDULED,
    _enforce_publish_guards,
    _load_locked_task_ids,
    _validate_caption,
    check_publish_guards,
    is_cleared_to_publish,
    process_publishable_task,
    scheduled_publish_pht,
    task_platforms,
)

# Hard-force dry mode for this test process. We never want this smoke test to
# trip into live publishing regardless of what .env says. We set the env var
# BEFORE load_dotenv so dotenv's default override=False leaves it as "false".
# Note: this script never invokes publisher.py's main() — it calls
# process_publishable_task(..., dry_run=True) directly, so live_mode is
# additionally a no-op for the publish path. This is belt + suspenders.
os.environ["PUBLISHER_LIVE_MODE"] = "false"

load_dotenv(ROOT / ".env")
TOKEN = os.environ["CLICKUP_API_TOKEN"]
CREATE_TASK = str(ROOT / "scripts" / "create_task.py")
PY = sys.executable
LOCKED = _load_locked_task_ids()

# Re-assert after dotenv (paranoia floor): the in-process env var must still
# read "false". If somehow it's true we abort before touching the API.
if os.environ.get("PUBLISHER_LIVE_MODE", "").lower() == "true":
    print("ABORT: PUBLISHER_LIVE_MODE=true after force-set. Refusing to run.")
    sys.exit(2)
print(f"PUBLISHER_LIVE_MODE (in-process, forced): "
      f"{os.environ.get('PUBLISHER_LIVE_MODE')!r}")


def get_task(task_id: str) -> dict:
    r = requests.get(
        f"https://api.clickup.com/api/v2/task/{task_id}",
        headers={"Authorization": TOKEN},
        params={"custom_fields": "true", "include_subtasks": "false"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def list_open_tasks_summary() -> list[tuple[str, str, str]]:
    """Return [(id, name, status)] of all tasks on the list. For final reporting."""
    out: list[tuple[str, str, str]] = []
    page = 0
    while True:
        r = requests.get(
            f"https://api.clickup.com/api/v2/list/{CLICKUP_LIST_ID}/task",
            headers={"Authorization": TOKEN},
            params={"archived": "false", "subtasks": "false",
                    "include_closed": "true", "page": page},
            timeout=30,
        )
        r.raise_for_status()
        tasks = r.json().get("tasks", [])
        if not tasks:
            break
        for t in tasks:
            out.append((
                t["id"],
                t.get("name", ""),
                (t.get("status") or {}).get("status", "").lower(),
            ))
        page += 1
        if len(tasks) < 100:
            break
    return out


def delete_task(task_id: str) -> int:
    # Defense in depth — never delete a locked task
    if task_id in LOCKED:
        print(f"  REFUSING to delete locked task {task_id}")
        return -1
    r = requests.delete(
        f"https://api.clickup.com/api/v2/task/{task_id}",
        headers={"Authorization": TOKEN},
        timeout=30,
    )
    return r.status_code


# --- Build the spec ---
SMOKE_CAPTION = (
    "The 1987 Constitution is the binding document that organises the "
    "Philippine state. Sovereignty resides in the people, and all "
    "government authority emanates from them.\n\nThis is the source of "
    "every legitimate office and the limit on every dynasty. Tama na ang "
    "theatrics. The Constitution is still in force."
)

# Name chosen specifically to NOT trigger the TEST guard:
#   - no standalone TEST token
#   - no "YYYY-MM-DD TEST" prefix
#   - no 'test' tag will be set on the task
# The publisher's TEST guard must NOT fire here. The intent is to isolate
# the FAR-FUTURE placeholder reason for the publish hold.
SMOKE_TASK_NAME = "2027-01-15 Constitutional Awareness Publisher Guard Smoke FB"

SPEC = {
    "task_name": SMOKE_TASK_NAME,
    "caption": SMOKE_CAPTION,
    "content_pillar": "Constitutional Awareness",
    "post_type": "Static",
    "platform": ["Facebook"],
    "scheduled_publish_pht": "2027-01-15T08:00:00+08:00",
    "status": "draft",
}


def main() -> int:
    print("=" * 72)
    print("PUBLISHER SAFETY-GUARD SMOKE TEST AGAINST REAL CLICKUP DATA")
    print("=" * 72)
    print(f"List           : {CLICKUP_LIST_ID}")
    print(f"PUBLISHER_LIVE_MODE: false (dry, asserted)")
    print(f"Locked tasks   : {sorted(LOCKED)}")
    print()

    # ---- Step 1: create the throwaway task ----
    print("STEP 1 — Create throwaway draft task via scripts/create_task.py")
    print("-" * 72)
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                      encoding="utf-8") as f:
        json.dump(SPEC, f, ensure_ascii=False)
        spec_path = f.name
    try:
        proc = subprocess.run(
            [PY, CREATE_TASK, "--spec", spec_path],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
    finally:
        try:
            os.unlink(spec_path)
        except OSError:
            pass
    print(f"  exit code: {proc.returncode}")
    if proc.returncode != 0:
        print(f"  stdout: {proc.stdout}")
        print(f"  stderr: {proc.stderr}")
        return 1
    create_result = json.loads(proc.stdout)
    print(f"  create_task result: status={create_result.get('status')}")
    task_id = create_result.get("task_id")
    task_url = create_result.get("task_url")
    print(f"  task_id : {task_id}")
    print(f"  task_url: {task_url}")
    if not task_id:
        print("  FAIL — no task_id returned")
        return 1
    if task_id in LOCKED:
        print(f"  FAIL — refusing to proceed; task_id {task_id} is in lock list")
        return 1
    print()

    try:
        # ---- Step 2: fetch back via ClickUp ----
        print("STEP 2 — Fetch the task back via ClickUp REST")
        print("-" * 72)
        task = get_task(task_id)
        print(f"  GET /task/{task_id} status: 200")
        print(f"  list.id            : {(task.get('list') or {}).get('id')!r} "
              f"(must be {CLICKUP_LIST_ID!r})")
        assert (task.get("list") or {}).get("id") == CLICKUP_LIST_ID, \
            "task is not on the locked list — refusing to proceed"
        print()

        # ---- Step 3: print raw + parsed values ----
        print("STEP 3 — Verify real-shape parsing")
        print("-" * 72)
        # Find the raw FIELD_SCHEDULED_PUBLISH value as stored by ClickUp
        raw_sched_value = None
        raw_platform_value = None
        for cf in task.get("custom_fields", []):
            if cf.get("id") == FIELD_SCHEDULED_PUBLISH:
                raw_sched_value = cf.get("value")
            elif cf.get("id") == FIELD_PLATFORM:
                raw_platform_value = cf.get("value")
        print(f"  raw FIELD_SCHEDULED_PUBLISH value (epoch-ms): {raw_sched_value!r}")
        parsed_sched = scheduled_publish_pht(task)
        print(f"  publisher.scheduled_publish_pht() parsed   : "
              f"{parsed_sched.isoformat() if parsed_sched else None}")
        print(f"  raw FIELD_PLATFORM value: {raw_platform_value!r}")
        parsed_platforms = task_platforms(task)
        print(f"  publisher.task_platforms() parsed: {parsed_platforms}")
        parsing_ok = (
            isinstance(raw_sched_value, (str, int))
            and parsed_sched is not None
            and parsed_sched.year == 2027
            and parsed_platforms == ["facebook"]
        )
        print(f"  Real-shape parsing verified: {parsing_ok}")
        print(f"    expected parsed year     : 2027  (got: "
              f"{parsed_sched.year if parsed_sched else None})")
        print(f"    expected parsed platforms: ['facebook']  (got: "
              f"{parsed_platforms})")
        print()
        if not parsing_ok:
            print("  FAIL — real-shape parsing did not return expected values")
            return 1

        # ---- Step 4: is_cleared_to_publish on as-fetched task ----
        print("STEP 4 — is_cleared_to_publish on the as-fetched task "
              "(status=draft)")
        print("-" * 72)
        cleared, why, kind = is_cleared_to_publish(task)
        print(f"  cleared : {cleared}  kind={kind!r}")
        print(f"  reason  : {why}")
        step4_ok = (not cleared) and kind == "hold"
        print(f"  Result  : {'PASS' if step4_ok else 'FAIL'} "
              f"(draft task is correctly held by the cleared-to-publish gate)")
        print()

        # ---- Step 5: in-memory copy with status patched to "scheduled" ----
        print("STEP 5 — In-memory copy patched to status=scheduled "
              "(NO real mutation), to isolate the placeholder-year reason")
        print("-" * 72)
        patched = dict(task)
        patched["status"] = {"status": STATUS_SCHEDULED}
        cleared2, why2, kind2 = is_cleared_to_publish(patched)
        print(f"  cleared : {cleared2}  kind={kind2!r}")
        print(f"  reason  : {why2}")
        step5_ok = (
            not cleared2
            and kind2 == "hold"
            and "far-future placeholder" in why2.lower()
            and str(publisher.FAR_FUTURE_YEAR_THRESHOLD) in why2
        )
        print(f"  Result  : {'PASS' if step5_ok else 'FAIL'} "
              f"(placeholder year reason fires with real parsed sched)")
        print()

        # ---- Step 6: process_publishable_task in dry mode ----
        print("STEP 6 — process_publishable_task in DRY mode against real task")
        print("-" * 72)
        # Use a FakeClickUp that REFUSES writes. Dry mode shouldn't write
        # anyway, but this is the paranoia floor.
        class FakeClickUp:
            def __init__(self) -> None:
                self.writes = []
            def set_status(self, tid, status):
                self.writes.append(("set_status", tid, status))
                raise AssertionError(f"set_status called in DRY: {tid} -> {status}")
            def set_custom_field(self, tid, fid, value):
                self.writes.append(("set_custom_field", tid, fid, value))
                raise AssertionError(f"set_custom_field in DRY: {tid}.{fid}")
            def add_comment(self, tid, text):
                self.writes.append(("add_comment", tid, text))
                raise AssertionError(f"add_comment in DRY: {tid}: {text[:80]}")

        captured: list[str] = []
        class Spy(logging.Handler):
            def emit(self, record):
                captured.append(self.format(record))
        spy = Spy()
        spy.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger = logging.getLogger("publisher-smoke-real")
        logger.handlers.clear()
        logger.setLevel(logging.INFO)
        logger.addHandler(spy)
        logger.propagate = False

        fake_cu = FakeClickUp()
        ok = process_publishable_task(
            task, fake_cu, None, dict(os.environ), logger, dry_run=True,
        )
        print(f"  process_publishable_task returned: {ok}")
        print(f"  FakeClickUp writes attempted: {len(fake_cu.writes)} (must be 0)")
        print(f"  Logged messages ({len(captured)}):")
        # Show only the most relevant lines
        keepers = [
            m for m in captured
            if any(s in m for s in ("BLOCKED", "guard", "rule=", "DRY RUN",
                                     "Processing task", "Platform broadcast"))
        ]
        for line in keepers[:20]:
            print(f"    {line}")
        step6_ok = (not ok) and len(fake_cu.writes) == 0 and any(
            "NOT_CLEARED_TO_PUBLISH" in m for m in captured
        )
        print(f"  Result: {'PASS' if step6_ok else 'FAIL'}")
        print()

    finally:
        # ---- Step 7: cleanup ----
        print("STEP 7 — Delete the throwaway task")
        print("-" * 72)
        try:
            code = delete_task(task_id)
            print(f"  DELETE /task/{task_id} status: {code}")
        except requests.RequestException as e:
            print(f"  WARNING: deletion request failed: {e}")
            code = -2
        # Verify deletion
        try:
            after = get_task(task_id)
            archived = (after.get("archived"))
            print(f"  Post-delete fetch returned 200; archived={archived}")
            cleanup_ok = bool(archived)
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (404, 401):
                print(f"  Post-delete fetch -> HTTP {e.response.status_code} "
                      f"(task no longer accessible — OK)")
                cleanup_ok = True
            else:
                print(f"  Post-delete fetch -> unexpected error: {e}")
                cleanup_ok = False
        print(f"  Cleanup verified: {cleanup_ok}")
        print()

    # ---- Final list state ----
    print("FINAL — List state after smoke test")
    print("-" * 72)
    summary = list_open_tasks_summary()
    draft_nr = [(tid, name, st) for tid, name, st in summary
                if st in ("draft", "needs-revision", "needs revision")]
    print(f"  Total tasks on list      : {len(summary)}")
    print(f"  draft / needs-revision   : {len(draft_nr)}")
    for tid, name, st in draft_nr:
        marker = " (LOCKED)" if tid in LOCKED else ""
        print(f"    - [{st}] {tid}{marker}: {name}")
    print()

    overall_pass = step4_ok and step5_ok and step6_ok and cleanup_ok and parsing_ok
    print("=" * 72)
    print(f"OVERALL RESULT: {'PASS' if overall_pass else 'FAIL'}")
    print(f"  Real-shape parsing verified           : {parsing_ok}")
    print(f"  is_cleared_to_publish holds draft     : {step4_ok}")
    print(f"  Placeholder-year reason fires (patched): {step5_ok}")
    print(f"  process_publishable_task dry-runs OK  : {step6_ok}")
    print(f"  Throwaway task deleted                : {cleanup_ok}")
    print(f"  Real ClickUp writes (publisher path)  : 0")
    print(f"  Real Post for Me publishes            : 0")
    print("=" * 72)
    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
