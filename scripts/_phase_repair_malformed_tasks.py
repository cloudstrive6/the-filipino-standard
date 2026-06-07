"""One-shot repair of three malformed ClickUp tasks. Constrained to list
901614911598. Does NOT touch HelloNorg or any other list. Does NOT touch
locked tasks. Does NOT publish. Leaves status untouched (needs-revision)."""
import truststore; truststore.inject_into_ssl()

import os
import sys
import json
import datetime as dt
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))
TOKEN = os.environ["CLICKUP_API_TOKEN"]
LIST_ID = "901614911598"
PHT = ZoneInfo("Asia/Manila")

# Canonical custom field IDs (from publisher.py + validator + SKILL.md)
F_PILLAR        = "b2ffb6c6-7fe3-412d-9ba3-a7ca2ca5e9f1"
F_POST_TYPE     = "6a3e613e-524d-4471-b9eb-8fc5451e3077"
F_PLATFORM      = "ef8cfddd-c950-40b8-95ca-6da001c6ac50"
F_SCHEDULED     = "8a89f1c0-f964-4281-bbe8-82f2bc187ca0"
F_ORIG_DRAFT    = "54a8d8d0-f051-4e70-a50c-0ec526bbc1cf"
F_FINAL_CAPTION = "f9e3e3eb-de98-406a-a716-84760d13457a"
F_ARCHETYPE     = "ff14a5f5-9124-4a92-95c0-44a33dde7ee7"
F_STYLE         = "d91c15b8-95dc-47f2-aa70-6d58effa7b01"
F_TOPIC_NUMBER  = "a3cfd2e6-f963-4e39-9778-c4d488802d00"
F_NEWS_HOOK     = "fa544f0a-85e2-4b55-bf41-9c121898930f"

# Pillar option IDs
P_GOVERNANCE = "b161199b-3f7c-4f5c-a0df-b3d882259ee5"
P_POLITICAL  = "891ebe37-d6db-4949-9f09-11c5360b9b16"
P_CONSTITUTIONAL = "346a2cb6-828f-4057-a56a-ea78abc809cd"
P_ECONOMIC   = "d2c5c063-69c6-4c1a-b3c9-d8fddcfb248e"
P_EMPOWERMENT = "6814f263-c442-4fb0-9bac-8d64fb527d89"
P_SME        = "1c142aa1-1160-4d0a-b50f-89da54909b58"

# Post Type option IDs
PT_STATIC   = "b411d4ca-43db-4cd8-b2dd-37f10e88d38a"
PT_REACTIVE = "95f47825-d966-4381-a856-1e2a709e3da9"
PT_HYBRID   = "15b2a458-ff72-49ba-8610-f6eb89df4353"
PT_REELS    = "d0f50ee1-28ee-4030-ac2f-60c5baaca0dc"

# Platform option IDs
PL_FACEBOOK  = "673cfb92-15e7-4315-9bbf-94db2baffa08"
PL_INSTAGRAM = "32e72ad5-83d0-4a92-87f6-b8c8b4990a44"
PL_THREADS   = "225e6544-1287-44b7-a019-7f3b1fdc31e1"
PL_REDDIT    = "5bd32f20-3976-4c9e-931b-6f1d562c8c58"

# Archetype option IDs
A_EDIT_ALLEGORY = "46c41af2-5383-476b-b37b-e145f47f65cc"
A_PH_VS_NZ      = "d2521238-88ac-44c4-9248-3e4f5f5fc4b6"
A_SATIRICAL     = "f3b26d25-0a2a-49eb-98c5-b0a8663e8eb2"
A_CONSTITUTIONAL = "a67ef797-f40c-4e9e-8c78-d67c41471dfa"
A_PAIN_POINT    = "207c9b7b-c2ba-4d21-919d-08665929a374"

# Far-future safety: 2027-01-15 08:00 PHT (used elsewhere in the repo)
FAR_FUTURE_MS = int(dt.datetime(2027, 1, 15, 8, 0, 0, tzinfo=PHT).timestamp() * 1000)


def read_caption(filename: str) -> str:
    p = os.path.join(ROOT, "output", "posts", filename)
    return open(p, encoding="utf-8").read().rstrip()


