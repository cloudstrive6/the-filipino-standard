"""
fix_scheduled_publish.py - one-off repair + schedule report.

The ClickUp MCP stored the "Scheduled Publish" date custom field as date-only
(midnight), dropping the time. The publisher reads THIS field to decide when to
post, so midnight values are wrong (and can trip the 6h staleness guard). The
native due_date, however, kept the correct instant WITH time.

This script, for each task id passed, copies the task's due_date into the
Scheduled Publish custom field WITH value_options.time=true (so the time is
stored and displayed), then prints every task's resulting publish time in PHT
and NZT, sorted chronologically.

Usage: py fix_scheduled_publish.py id1,id2,id3   (omit ids -> dry report only)
Add --report to only print times without writing.
"""
from __future__ import annotations
import truststore
truststore.inject_into_ssl()
import datetime as dt
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
TOKEN = (os.environ.get("CLICKUP_API_TOKEN") or "").strip()
BASE = "https://api.clickup.com/api/v2"
FIELD_SCHEDULED_PUBLISH = "8a89f1c0-f964-4281-bbe8-82f2bc187ca0"
PHT = ZoneInfo("Asia/Manila")
NZT = ZoneInfo("Pacific/Auckland")

S = requests.Session()
S.headers.update({"Authorization": TOKEN, "Content-Type": "application/json"})


def get_task(tid):
    for _ in range(5):
        try:
            r = S.get(f"{BASE}/task/{tid}", params={"custom_fields": "true"}, timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.RequestException:
            continue
    raise RuntimeError(f"get_task failed for {tid}")


def set_sched(tid, ms):
    for _ in range(5):
        try:
            r = S.post(f"{BASE}/task/{tid}/field/{FIELD_SCHEDULED_PUBLISH}",
                       json={"value": ms, "value_options": {"time": True}}, timeout=30)
            if r.status_code in (200, 201):
                return True, ""
            return False, f"HTTP {r.status_code} {r.text[:120]}"
        except requests.RequestException as e:
            last = str(e)
    return False, last


def field_value(task, fid):
    for f in task.get("custom_fields", []) or []:
        if f.get("id") == fid:
            return f.get("value")
    return None


def main():
    args = [a for a in sys.argv[1:] if a != "--report"]
    report_only = "--report" in sys.argv
    ids = args[0].split(",") if args else []
    rows = []
    for tid in ids:
        t = get_task(tid)
        name = (t.get("name") or "")[:52]
        due = t.get("due_date")
        sched = field_value(t, FIELD_SCHEDULED_PUBLISH)
        if not due:
            rows.append((None, tid, name, "NO due_date", "skipped"))
            continue
        ms = int(due)
        result = "report-only"
        if not report_only:
            ok, msg = set_sched(tid, ms)
            result = "OK" if ok else f"ERR {msg}"
        pht = dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).astimezone(PHT)
        nzt = dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).astimezone(NZT)
        rows.append((ms, tid, name, f"{pht:%a %b %d  %I:%M %p} PHT  ({nzt:%I:%M %p} NZT)", result))

    rows.sort(key=lambda r: (r[0] is None, r[0] or 0))
    now_pht = dt.datetime.now(PHT)
    print(f"\nNow: {now_pht:%a %b %d %I:%M %p} PHT\n")
    print(f"{'TASK':10} {'WHEN':42} {'NAME':54} RESULT")
    for ms, tid, name, when, result in rows:
        past = "  <-- PAST" if (ms and ms / 1000 < now_pht.timestamp()) else ""
        print(f"{tid:10} {when:42} {name:54} {result}{past}")


if __name__ == "__main__":
    main()
