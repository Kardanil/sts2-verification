#!/usr/bin/env python3
"""Mutation tests for the M1 streak verifier.

Takes a known-good recording (one that replays clean), applies a single
adversarial mutation, and asserts the verifier REJECTS it. Passing a real run
only proves the happy path; these prove the verifier actually catches cheating
and tampering — the evidence behind any "verified" claim.

Each case: copy the base recording, apply one mutation, replay through the
headless env, and check the outcome matches expectation (base must PASS, every
mutation must FAIL). A mutation that unexpectedly PASSES is a strictness gap to
fix, and is reported as such.

Usage:
  python3 mutations/run_mutation_tests.py <base_recording_dir> [--host H --port P]
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from replay_m1_recording import load_actions, load_json, replay_recording  # noqa: E402


# ── mutation helpers ──

def _find(actions: list[dict[str, Any]], surface: str, action: str, nth: int = 0) -> int:
    seen = 0
    for i, e in enumerate(actions):
        if e.get("surface") == surface and e.get("action") == action:
            if seen == nth:
                return i
            seen += 1
    raise AssertionError(f"base recording has no {surface}/{action} row #{nth} to mutate")


def m_insert_debug_command(manifest, actions, final_run):
    """Insert a ~console cheat row. Must be rejected outright."""
    idx = _find(actions, "event", "choose_option")
    row = {
        "type": "action", "seq": 0, "surface": "debug",
        "action": "debug_command", "data": {"command": "godmode"},
    }
    actions = actions[:idx] + [row] + actions[idx:]
    return manifest, actions, final_run


def m_bad_event_index(manifest, actions, final_run):
    """Choose an out-of-range event option. Engine must reject the index."""
    idx = _find(actions, "event", "choose_option")
    actions[idx]["data"]["selected_index"] = 99
    return manifest, actions, final_run


def m_illegal_play_card(manifest, actions, final_run):
    """Play a card from a non-existent hand index. Engine must reject it."""
    idx = _find(actions, "combat", "play_card")
    actions[idx]["data"]["hand_index"] = 99
    return manifest, actions, final_run


def m_drop_map_select(manifest, actions, final_run):
    """Remove a load-bearing (mid-run) map-node selection. The next recorded
    action then targets a non-reachable node or an inactive combat, so the engine
    must reject it. (The very first select is the auto-entered start node and is a
    no-op, so we drop the second.)"""
    idx = _find(actions, "map", "select_node", nth=1)
    del actions[idx]
    return manifest, actions, final_run


def m_changed_seed(manifest, actions, final_run):
    """Run a different seed than recorded. The run cannot reproduce."""
    manifest = dict(manifest)
    manifest["seed"] = "ZZZZZZZZZZ"
    return manifest, actions, final_run


def m_changed_character(manifest, actions, final_run):
    """Claim a different character than recorded. Deck/cards won't match."""
    manifest = dict(manifest)
    manifest["character"] = "Silent" if manifest.get("character") != "Silent" else "Ironclad"
    return manifest, actions, final_run


def m_reward_before_combat_end(manifest, actions, final_run):
    """Inject a reward click while still in the first combat. No reward surface
    exists yet, so the engine must reject it."""
    cidx = _find(actions, "combat", "play_card")
    row = {
        "type": "action", "seq": 0, "surface": "rewards",
        "action": "click_reward", "data": {"index": 0},
    }
    actions = actions[:cidx + 1] + [row] + actions[cidx + 1:]
    return manifest, actions, final_run


MUTATIONS: list[tuple[str, Callable, bool]] = [
    ("baseline (no mutation)", lambda m, a, f: (m, a, f), False),  # must PASS
    ("insert_debug_command", m_insert_debug_command, True),
    ("bad_event_index", m_bad_event_index, True),
    ("illegal_play_card", m_illegal_play_card, True),
    ("drop_map_select", m_drop_map_select, True),
    ("changed_seed", m_changed_seed, True),
    ("changed_character", m_changed_character, True),
    ("reward_before_combat_end", m_reward_before_combat_end, True),
]


def write_recording(dst: Path, base: Path, manifest, actions, final_run) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with (dst / "actions.jsonl").open("w", encoding="utf-8") as f:
        for e in actions:
            f.write(json.dumps(e) + "\n")
    (dst / "final.run.json").write_text(json.dumps(final_run), encoding="utf-8")
    src_snaps = base / "snapshots"
    if src_snaps.is_dir():
        shutil.copytree(src_snaps, dst / "snapshots")


def run_case(rec_dir: Path, host: str, port: int) -> tuple[bool, str]:
    try:
        report = replay_recording(rec_dir, host=host, port=port)
        return True, json.dumps({k: report.get(k) for k in ("verified_result", "verified_floor")})
    except Exception as exc:
        return False, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("base_recording_dir", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9876)
    args = parser.parse_args()

    base = args.base_recording_dir.expanduser().resolve()
    manifest0 = load_json(base / "manifest.json")
    actions0 = load_actions(base / "actions.jsonl")
    final0 = load_json(base / "final.run.json") if (base / "final.run.json").exists() else None

    print(f"base: {base}")
    print(f"{'case':<28} {'expect':<7} {'got':<7} {'result'}")
    print("-" * 88)

    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        for name, fn, expect_fail in MUTATIONS:
            m, a, f = fn(copy.deepcopy(manifest0), copy.deepcopy(actions0), copy.deepcopy(final0))
            rec_dir = Path(tmp) / name.replace(" ", "_").replace("(", "").replace(")", "")
            write_recording(rec_dir, base, m, a, f)
            ok, detail = run_case(rec_dir, args.host, args.port)
            got_fail = not ok
            as_expected = got_fail == expect_fail
            if not as_expected:
                failures += 1
            exp = "REJECT" if expect_fail else "PASS"
            got = "REJECT" if got_fail else "PASS"
            flag = "OK " if as_expected else ">>> GAP"
            print(f"{name:<28} {exp:<7} {got:<7} {flag}  {detail[:60]}")

    print("-" * 88)
    if failures:
        print(f"FAILED: {failures} case(s) did not behave as expected")
        return 1
    print("All mutation cases behaved as expected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
