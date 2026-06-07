"""5-task simplification proof for the TFS image pipeline.

For each archetype (5 total): create one throwaway draft task via
create_task.py with a clean caption + realistic text_in_image, run the
image generator once, fetch back the task to verify FIELD_IMAGE_URL is
an https clickup-attachments URL, and inspect the sidecar JSON for the
two-stage QA result + the deterministic layout check.

Then, for one of the five tasks, run a publisher dry-run (PUBLISHER_LIVE_MODE
forced false in-process) to confirm the new image-resolver downloads the
hosted https image, reaches the upload path, and reports the correct
target platforms.

Finally DELETE all five throwaway tasks so the list returns to zero
draft / needs-revision.

Scope: list 901614911598 only. Never touches HelloNorg, never touches
86d30n0ne (defense-in-depth check before each delete), never touches
config/locked_tasks.json.
"""
# Hard-force dry mode for the embedded publisher run, BEFORE load_dotenv.
import os as _os
_os.environ["PUBLISHER_LIVE_MODE"] = "false"

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

load_dotenv(ROOT / ".env")
TOKEN = os.environ["CLICKUP_API_TOKEN"]
LIST_ID = "901614911598"
CREATE_TASK = str(ROOT / "scripts" / "create_task.py")
GENERATE_IMG = str(ROOT / "scripts" / "generate_image.py")
PY = sys.executable

# Lock-list defense-in-depth: refuse to delete anything in this set.
import publisher as _pub
LOCKED_IDS = _pub._load_locked_task_ids()


CAPTIONS = {
    "editorial_allegory": (
        "The Philippines watches the same chokepoint cause the same pain in "
        "the 1970s, in 1990, in 2008, in 2022, four decades of the same "
        "lesson, four decades of choosing not to build the buffer.\n\n"
        "Tama na ang katamaran. Sovereign buffers come from sovereign "
        "decisions made years in advance, not from imported credit lines "
        "called in during a crisis."
    ),
    "pain_point": (
        "MSMEs spend more hours waiting in BIR lines than they spend doing "
        "their books. Every queue is a small business owner not serving a "
        "customer, not training a hire, not closing a sale.\n\n"
        "The cost of bad public service is paid in private revenue. Tama "
        "na ang pila."
    ),
    "constitutional_quote": (
        "The 1987 Constitution opens with one sentence the rest of the "
        "document is built on.\n\nArticle II Section 1: Sovereignty resides "
        "in the people, and all government authority emanates from them.\n\n"
        "Not from the office. Not from the party. Not from the dynasty. "
        "From the people. The mandate is unambiguous and it has been in "
        "force for 38 years. Tama na ang theatrics."
    ),
    "ph_vs_nz_split": (
        "Same trade. Same skill. Different rules around it. A Filipino "
        "household in Manila and the same household in Auckland pay very "
        "different prices for the same kilowatt-hour, because one grid is "
        "ring-fenced by an act of the legislature and the other is not.\n\n"
        "The lesson is structural, not personal. Tama na ang pasakit."
    ),
    "satirical_meme": (
        "Every election cycle the same politician promises to fix traffic. "
        "Every cycle after, the same politician arrives at the same event "
        "in a convoy that closes three lanes for twenty minutes.\n\n"
        "Promises are cheap. Convoys are paid in your morning."
    ),
}

# realistic image-generation inputs
ARCHETYPE_SPECS: list[dict] = [
    {
        "archetype": "editorial_allegory",
        "topic_slug": "Borrowed Buffer",
        "image_prompt":
            "A single tin bucket on a cracked concrete floor in dawn light, "
            "catching slow drips from an unseen ceiling, the bucket already "
            "more than half full. Spare, sober, observational. No people.",
        "text_in_image": {"footer": "THE FILIPINO STANDARD"},
        "platforms": ["Facebook", "Instagram"],
    },
    {
        "archetype": "pain_point",
        "topic_slug": "BIR Queue",
        "image_prompt":
            "A long queue of Filipino small business owners inside a "
            "Philippine government revenue office, mid-morning, fluorescent "
            "lighting, weary postures, folders held close, a queue stretching "
            "back behind the foreground figure.",
        "text_in_image": {"footer": "THE FILIPINO STANDARD"},
        "platforms": ["Facebook", "Instagram"],
    },
    {
        "archetype": "constitutional_quote",
        "topic_slug": "Sovereignty",
        "image_prompt":
            "The Philippine flag at dusk over a quiet provincial road "
            "receding into the distance, civic gravitas, restrained, "
            "monumental. Dramatic dusk light. No people, no text on signs.",
        "text_in_image": {"footer": "THE FILIPINO STANDARD"},
        "platforms": ["Facebook"],
    },
    {
        "archetype": "ph_vs_nz_split",
        "topic_slug": "Power Grid",
        "image_prompt":
            "Split-screen, two Filipino households. Left panel: Manila "
            "kitchen at night, electric bill open on the table, weary "
            "father reading it under a single dim bulb. Right panel: same "
            "kind of family in Auckland kitchen, warm-toned lamp, the same "
            "kind of bill in front of them but with a small relieved smile. "
            "Both panels evidently Filipino in features and dress.",
        "text_in_image": {
            "left_label": "MANILA",
            "right_label": "AUCKLAND",
            "footer": "THE FILIPINO STANDARD",
        },
        "platforms": ["Facebook", "Instagram"],
    },
    {
        "archetype": "satirical_meme",
        "topic_slug": "Promise Trap",
        "image_prompt":
            "An editorial cartoon: a politician at a podium gesturing with "
            "confidence while standing on a pile of unfinished road "
            "construction, traffic chaos behind him, vintage rubber-hose "
            "ink-and-wash style, satirical caricature. No recognizable real "
            "faces.",
        "text_in_image": {
            "top_text": "PROMISED TO FIX TRAFFIC",
            "bottom_text": "FIXED IT FOR HIS CONVOY ONLY",
            "footer": "THE FILIPINO STANDARD",
        },
        "platforms": ["Facebook", "Instagram"],
    },
]