def safety_check_task(task_id: str) -> bool:
    """Refuse to act on tasks outside list 901614911598 or on the locked task."""
    if task_id == "86d30n0ne":
        raise RuntimeError(f"REFUSE: {task_id} is the locked benchmark task")
    r = requests.get(
        f"https://api.clickup.com/api/v2/task/{task_id}",
        headers={"Authorization": TOKEN}, timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Could not fetch {task_id}: HTTP {r.status_code}")
    d = r.json()
    list_id = (d.get("list") or {}).get("id")
    if list_id != LIST_ID:
        raise RuntimeError(
            f"REFUSE: {task_id} is on list {list_id}, not {LIST_ID}",
        )
    return True


def update_description(task_id: str, text: str) -> int:
    r = requests.put(
        f"https://api.clickup.com/api/v2/task/{task_id}",
        headers={"Authorization": TOKEN, "Content-Type": "application/json"},
        json={"description": text},
        timeout=30,
    )
    return r.status_code


def set_field(task_id: str, field_id: str, value) -> int:
    r = requests.post(
        f"https://api.clickup.com/api/v2/task/{task_id}/field/{field_id}",
        headers={"Authorization": TOKEN, "Content-Type": "application/json"},
        json={"value": value},
        timeout=30,
    )
    return r.status_code


# --- repair payloads (per task) -------------------------------------------

REPAIRS = [
    {
        "task_id": "86d30khk6",
        "caption_file": "2026-05-15-oil-shock-rescue-loan-fb.extracted-caption.txt",
        "fields": {
            F_PILLAR:    P_ECONOMIC,
            F_POST_TYPE: PT_HYBRID,
            F_PLATFORM:  [PL_FACEBOOK],
            F_ARCHETYPE: A_EDIT_ALLEGORY,
            F_TOPIC_NUMBER: 4.11,
            F_NEWS_HOOK: (
                "ADB offers PH up to USD 1.75B in crisis lending (May 15, 2026) "
                "amid the Middle East oil shock; peso at record low 61.64; April "
                "inflation 7.2%, transport costs up 21.4% after fuel spike; Q1 "
                "growth at 2.8% (weakest since pandemic). No PH strategic "
                "petroleum reserve."
            ),
            F_SCHEDULED: FAR_FUTURE_MS,
        },
    },
    {
        "task_id": "86d30te8y",
        "caption_file": "2026-05-16-ofw-economy-ceiling-fb.extracted-caption.txt",
        "fields": {
            F_PILLAR:    P_ECONOMIC,
            F_POST_TYPE: PT_REACTIVE,
            F_PLATFORM:  [PL_FACEBOOK],
            F_ARCHETYPE: A_EDIT_ALLEGORY,
            F_NEWS_HOOK: (
                "Q1 2026 OFW deployment collapsed 45.15% YoY to 526,464 (vs "
                "767,057 Q1 2025) - DMW. March cash remittances at USD 2.87B, "
                "growth of only 2.3% YoY - slowest in nearly 3 years - BSP. "
                "About 40,000 cleared OFWs grounded as Middle East corridor "
                "froze. SWS Mar 2026: 52% of families (14.5M households) "
                "self-rated poor."
            ),
            F_SCHEDULED: FAR_FUTURE_MS,
        },
    },
    {
        "task_id": "86d30w135",
        "caption_file": "2026-05-16-senate-public-trust-shot-fb.extracted-caption.txt",
        "fields": {
            F_PILLAR:    P_CONSTITUTIONAL,
            F_POST_TYPE: PT_REACTIVE,
            F_PLATFORM:  [PL_FACEBOOK],
            F_ARCHETYPE: A_EDIT_ALLEGORY,
            F_NEWS_HOOK: (
                "ICC warrant against Sen. Bato Dela Rosa unsealed May 11, 2026 "
                "(alleged murder of 32 persons July 2016-Apr 2018, Duterte drug "
                "war). May 13 gunfire on Senate premises in Pasay: Senate "
                "Sergeant-at-Arms Mao Aplasca fires warning shot at NBI agent; "
                "NBI agent fires back; Dela Rosa quietly leaves building. May 15 "
                "Ombudsman orders 6-month preventive suspension of Aplasca; "
                "NBI probes whether shooting was staged."
            ),
            F_SCHEDULED: FAR_FUTURE_MS,
        },
    },
]


def main() -> int:
    print("=" * 70)
    print("Repair pass: malformed tasks on list 901614911598")
    print("Safety: refuses any task not on this list; refuses 86d30n0ne.")
    print("=" * 70)
    print()

    for spec in REPAIRS:
        tid = spec["task_id"]
        print(f"--- {tid} ---")

        # Safety check
        try:
            safety_check_task(tid)
        except RuntimeError as e:
            print(f"  SAFETY ABORT: {e}")
            continue

        # Caption
        caption_path = os.path.join(ROOT, "output", "posts", spec["caption_file"])
        caption = open(caption_path, encoding="utf-8").read().rstrip()
        EM = chr(0x2014); EN = chr(0x2013)
        if EM in caption or EN in caption:
            print(f"  STOP - em/en dash in caption; refusing to write")
            continue

        # PUT description (the actual repair)
        rc = update_description(tid, caption)
        print(f"  PUT description -> HTTP {rc}  ({len(caption)} chars)")

        # Original AI Draft + Final Caption (mirror description for consistency)
        rc = set_field(tid, F_ORIG_DRAFT, caption)
        print(f"  set Original AI Draft -> HTTP {rc}")
        rc = set_field(tid, F_FINAL_CAPTION, caption)
        print(f"  set Final Caption     -> HTTP {rc}")

        # Other custom fields
        for fid, val in spec["fields"].items():
            rc = set_field(tid, fid, val)
            short_val = (json.dumps(val)[:50] if not isinstance(val, str)
                         else (val[:50] + ("..." if len(val) > 50 else "")))
            print(f"  set {fid[:8]}... = {short_val!r} -> HTTP {rc}")

        print()

    print("Repair pass complete. Status of each task is unchanged (per spec).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
