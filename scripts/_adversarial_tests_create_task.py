"""Five adversarial tests for scripts/create_task.py.

For each rejection case: confirm the script exits non-zero AND the
ClickUp task count on list 901614911598 is unchanged.

For the clean valid case: confirm one new task was created with the
expected fields.

This script does NOT write to anything except possibly creating one
draft test task on list 901614911598 in the final test."""
import truststore; truststore.inject_into_ssl()

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
TOKEN = os.environ["CLICKUP_API_TOKEN"]
LIST_ID = "901614911598"
CREATE_TASK = str(ROOT / "scripts" / "create_task.py")
PY = sys.executable


def list_task_ids() -> set[str]:
    """Return the set of all task IDs on the list (snapshot for write-detection)."""
    ids: set[str] = set()
    page = 0
    while True:
        r = requests.get(
            f"https://api.clickup.com/api/v2/list/{LIST_ID}/task",
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
            ids.add(t["id"])
        page += 1
        if len(tasks) < 100:
            break
    return ids


def run_create(spec: dict) -> tuple[int, dict]:
    """Run create_task.py --spec <tmp> and return (exit_code, parsed_json)."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False)
        spec_path = f.name
    try:
        proc = subprocess.run(
            [PY, CREATE_TASK, "--spec", spec_path],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            data = {"_parse_error": proc.stdout, "_stderr": proc.stderr}
        return proc.returncode, data
    finally:
        os.unlink(spec_path)


# ---------- Tests ----------

VALID_BASE_SPEC = {
    "task_name":             "2027-01-15 Constitutional Awareness Validation Gate Test FB+IG+TH",
    "caption":                None,  # filled per test
    "content_pillar":        "Constitutional Awareness",
    "post_type":             "Static",
    "platform":              ["Facebook", "Instagram", "Threads"],
    "scheduled_publish_pht": "2027-01-15T08:00:00+08:00",
    "status":                "draft",
}

BRIEF_BLOB_CAPTION = """\
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

BLOCKQUOTE_CAPTION = """\
The Senate is where Philippine laws are written.

This week, the Senate was where Philippine laws were broken so that a senator could quietly walk out the door.

> Public office is a public trust.

The chamber holds power on behalf of someone else.
"""

# Em-dash test: insert a real U+2014 character
EM_DASH_CAPTION = (
    "The Philippines has watched the same chokepoint cause the same pain "
    "in the 1970s, in 1990, in 2008, in 2022 — four decades of the same "
    "lesson, four decades of choosing not to build the buffer.\n\n"
    "Tama na."
)

CLEAN_VALID_CAPTION = (
    "The 1987 Constitution opens with one sentence the rest of the document "
    "is built on.\n\nSovereignty resides in the people, and all government "
    "authority emanates from them.\n\nNot from the office. Not from the "
    "party. Not from the dynasty. From the people. The mandate is "
    "unambiguous and it has been in force for 38 years.\n\nTama na ang "
    "theatrics. The Constitution is still in force."
)


def print_violations(label: str, data: dict) -> None:
    print(f"  status = {data.get('status')}")
    if data.get("status") == "rejected":
        for v in data.get("violations", []):
            print(f"    - rule={v.get('rule')!r}: {v.get('detail')}")


def main() -> int:
    print("Snapshot of ClickUp task IDs BEFORE adversarial tests...")
    before_ids = list_task_ids()
    print(f"  count = {len(before_ids)}")
    print()

    new_task_ids: list[str] = []

    # Test 1: brief-dumped-into-caption blob
    print("=" * 70)
    print("TEST 1: brief-dumped-into-caption blob (must REJECT)")
    print("=" * 70)
    spec = dict(VALID_BASE_SPEC); spec["caption"] = BRIEF_BLOB_CAPTION
    rc, data = run_create(spec)
    print(f"  exit code = {rc}")
    print_violations("test1", data)
    after = list_task_ids()
    delta = after - before_ids
    print(f"  ClickUp delta after test 1: {len(delta)} new task(s) {sorted(delta) if delta else ''}")
    if delta:
        new_task_ids.extend(delta); before_ids = after
    print()

    # Test 2: blockquote line
    print("=" * 70)
    print("TEST 2: caption contains '> ' markdown blockquote line (must REJECT)")
    print("=" * 70)
    spec = dict(VALID_BASE_SPEC); spec["caption"] = BLOCKQUOTE_CAPTION
    rc, data = run_create(spec)
    print(f"  exit code = {rc}")
    print_violations("test2", data)
    after = list_task_ids()
    delta = after - before_ids
    print(f"  ClickUp delta after test 2: {len(delta)} new task(s) {sorted(delta) if delta else ''}")
    if delta:
        new_task_ids.extend(delta); before_ids = after
    print()

    # Test 3: em-dash
    print("=" * 70)
    print("TEST 3: caption contains an em-dash U+2014 (must REJECT)")
    print("=" * 70)
    spec = dict(VALID_BASE_SPEC); spec["caption"] = EM_DASH_CAPTION
    rc, data = run_create(spec)
    print(f"  exit code = {rc}")
    print_violations("test3", data)
    after = list_task_ids()
    delta = after - before_ids
    print(f"  ClickUp delta after test 3: {len(delta)} new task(s) {sorted(delta) if delta else ''}")
    if delta:
        new_task_ids.extend(delta); before_ids = after
    print()

    # Test 4: missing platform
    print("=" * 70)
    print("TEST 4: task missing Platform (must REJECT)")
    print("=" * 70)
    spec = dict(VALID_BASE_SPEC)
    spec["caption"] = CLEAN_VALID_CAPTION
    spec["platform"] = []  # empty
    rc, data = run_create(spec)
    print(f"  exit code = {rc}")
    print_violations("test4", data)
    after = list_task_ids()
    delta = after - before_ids
    print(f"  ClickUp delta after test 4: {len(delta)} new task(s) {sorted(delta) if delta else ''}")
    if delta:
        new_task_ids.extend(delta); before_ids = after
    print()

    # Test 5: clean valid input -> must CREATE exactly one well-formed task
    print("=" * 70)
    print("TEST 5: clean valid input (must CREATE exactly one task, draft)")
    print("=" * 70)
    spec = dict(VALID_BASE_SPEC); spec["caption"] = CLEAN_VALID_CAPTION
    rc, data = run_create(spec)
    print(f"  exit code = {rc}")
    print(f"  status     = {data.get('status')}")
    print(f"  task_id    = {data.get('task_id')}")
    print(f"  task_url   = {data.get('task_url')}")
    after = list_task_ids()
    delta = after - before_ids
    print(f"  ClickUp delta after test 5: {len(delta)} new task(s) {sorted(delta) if delta else ''}")
    if delta:
        new_task_ids.extend(delta); before_ids = after
    print()

    # Final report
    print("=" * 70)
    print("FINAL VERIFICATION")
    print("=" * 70)
    print(f"Total new tasks created across all 5 tests: {len(new_task_ids)}")
    print(f"Expected: 1 (from test 5 only). Actual: {len(new_task_ids)}")
    if len(new_task_ids) == 1:
        # Read back the created task and verify
        tid = new_task_ids[0]
        r = requests.get(
            f"https://api.clickup.com/api/v2/task/{tid}",
            headers={"Authorization": TOKEN}, timeout=30,
        )
        if r.status_code == 200:
            d = r.json()
            desc = d.get("description") or ""
            print(f"  Created task: {tid}")
            print(f"    name     : {d.get('name')}")
            print(f"    status   : {(d.get('status') or {}).get('status')}")
            print(f"    list     : {(d.get('list') or {}).get('id')}")
            print(f"    desc len : {len(desc)} chars")
            print(f"    desc head: {desc[:80]!r}")
            print(f"    desc == caption: {desc.strip() == CLEAN_VALID_CAPTION.strip()}")
    print()
    return 0 if len(new_task_ids) == 1 else 1


if __name__ == "__main__":
    sys.exit(main())