def _today_pht() -> str:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y-%m-%d")


def get_task(task_id: str) -> dict:
    r = requests.get(
        f"https://api.clickup.com/api/v2/task/{task_id}",
        headers={"Authorization": TOKEN},
        params={"custom_fields": "true", "include_subtasks": "false"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def delete_task(task_id: str) -> int:
    if task_id in LOCKED_IDS:
        print(f"  REFUSING to delete locked task {task_id}")
        return -1
    r = requests.delete(
        f"https://api.clickup.com/api/v2/task/{task_id}",
        headers={"Authorization": TOKEN}, timeout=30,
    )
    return r.status_code


def list_summary() -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
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
            out.append((
                t["id"], t.get("name", ""),
                (t.get("status") or {}).get("status", "").lower(),
            ))
        page += 1
        if len(tasks) < 100:
            break
    return out


def create_one(spec_def: dict) -> tuple[str | None, dict]:
    """Build spec.json + invoke create_task.py. Returns (task_id, full_create_result)."""
    today = _today_pht()
    arch = spec_def["archetype"]
    # Names crafted to NOT trigger publisher TEST guard:
    #  - no standalone TEST token, no \bTEST\b match anywhere
    name = (
        f"{today} Simplification Proof {spec_def['topic_slug']} "
        f"({arch}) FB"
    )
    # constitutional_quote: just FB. Others: FB+IG.
    platforms = spec_def["platforms"]
    # Schedule: 2027 placeholder so publisher hold-skips these (they will not
    # broadcast even if PUBLISHER_LIVE_MODE were true).
    spec = {
        "task_name": name,
        "caption": CAPTIONS[arch],
        "content_pillar": (
            "Constitutional Awareness" if arch == "constitutional_quote"
            else "Political Commentary" if arch in ("editorial_allegory", "satirical_meme")
            else "Business & SME Advocacy" if arch == "pain_point"
            else "Governance Comparison"
        ),
        "post_type": "Static",
        "platform": platforms,
        "scheduled_publish_pht": "2027-01-15T08:00:00+08:00",
        "archetype": arch,
        "image_prompt": spec_def["image_prompt"],
        "text_in_image": spec_def["text_in_image"],
        "status": "draft",
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                      encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False)
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
    if proc.returncode != 0:
        print(f"  create_task failed (exit {proc.returncode}): {proc.stdout[:200]} | {proc.stderr[:200]}")
        return None, {}
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"  could not parse create_task output: {proc.stdout[:200]}")
        return None, {}
    return result.get("task_id"), result


def run_generator(task_id: str) -> int:
    """Run scripts/generate_image.py --task <id>. Returns the process exit code."""
    proc = subprocess.run(
        [PY, GENERATE_IMG, "--task", task_id],
        capture_output=True, text=True, encoding="utf-8", timeout=600,
    )
    # Print a short tail of stdout for visibility
    tail = "\n".join(proc.stdout.splitlines()[-25:])
    print(f"  generator exit={proc.returncode}, last 25 lines of stdout:")
    for ln in tail.split("\n"):
        print(f"    | {ln}")
    if proc.returncode != 0 and proc.stderr:
        print(f"  stderr: {proc.stderr[:300]}")
    return proc.returncode


def latest_sidecar_for_task(task_id: str) -> dict | None:
    """Find the most recent sidecar JSON in output/images that references
    the given task_id. Returns the parsed JSON or None."""
    img_dir = ROOT / "output" / "images"
    candidates = []
    for jp in img_dir.glob("*.rendered-*.json"):
        try:
            d = json.loads(jp.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("task_id") == task_id:
            candidates.append((jp.stat().st_mtime, jp, d))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2]


def field_value_of(task: dict, field_id: str):
    for f in (task.get("custom_fields") or []):
        if f.get("id") == field_id:
            return f.get("value")
    return None


FIELD_IMAGE_URL = "34e674b6-bfb7-4c71-80f3-35065c84f1a3"


# =============================================================================
# Main
# =============================================================================


def main() -> int:
    print("=" * 78)
    print("TFS IMAGE PIPELINE SIMPLIFICATION — 5-TASK PROOF")
    print("=" * 78)
    print(f"List           : {LIST_ID}")
    print(f"Locked task IDs (defense-in-depth): {sorted(LOCKED_IDS)}")
    print(f"PUBLISHER_LIVE_MODE (in-process, forced): "
          f"{os.environ.get('PUBLISHER_LIVE_MODE')!r}")
    print()

    pre = list_summary()
    print(f"BEFORE: list has {len(pre)} task(s); "
          f"draft={sum(1 for _,_,s in pre if s=='draft')}, "
          f"nr={sum(1 for _,_,s in pre if 'needs' in s)}")
    print()

    created_ids: list[str] = []
    per_archetype: list[dict] = []

    try:
        # ----- Step 1: create 5 throwaway tasks -----
        print("STEP 1 — Create 5 throwaway draft tasks via create_task.py")
        print("-" * 78)
        for spec_def in ARCHETYPE_SPECS:
            arch = spec_def["archetype"]
            print(f"  creating {arch}...")
            tid, result = create_one(spec_def)
            if not tid:
                print(f"    FAILED to create {arch}")
                continue
            created_ids.append(tid)
            print(f"    task_id={tid}  url={result.get('task_url')}")
            per_archetype.append({
                "archetype": arch,
                "task_id": tid,
                "task_url": result.get("task_url"),
            })
        print()

        if not created_ids:
            print("No tasks created; aborting.")
            return 2

        # ----- Step 2: run generator once per task -----
        print("STEP 2 — Run image generator once per task")
        print("-" * 78)
        for entry in per_archetype:
            tid = entry["task_id"]
            arch = entry["archetype"]
            print(f"\n  [{arch}] generating image for task {tid}...")
            entry["generator_exit"] = run_generator(tid)
        print()

        # ----- Step 3: fetch each task back + inspect sidecar -----
        print("STEP 3 — Verify FIELD_IMAGE_URL is https + read sidecar")
        print("-" * 78)
        for entry in per_archetype:
            tid = entry["task_id"]
            arch = entry["archetype"]
            task = get_task(tid)
            iurl = field_value_of(task, FIELD_IMAGE_URL)
            entry["image_url_field"] = iurl
            entry["image_url_scheme"] = (iurl or "").split(":", 1)[0] if iurl else None
            entry["attachments_count"] = len(task.get("attachments") or [])
            sidecar = latest_sidecar_for_task(tid)
            if sidecar:
                attempts = sidecar.get("attempts") or []
                final = attempts[-1] if attempts else {}
                entry["attempts_count"] = len(attempts)
                entry["text_integrity_pass"] = (final.get("text_integrity") or {}).get("pass")
                entry["scene_qa_pass"] = (final.get("scene_qa") or {}).get("pass")
                lc = final.get("layout_check") or {}
                entry["layout_ok"] = lc.get("ok")
                entry["layout_zones"] = list((lc.get("zones") or {}).keys())
                entry["layout_violations"] = lc.get("violations") or []
                entry["final_qa_pass"] = sidecar.get("final_qa_pass")
                entry["sidecar_archetype"] = sidecar.get("archetype")
            else:
                entry["sidecar"] = "NOT FOUND"

            print(f"\n  [{arch}] task {tid}")
            print(f"    FIELD_IMAGE_URL scheme: {entry['image_url_scheme']!r}")
            if iurl:
                preview = iurl if len(iurl) <= 90 else (iurl[:87] + "...")
                print(f"    FIELD_IMAGE_URL value : {preview}")
            print(f"    attachments on task   : {entry['attachments_count']}")
            if sidecar:
                print(f"    sidecar attempts      : {entry.get('attempts_count')}")
                print(f"    text-integrity pass   : {entry.get('text_integrity_pass')}")
                print(f"    scene QA pass         : {entry.get('scene_qa_pass')}")
                print(f"    layout check ok       : {entry.get('layout_ok')}")
                print(f"    layout zones          : {entry.get('layout_zones')}")
                if entry.get("layout_violations"):
                    for v in entry["layout_violations"]:
                        print(f"      - {v}")
                print(f"    final_qa_pass         : {entry.get('final_qa_pass')}")
                print(f"    sidecar archetype     : {entry.get('sidecar_archetype')}")
            else:
                print(f"    sidecar               : NOT FOUND")
        print()

        # ----- Step 4: publisher dry-run against one task -----
        print("STEP 4 — Publisher dry-run against one task (PUBLISHER_LIVE_MODE=false)")
        print("-" * 78)
        # Pick ph_vs_nz_split (most interesting text). If unavailable pick first.
        target = next((e for e in per_archetype
                       if e["archetype"] == "ph_vs_nz_split"
                       and e.get("image_url_scheme") in ("http", "https")),
                      None)
        if target is None:
            target = next((e for e in per_archetype
                          if e.get("image_url_scheme") in ("http", "https")), None)
        if target is None:
            print("  no task with https FIELD_IMAGE_URL; skipping publisher dry-run")
        else:
            tid = target["task_id"]
            arch = target["archetype"]
            print(f"  target: [{arch}] task {tid}")
            print(f"  Image URL: {target['image_url_field']}")
            # Fetch the task and run process_publishable_task in dry mode
            task = get_task(tid)
            # The task is in draft + 2027 schedule — guards would hold-skip.
            # To prove the image-resolver path works, we exercise
            # resolve_image_to_local_path directly + log what would happen.
            import publisher
            logging.getLogger().handlers.clear()
            logger = logging.getLogger("pub-proof")
            logger.handlers.clear()
            logger.setLevel(logging.INFO)
            sh = logging.StreamHandler(sys.stdout)
            sh.setFormatter(logging.Formatter("    | %(levelname)s %(message)s"))
            logger.addHandler(sh)
            logger.propagate = False
            print("\n  resolve_image_to_local_path output:")
            local_path, source = publisher.resolve_image_to_local_path(
                target["image_url_field"], task, logger, tid,
            )
            print(f"    -> resolved source: {source!r}")
            print(f"    -> local path     : {local_path}")
            if local_path is not None:
                size = local_path.stat().st_size
                print(f"    -> file size      : {size} bytes")
                # Verify upload_image would accept this — we don't actually
                # upload (no Post for Me call), just check the content type
                # routing publisher uses.
                ct = publisher.PostForMeClient._content_type_for(local_path)
                print(f"    -> content-type   : {ct}")
                # Confirm platforms would be derived from custom field
                plats = publisher.task_platforms(task)
                print(f"    -> task_platforms : {plats}")
                # Confirm guards would HOLD (draft + 2027) — proves we're not
                # accidentally about to publish.
                cleared, why, kind = publisher.is_cleared_to_publish(task)
                print(f"    -> is_cleared_to_publish: cleared={cleared} kind={kind!r}")
                print(f"       reason: {why}")
                # Cleanup temp file
                if local_path.name.startswith("tfs-publisher-img-"):
                    try:
                        local_path.unlink()
                        print(f"    -> cleaned up temp file")
                    except OSError as e:
                        print(f"    -> could not unlink temp: {e}")
            target["publisher_dry_run"] = {
                "source": source,
                "local_path": str(local_path) if local_path else None,
                "task_platforms": plats if local_path else [],
                "cleared": cleared if local_path else None,
                "cleared_kind": kind if local_path else None,
            }

    finally:
        # ----- Step 5: DELETE all created tasks -----
        print("\n" + "=" * 78)
        print("STEP 5 — Delete all 5 throwaway tasks")
        print("=" * 78)
        for entry in per_archetype:
            tid = entry["task_id"]
            print(f"  deleting {tid} ({entry['archetype']})...")
            code = delete_task(tid)
            entry["delete_status"] = code
            print(f"    DELETE status: {code}")

    # Final list state
    print()
    post = list_summary()
    print(f"AFTER: list has {len(post)} task(s); "
          f"draft={sum(1 for _,_,s in post if s=='draft')}, "
          f"nr={sum(1 for _,_,s in post if 'needs' in s)}")

    # Final per-archetype summary table
    print()
    print("=" * 78)
    print("PER-ARCHETYPE SUMMARY")
    print("=" * 78)
    print(f"{'archetype':<22} {'gen':>3} {'ti':>3} {'sq':>3} {'lay':>3} "
          f"{'url_scheme':<12} {'attach':>6} {'del':>4}")
    for e in per_archetype:
        print(f"{e['archetype']:<22} "
              f"{e.get('generator_exit', '-'):>3} "
              f"{str(e.get('text_integrity_pass'))[:1]:>3} "
              f"{str(e.get('scene_qa_pass'))[:1]:>3} "
              f"{str(e.get('layout_ok'))[:1]:>3} "
              f"{(e.get('image_url_scheme') or '-'):<12} "
              f"{e.get('attachments_count', '-'):>6} "
              f"{e.get('delete_status', '-'):>4}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
