"""Five adversarial tests for the publisher safety guards.

For each blocked test: confirm the right guard fires AND no real ClickUp
writes / Post for Me publishes happen. For the clean valid test: confirm
all guards pass AND the publish path is reached (no-op in dry mode).

This script:
  * Runs entirely with PUBLISHER_LIVE_MODE=false (asserted at startup).
  * Uses synthetic in-memory ClickUp task fixtures — it NEVER reads or writes
    list 901614911598, never touches HelloNorg, never touches the lock file.
  * Uses a FakeClickUp that REFUSES every write — any write attempt is a
    test failure.
  * Stubs out PostForMeClient — never instantiated in dry mode, so zero
    real publishes occur.
"""
import truststore; truststore.inject_into_ssl()

import datetime as dt
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import publisher  # noqa: E402
from publisher import (  # noqa: E402
    APPROVED_NEAR_BUFFER_MINUTES,
    CLICKUP_LIST_ID,
    FIELD_PLATFORM,
    FIELD_SCHEDULED_PUBLISH,
    PHT,
    PLATFORM_OPTION_FACEBOOK,
    PLATFORM_OPTION_INSTAGRAM,
    PLATFORM_OPTION_THREADS,
    STATUS_APPROVED,
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PLATFORM_NAME_TO_OPTION = {
    "facebook": PLATFORM_OPTION_FACEBOOK,
    "instagram": PLATFORM_OPTION_INSTAGRAM,
    "threads": PLATFORM_OPTION_THREADS,
}


def _ms(d: dt.datetime) -> str:
    """ClickUp serializes timestamps as unix-ms strings."""
    return str(int(d.timestamp() * 1000))


def make_task(
    *,
    task_id: str,
    name: str,
    description: str,
    status: str,
    sched_pht: dt.datetime,
    platforms: list[str] | None = None,
) -> dict:
    """Build a synthetic ClickUp task dict matching the fields publisher reads."""
    plat = platforms if platforms is not None else []
    return {
        "id": task_id,
        "name": name,
        "description": description,
        "text_content": description,
        "status": {"status": status},
        "list": {"id": CLICKUP_LIST_ID},
        "custom_fields": [
            {"id": FIELD_SCHEDULED_PUBLISH, "value": _ms(sched_pht)},
            {
                "id": FIELD_PLATFORM,
                "value": [
                    {"id": PLATFORM_NAME_TO_OPTION[p]} for p in plat
                ],
            },
        ],
    }


# ---------------------------------------------------------------------------
# Test captions
# ---------------------------------------------------------------------------

BRIEF_BLOB_DESC = """\
Hook: A war broke out thousands of kilometres away.

Core Argument
The Philippines runs a borrowed buffer model.

Sources
- ADB press release May 15, 2026
- BSP daily peso close

Sanity Checklist
- [x] Constitutional Citation present
- [x] No em-dashes
"""

CLEAN_VALID_DESC = (
    "The 1987 Constitution opens with one sentence the rest of the document "
    "is built on.\n\nSovereignty resides in the people, and all government "
    "authority emanates from them.\n\nNot from the office. Not from the "
    "party. Not from the dynasty. From the people. The mandate is "
    "unambiguous and it has been in force for 38 years.\n\nTama na ang "
    "theatrics. The Constitution is still in force."
)


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------


class FakeClickUp:
    """Read-allowed, write-refusing ClickUp client. Any write is a hard fail."""

    def __init__(self) -> None:
        self.writes: list[tuple] = []

    # ---- writes — every call records itself AND raises ----
    def set_status(self, task_id, status):
        self.writes.append(("set_status", task_id, status))
        raise AssertionError(
            f"FakeClickUp.set_status called in DRY mode: {task_id} -> {status}"
        )

    def set_custom_field(self, task_id, field_id, value):
        self.writes.append(("set_custom_field", task_id, field_id, value))
        raise AssertionError(
            f"FakeClickUp.set_custom_field called in DRY mode: "
            f"{task_id}.{field_id}={value!r}"
        )

    def add_comment(self, task_id, text):
        self.writes.append(("add_comment", task_id, text))
        raise AssertionError(
            f"FakeClickUp.add_comment called in DRY mode: {task_id}: {text[:80]}"
        )


class LogCapture(logging.Handler):
    """Captures log records for later assertion."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def messages(self) -> list[str]:
        return [r.getMessage() for r in self.records]


# ---------------------------------------------------------------------------
# Test driver
# ---------------------------------------------------------------------------


def main() -> int:
    # Hard safety floor: refuse to run in live mode.
    live = os.environ.get("PUBLISHER_LIVE_MODE", "").lower() == "true"
    if live:
        print("ABORT: PUBLISHER_LIVE_MODE=true detected. This test MUST run "
              "with PUBLISHER_LIVE_MODE=false. Refusing.")
        return 2
    print("PUBLISHER_LIVE_MODE = false (dry mode, asserted)")
    print()

    # Show the locked-task list the guards will use
    locked_ids = _load_locked_task_ids()
    print(f"Locked task IDs (defense in depth): {sorted(locked_ids)}")
    print()

    now = dt.datetime.now(PHT)
    near_future = now + dt.timedelta(minutes=15)         # within APPROVED 30-min window
    far_future = dt.datetime(2027, 1, 15, 8, 0, 0, tzinfo=PHT)  # placeholder year
    print(f"now (PHT)           : {now.isoformat()}")
    print(f"near-term (15min)   : {near_future.isoformat()}")
    print(f"far-future (placeholder year 2027): {far_future.isoformat()}")
    print(f"FAR_FUTURE_YEAR_THRESHOLD = {publisher.FAR_FUTURE_YEAR_THRESHOLD}")
    print(f"APPROVED_NEAR_BUFFER_MINUTES = {APPROVED_NEAR_BUFFER_MINUTES}")
    print()

    results: list[tuple[str, str, str]] = []  # (test_num, status, note)

    def report_guards(task: dict) -> tuple[list, bool, str]:
        """Print and return (blocks, cleared, cleared_reason) for a task."""
        sp = scheduled_publish_pht(task)
        print(f"  task_id   : {task['id']}")
        print(f"  task_name : {task['name']!r}")
        print(f"  status    : {task['status']['status']}")
        print(f"  sched(PHT): {sp.isoformat() if sp else '(none)'}")
        print(f"  Platform field: {task_platforms(task)}")
        cleared, why, kind = is_cleared_to_publish(task)
        print(f"  cleared?  : {cleared} (kind={kind}) -- {why}")
        blocks = check_publish_guards(
            task,
            validate_caption_fn=_validate_caption,
            locked_ids=locked_ids,
        )
        if blocks:
            print(f"  blocks ({len(blocks)}):")
            for b in blocks:
                print(f"    - rule={b.rule!r} quarantine={b.quarantine}: {b.detail}")
        else:
            print("  blocks: NONE -- guards permit publish")
        return blocks, cleared, why

    def run_enforce(task: dict, label: str) -> tuple[bool, FakeClickUp, list[str]]:
        """Drive the actual _enforce_publish_guards path with a fake ClickUp
        and a log spy. Returns (allowed, fake_cu, log_messages)."""
        fake = FakeClickUp()
        spy = LogCapture()
        logger = logging.getLogger(f"publisher-test-{label}")
        logger.handlers.clear()
        logger.setLevel(logging.INFO)
        logger.addHandler(spy)
        logger.propagate = False
        allowed = _enforce_publish_guards(
            task, fake, logger, dry_run=True, locked_ids=locked_ids,
        )
        return allowed, fake, spy.messages()

    # ---------------- TEST 1 ----------------
    print("=" * 70)
    print("TEST 1: brief-as-caption description in publishable status")
    print("        (must be BLOCKED + would-be-quarantined; race window closed)")
    print("=" * 70)
    task1 = make_task(
        task_id="86fixt_brief",
        name="2026-05-20 Constitutional Awareness Brief-Cap FB+IG+TH",
        description=BRIEF_BLOB_DESC,
        status=STATUS_SCHEDULED,
        sched_pht=now - dt.timedelta(minutes=5),  # publish time arrived
        platforms=["facebook", "instagram", "threads"],
    )
    blocks1, _, _ = report_guards(task1)
    allowed1, fake1, msgs1 = run_enforce(task1, "1")
    print(f"  _enforce_publish_guards allowed: {allowed1}  (must be False)")
    print(f"  FakeClickUp writes attempted   : {len(fake1.writes)}  (must be 0 in dry mode)")
    has_brief_rule = any(b.rule == "BRIEF_MARKER_PRESENT" for b in blocks1)
    has_quarantine_log = any("[DRY RUN]" in m and "needs-revision" in m.lower() for m in msgs1)
    ok = (
        not allowed1
        and len(fake1.writes) == 0
        and has_brief_rule
        and has_quarantine_log
    )
    print(f"  Result: {'PASS' if ok else 'FAIL'}")
    results.append(("1", "PASS" if ok else "FAIL", "brief blob -> BRIEF_MARKER_PRESENT"))
    print()

    # ---------------- TEST 2 ----------------
    print("=" * 70)
    print("TEST 2: valid caption + far-future placeholder schedule (year 2027)")
    print("        (must be SKIPPED as not cleared; hold-skip, NOT quarantined)")
    print("=" * 70)
    task2 = make_task(
        task_id="86fixt_far",
        name="2027-01-15 Constitutional Awareness Far-Future FB+IG+TH",
        description=CLEAN_VALID_DESC,
        status=STATUS_SCHEDULED,
        sched_pht=far_future,
        platforms=["facebook", "instagram", "threads"],
    )
    blocks2, _, _ = report_guards(task2)
    allowed2, fake2, msgs2 = run_enforce(task2, "2")
    print(f"  _enforce_publish_guards allowed: {allowed2}  (must be False)")
    print(f"  FakeClickUp writes attempted   : {len(fake2.writes)}  (must be 0)")
    has_not_cleared = any(b.rule == "NOT_CLEARED_TO_PUBLISH" for b in blocks2)
    no_quarantine = all(not b.quarantine for b in blocks2)
    ok = (
        not allowed2
        and len(fake2.writes) == 0
        and has_not_cleared
        and no_quarantine  # hold-skip, never quarantine
    )
    print(f"  Result: {'PASS' if ok else 'FAIL'}")
    results.append(("2", "PASS" if ok else "FAIL", "far-future placeholder -> NOT_CLEARED_TO_PUBLISH (hold-skip)"))
    print()

    # ---------------- TEST 3 ----------------
    print("=" * 70)
    print('TEST 3: "TEST" in task name in publishable status')
    print("        (must be BLOCKED + would-be-quarantined; prevents 86d30n0ne incident class)")
    print("=" * 70)
    task3 = make_task(
        task_id="86fixt_testname",
        name="2026-05-20 Constitutional Awareness Validation TEST FB+IG+TH",
        description=CLEAN_VALID_DESC,
        status=STATUS_SCHEDULED,
        sched_pht=now - dt.timedelta(minutes=5),
        platforms=["facebook", "instagram", "threads"],
    )
    blocks3, _, _ = report_guards(task3)
    allowed3, fake3, msgs3 = run_enforce(task3, "3")
    print(f"  _enforce_publish_guards allowed: {allowed3}  (must be False)")
    print(f"  FakeClickUp writes attempted   : {len(fake3.writes)}  (must be 0)")
    has_test_rule = any(b.rule == "TEST_TASK_BROADCAST_BLOCKED" for b in blocks3)
    has_quarantine_log = any("[DRY RUN]" in m and "needs-revision" in m.lower() for m in msgs3)
    ok = (
        not allowed3
        and len(fake3.writes) == 0
        and has_test_rule
        and has_quarantine_log
    )
    print(f"  Result: {'PASS' if ok else 'FAIL'}")
    results.append(("3", "PASS" if ok else "FAIL", "'TEST' in name -> TEST_TASK_BROADCAST_BLOCKED"))
    print()

    # ---------------- TEST 4 ----------------
    print("=" * 70)
    print("TEST 4: empty Platform custom field")
    print("        (must be BLOCKED + would-be-quarantined; no default-to-all fallback)")
    print("=" * 70)
    task4 = make_task(
        task_id="86fixt_noplat",
        name="2026-05-20 Constitutional Awareness Empty Platform",
        description=CLEAN_VALID_DESC,
        status=STATUS_SCHEDULED,
        sched_pht=now - dt.timedelta(minutes=5),
        platforms=[],
    )
    blocks4, _, _ = report_guards(task4)
    allowed4, fake4, msgs4 = run_enforce(task4, "4")
    print(f"  _enforce_publish_guards allowed: {allowed4}  (must be False)")
    print(f"  FakeClickUp writes attempted   : {len(fake4.writes)}  (must be 0)")
    has_empty_plat = any(b.rule == "EMPTY_PLATFORM_FIELD" for b in blocks4)
    has_quarantine_log = any("[DRY RUN]" in m and "needs-revision" in m.lower() for m in msgs4)
    ok = (
        not allowed4
        and len(fake4.writes) == 0
        and has_empty_plat
        and has_quarantine_log
    )
    print(f"  Result: {'PASS' if ok else 'FAIL'}")
    results.append(("4", "PASS" if ok else "FAIL", "empty Platform -> EMPTY_PLATFORM_FIELD"))
    print()

    # ---------------- TEST 5 ----------------
    print("=" * 70)
    print("TEST 5: fully clean, cleared, near-term task")
    print("        (must PASS all guards, reach publish call, no-op in dry mode)")
    print("=" * 70)
    task5 = make_task(
        task_id="86fixt_clean",
        name="2026-05-20 Constitutional Awareness Clean FB+IG+TH",
        description=CLEAN_VALID_DESC,
        status=STATUS_APPROVED,
        sched_pht=near_future,   # within APPROVED 30-min buffer
        platforms=["facebook", "instagram", "threads"],
    )
    blocks5, cleared5, _ = report_guards(task5)
    allowed5, fake5, msgs5 = run_enforce(task5, "5a")
    print(f"  _enforce_publish_guards allowed: {allowed5}  (must be True)")
    print(f"  FakeClickUp writes attempted   : {len(fake5.writes)}  (must be 0)")

    # Now drive the full process_publishable_task in dry mode. Verify that
    # the per-platform "[DRY RUN] Would publish to ..." lines appear for
    # exactly the platforms in the Platform custom field.
    print()
    print("  -- full process_publishable_task() trace in DRY mode --")
    spy5b = LogCapture()
    logger5b = logging.getLogger("publisher-test-5b")
    logger5b.handlers.clear()
    logger5b.setLevel(logging.INFO)
    logger5b.addHandler(spy5b)
    logger5b.propagate = False
    env: dict[str, str] = {
        # Provide stub creds so the dry-run path doesn't complain. These
        # are NEVER used to make real API calls; dry mode short-circuits
        # before any HTTP request.
        "POSTFORME_FB_PAGE_ID": "stub-fb",
        "POSTFORME_IG_ACCOUNT_ID": "stub-ig",
        "POSTFORME_THREADS_ACCOUNT_ID": "stub-threads",
    }
    fake5b = FakeClickUp()
    ok_5b = process_publishable_task(
        task5, fake5b, None, env, logger5b, dry_run=True,
    )
    msgs5b = spy5b.messages()
    print(f"  process_publishable_task returned: {ok_5b}")
    print(f"  FakeClickUp writes attempted     : {len(fake5b.writes)}  (must be 0)")
    dry_lines = [m for m in msgs5b if "[DRY RUN]" in m and "Would publish to" in m]
    print(f"  [DRY RUN] Would publish lines:")
    for ln in dry_lines:
        print(f"    {ln}")
    platforms_reached = set()
    for p in ("facebook", "instagram", "threads"):
        if any(f"Would publish to {p}" in ln for ln in dry_lines):
            platforms_reached.add(p)
    print(f"  Platforms the publish path was reached for: {sorted(platforms_reached)}")
    # Also surface which logged platform broadcast line was reported
    broadcast_lines = [m for m in msgs5b if "Platform broadcast:" in m]
    for ln in broadcast_lines:
        print(f"  {ln}")

    expected_platforms = {"facebook", "instagram", "threads"}
    ok_test5 = (
        allowed5
        and cleared5
        and not blocks5
        and len(fake5.writes) == 0
        and len(fake5b.writes) == 0
        and platforms_reached == expected_platforms
    )
    print(f"  Result: {'PASS' if ok_test5 else 'FAIL'}")
    results.append((
        "5",
        "PASS" if ok_test5 else "FAIL",
        f"clean task -> guards pass; reached publish path for {sorted(platforms_reached)}; "
        f"LIVE_MODE=false -> NO real publish",
    ))
    print()

    # ---------------- TEST 6 ----------------
    # PROTEST must NOT be flagged as a test task. The whole-word \bTEST\b
    # regex must not match inside "PROTEST". As a bonus, also verify the
    # task with name containing "PROTEST" reaches the publish path cleanly.
    print("=" * 70)
    print("TEST 6: 'PROTEST' in name (must be ALLOWED past TEST guard)")
    print('        Whole-word \\bTEST\\b must NOT match inside "PROTEST"')
    print("=" * 70)
    task6 = make_task(
        task_id="86fixt_protest",
        name="2026-05-20 Foreign Policy PROTEST Movement Coverage FB+IG+TH",
        description=CLEAN_VALID_DESC,
        status=STATUS_APPROVED,
        sched_pht=near_future,
        platforms=["facebook", "instagram", "threads"],
    )
    blocks6, cleared6, _ = report_guards(task6)
    allowed6, fake6, msgs6 = run_enforce(task6, "6")
    print(f"  _enforce_publish_guards allowed: {allowed6}  (must be True)")
    print(f"  FakeClickUp writes attempted   : {len(fake6.writes)}  (must be 0)")
    has_test_rule = any(b.rule == "TEST_TASK_BROADCAST_BLOCKED" for b in blocks6)
    # Spot-check siblings too — none of these should trigger the TEST guard either
    import publisher as _pub
    sibling_names = [
        "2026-05-20 Foreign Policy CONTEST Over South China Sea",
        "2026-05-20 Foreign Policy LATEST Border Incident",
        "2026-05-20 Foreign Policy GREATEST Hits Of The Senate",
        "2026-05-20 Foreign Policy Testing The Boundaries",
        "2026-05-20 Foreign Policy Tester Of The Republic",
    ]
    sibling_results = []
    for sname in sibling_names:
        is_test, why = _pub._is_test_task({"name": sname, "tags": []})
        sibling_results.append((sname, is_test, why))
        print(f"  sibling: {sname!r} -> is_test={is_test} ({why!r})")
    siblings_all_clean = all(not flag for _, flag, _ in sibling_results)
    ok_6 = (
        allowed6
        and cleared6
        and not has_test_rule
        and len(fake6.writes) == 0
        and siblings_all_clean
    )
    print(f"  Result: {'PASS' if ok_6 else 'FAIL'}")
    results.append((
        "6",
        "PASS" if ok_6 else "FAIL",
        "PROTEST/CONTEST/LATEST/GREATEST/Testing/Tester -> NOT flagged as test",
    ))
    print()

    # ---------------- TEST 7 ----------------
    # Clean cleared task with sched 8h in the past -> STALE_SCHEDULE quarantine.
    print("=" * 70)
    print("TEST 7: clean cleared task scheduled 8 hours in the past")
    print(f"        (must be BLOCKED by staleness floor; STALE_PUBLISH_HOURS={publisher.STALE_PUBLISH_HOURS})")
    print("=" * 70)
    eight_hours_ago = now - dt.timedelta(hours=8)
    task7 = make_task(
        task_id="86fixt_stale",
        name="2026-05-17 Constitutional Awareness Stale-8h FB+IG+TH",
        description=CLEAN_VALID_DESC,
        status=STATUS_SCHEDULED,
        sched_pht=eight_hours_ago,
        platforms=["facebook", "instagram", "threads"],
    )
    blocks7, cleared7, why7 = report_guards(task7)
    allowed7, fake7, msgs7 = run_enforce(task7, "7")
    print(f"  _enforce_publish_guards allowed: {allowed7}  (must be False)")
    print(f"  FakeClickUp writes attempted   : {len(fake7.writes)}  (must be 0)")
    has_stale_rule = any(b.rule == "STALE_SCHEDULE" for b in blocks7)
    stale_is_quarantine = any(
        b.rule == "STALE_SCHEDULE" and b.quarantine for b in blocks7
    )
    has_quarantine_log = any("[DRY RUN]" in m and "needs-revision" in m.lower() for m in msgs7)
    ok_7 = (
        not allowed7
        and not cleared7
        and has_stale_rule
        and stale_is_quarantine
        and has_quarantine_log
        and len(fake7.writes) == 0
    )
    print(f"  Result: {'PASS' if ok_7 else 'FAIL'}")
    results.append((
        "7",
        "PASS" if ok_7 else "FAIL",
        "sched 8h ago -> STALE_SCHEDULE (quarantine)",
    ))
    print()

    # ---------------- Final report ----------------
    print("=" * 70)
    print("FINAL VERIFICATION")
    print("=" * 70)
    passed = sum(1 for _, s, _ in results if s == "PASS")
    failed = sum(1 for _, s, _ in results if s == "FAIL")
    for num, status, note in results:
        marker = "PASS" if status == "PASS" else "FAIL"
        print(f"  [{marker}] TEST {num}: {note}")
    print()
    all_fakes = [fake1, fake2, fake3, fake4, fake5, fake5b, fake6, fake7]
    print(f"Total: {passed} passed, {failed} failed")
    print(f"Real ClickUp writes attempted (across all tests): "
          f"{sum(len(f.writes) for f in all_fakes)}")
    print(f"Real Post for Me publishes:                       0 "
          f"(PostForMeClient never instantiated; dry mode short-circuits)")
    print(f"PUBLISHER_LIVE_MODE:                              false (dry)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
