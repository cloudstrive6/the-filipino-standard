"""
threads_tagalog_planner.py - History-aware Tagalog-placement picker.

The Filipino Standard's ~10% Tagalog beat in Threads-targeted posts must
vary across recent posts so the structure never repeats consecutively.
Pure random selection is NOT acceptable. This planner enforces a
deterministic, history-aware policy:

  1. Read the rolling history from config/threads_tagalog_history.json.
  2. Choose a placement pattern that is NOT in the last 2 entries.
  3. Among the remaining candidates, prefer the one used LEAST recently
     (or never used). Ties resolve to the canonical order in the
     `patterns` list.
  4. When the agent commits the post, record the (placement, phrase,
     task_id, task_name, source_skill) tuple. The history is trimmed
     to the most recent 7 entries.

The five placement patterns are:

  opening_hook   - the first line is the Tagalog beat; English follows.
  mid_pivot      - English open, Tagalog pivots, English closer.
  closing_line   - English-only until the closing Tagalog beat.
  inline_woven   - Tagalog beat as a clause inside an English sentence.
  standalone_beat- Tagalog beat on its own line in the middle of the post.

CLI:

  py threads_tagalog_planner.py peek
      Show which placement the planner WOULD pick next, plus recent
      phrases to avoid. No state change.

  py threads_tagalog_planner.py commit \\
       --placement <name> \\
       --phrase "Ganun kalala." \\
       --task-id 86d3abcd \\
       --task-name "..." \\
       --source threads-creator
      Record a new entry. Trims history to 7 most recent.

  py threads_tagalog_planner.py next \\
       --phrase "Ganun kalala." \\
       --task-id 86d3abcd \\
       --task-name "..." \\
       --source threads-creator
      Combined: pick + commit in one atomic step. The chosen placement
      is whatever `peek` would have returned. Prints the same JSON.

  py threads_tagalog_planner.py show
      Print the current history.

  py threads_tagalog_planner.py reset
      Empty the history. Confirmation required.

Output: always a JSON object on stdout. Logs and warnings go to stderr.

Exit codes:
  0 - success
  1 - validation error (bad placement name, missing arg, etc.)
  2 - I/O error (history file unreadable)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parent.parent
HISTORY_PATH = PROJECT_ROOT / "config" / "threads_tagalog_history.json"
PHT = ZoneInfo("Asia/Manila")

# How many entries the planner remembers. Older entries are dropped on commit.
MAX_HISTORY = 7

# How many of the most recent entries are HARD-banned for the next pick.
RECENT_BAN_WINDOW = 2

# How many most recent entries are checked for Tagalog-phrase reuse advisory.
PHRASE_AVOID_WINDOW = 5

VALID_PATTERNS: list[str] = [
    "opening_hook",
    "mid_pivot",
    "closing_line",
    "inline_woven",
    "standalone_beat",
]


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _empty_doc() -> dict[str, Any]:
    return {
        "_doc": (
            "Rolling history of the Tagalog placement pattern + key Tagalog "
            "phrase used in recent Threads-targeted posts. Selection is "
            "deterministic and history-aware - never random. Max 7 entries."
        ),
        "patterns": list(VALID_PATTERNS),
        "entries": [],
    }


def load_history() -> dict[str, Any]:
    if not HISTORY_PATH.exists():
        return _empty_doc()
    try:
        data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        sys.stderr.write(
            f"WARNING: could not parse {HISTORY_PATH}: {e}. "
            f"Treating as empty history.\n"
        )
        return _empty_doc()
    # Defensive: re-merge in case keys went missing
    if "entries" not in data or not isinstance(data["entries"], list):
        data["entries"] = []
    if "patterns" not in data or not isinstance(data["patterns"], list):
        data["patterns"] = list(VALID_PATTERNS)
    return data


def save_history(doc: dict[str, Any]) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Selection policy
# ---------------------------------------------------------------------------


def pick_next_placement(
    entries: list[dict[str, Any]],
    patterns: list[str] = VALID_PATTERNS,
) -> tuple[str, dict[str, Any]]:
    """Choose the next placement pattern, history-aware and deterministic.

    Returns (chosen_pattern, debug_info).

    Policy:
      1. Hard-ban: any pattern that appears in the last RECENT_BAN_WINDOW
         entries cannot be picked.
      2. Among candidates, prefer the one used LEAST recently (oldest
         last-seen index, with `never used` ranked as oldest).
      3. Ties resolve in the canonical `patterns` list order.
    """
    recent = [e.get("placement") for e in entries[-RECENT_BAN_WINDOW:]]
    banned = {p for p in recent if p in patterns}
    candidates = [p for p in patterns if p not in banned]
    if not candidates:
        # Defensive: history has every pattern banned (shouldn't happen with
        # a window of 2 and 5 patterns). Fall back to all patterns.
        candidates = list(patterns)

    # Compute last-seen index for each pattern (earliest = oldest)
    last_seen: dict[str, int] = {}
    for i, e in enumerate(entries):
        p = e.get("placement")
        if p in patterns:
            last_seen[p] = i

    def rank(p: str) -> tuple[int, int]:
        # Patterns never used get rank -1 (oldest); else use last-seen index.
        # Secondary: canonical-order index for tie-break.
        return (last_seen.get(p, -1), patterns.index(p))

    candidates.sort(key=rank)
    chosen = candidates[0]
    return chosen, {
        "candidates_considered": candidates,
        "banned_due_to_recency": sorted(banned),
        "last_seen_indices": {p: last_seen.get(p, None) for p in patterns},
        "policy": (
            f"Exclude last {RECENT_BAN_WINDOW}; prefer least-recently-used; "
            f"tie-break on canonical order."
        ),
    }


def recent_phrases(
    entries: list[dict[str, Any]],
    window: int = PHRASE_AVOID_WINDOW,
) -> list[str]:
    return [e.get("tagalog_phrase", "") for e in entries[-window:]]


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_peek(args: argparse.Namespace) -> int:
    doc = load_history()
    entries = doc.get("entries", [])
    chosen, debug = pick_next_placement(entries, doc.get("patterns", VALID_PATTERNS))
    out = {
        "action": "peek",
        "chosen_placement": chosen,
        "recent_phrases_to_avoid": recent_phrases(entries),
        "history_depth": len(entries),
        "selection_debug": debug,
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    doc = load_history()
    print(json.dumps(doc, indent=2, ensure_ascii=False))
    return 0


def cmd_commit(args: argparse.Namespace) -> int:
    if args.placement not in VALID_PATTERNS:
        sys.stderr.write(
            f"ERROR: placement {args.placement!r} not in {VALID_PATTERNS}\n"
        )
        return 1
    if not (args.phrase or "").strip():
        sys.stderr.write("ERROR: --phrase is required and must be non-empty.\n")
        return 1
    if not (args.task_id or "").strip():
        sys.stderr.write("ERROR: --task-id is required.\n")
        return 1

    doc = load_history()
    entry = {
        "task_id": args.task_id.strip(),
        "task_name": (args.task_name or "").strip(),
        "placement": args.placement,
        "tagalog_phrase": args.phrase.strip(),
        "source_skill": args.source.strip() if args.source else "unknown",
        "created_at_pht": dt.datetime.now(PHT).isoformat(timespec="seconds"),
    }
    doc.setdefault("entries", []).append(entry)
    # Trim to MAX_HISTORY most recent
    if len(doc["entries"]) > MAX_HISTORY:
        doc["entries"] = doc["entries"][-MAX_HISTORY:]
    save_history(doc)

    print(json.dumps({
        "action": "commit",
        "entry": entry,
        "history_depth_after": len(doc["entries"]),
    }, indent=2, ensure_ascii=False))
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    """Atomic: pick + commit. Returns the chosen placement and records it."""
    if not (args.phrase or "").strip():
        sys.stderr.write("ERROR: --phrase is required and must be non-empty.\n")
        return 1
    if not (args.task_id or "").strip():
        sys.stderr.write("ERROR: --task-id is required.\n")
        return 1

    doc = load_history()
    entries = doc.get("entries", [])
    chosen, debug = pick_next_placement(entries, doc.get("patterns", VALID_PATTERNS))
    entry = {
        "task_id": args.task_id.strip(),
        "task_name": (args.task_name or "").strip(),
        "placement": chosen,
        "tagalog_phrase": args.phrase.strip(),
        "source_skill": args.source.strip() if args.source else "unknown",
        "created_at_pht": dt.datetime.now(PHT).isoformat(timespec="seconds"),
    }
    entries.append(entry)
    if len(entries) > MAX_HISTORY:
        entries = entries[-MAX_HISTORY:]
    doc["entries"] = entries
    save_history(doc)

    print(json.dumps({
        "action": "next",
        "chosen_placement": chosen,
        "entry": entry,
        "history_depth_after": len(entries),
        "selection_debug": debug,
    }, indent=2, ensure_ascii=False))
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    if not args.yes:
        sys.stderr.write(
            "Refusing to reset without --yes. This erases the placement history.\n"
        )
        return 1
    save_history(_empty_doc())
    print(json.dumps({"action": "reset", "history_depth_after": 0}, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("peek", help="Show which placement the planner would pick next.")
    sub.add_parser("show", help="Print the current history.")

    p_commit = sub.add_parser("commit", help="Record an entry after the agent has used a placement.")
    p_commit.add_argument("--placement", required=True, choices=VALID_PATTERNS)
    p_commit.add_argument("--phrase", required=True)
    p_commit.add_argument("--task-id", required=True)
    p_commit.add_argument("--task-name", default="")
    p_commit.add_argument("--source", default="unknown")

    p_next = sub.add_parser("next", help="Atomic pick + commit.")
    p_next.add_argument("--phrase", required=True)
    p_next.add_argument("--task-id", required=True)
    p_next.add_argument("--task-name", default="")
    p_next.add_argument("--source", default="unknown")

    p_reset = sub.add_parser("reset", help="Empty the history (--yes required).")
    p_reset.add_argument("--yes", action="store_true")

    args = parser.parse_args()
    dispatch = {
        "peek": cmd_peek,
        "show": cmd_show,
        "commit": cmd_commit,
        "next": cmd_next,
        "reset": cmd_reset,
    }
    return dispatch[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
