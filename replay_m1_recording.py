#!/usr/bin/env python3
"""Replay a sts2-ladder M1 actions.jsonl recording against headless EnvServer.

This is an early validation adapter for the new local recording format. It
executes recorded surface actions by index/position and fails fast when the
headless env rejects a choice.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "sts2-ai"))

from env.client import STS2Env  # noqa: E402


class ReplayFailure(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def load_actions(path: Path) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line:
                actions.append(json.loads(line))
    return actions


def acts_from_first_snapshot(recording_dir: Path) -> list[dict[str, Any]] | None:
    snapshots = sorted((recording_dir / "snapshots").glob("*.save.json"))
    if not snapshots:
        return None
    root = load_json(snapshots[0])
    acts = root.get("acts")
    if not isinstance(acts, list):
        return None
    serialized_acts = [a for a in acts if isinstance(a, dict) and a.get("id")]
    return serialized_acts or None


def unlocked_epochs_from_first_snapshot(recording_dir: Path) -> list[str] | None:
    snapshots = sorted((recording_dir / "snapshots").glob("*.save.json"))
    if not snapshots:
        return None
    root = load_json(snapshots[0])
    players = root.get("players")
    if not isinstance(players, list) or not players or not isinstance(players[0], dict):
        return None
    unlock_state = players[0].get("unlock_state")
    if not isinstance(unlock_state, dict):
        return None
    epochs = unlock_state.get("unlocked_epochs")
    if not isinstance(epochs, list):
        return None
    values = [e for e in epochs if isinstance(e, str)]
    return values or None


def reward_type_name(reward: dict[str, Any] | None) -> str | None:
    if not isinstance(reward, dict):
        return None
    name = reward.get("reward_type")
    if not isinstance(name, str):
        return None
    return name if name.endswith("Reward") else f"{name}Reward"


def expected_result_from_final_run(final_run: dict[str, Any] | None) -> str | None:
    if not isinstance(final_run, dict):
        return None
    if final_run.get("was_abandoned") is True:
        return "abandoned"
    if final_run.get("win") is True:
        return "won"
    if final_run.get("win") is False:
        return "died"
    return None


def result_from_phase(phase: dict[str, Any]) -> str | None:
    if phase.get("phase") != "game_over":
        return None
    for action in phase.get("available_actions", []) or []:
        if isinstance(action, dict) and isinstance(action.get("result"), str):
            return action["result"]
    return "game_over"


def reward_action_matches(action: dict[str, Any], reward: dict[str, Any], wanted_type: str) -> bool:
    if action.get("reward_type") != wanted_type:
        return False

    if wanted_type == "PotionReward":
        potion = reward.get("potion") if isinstance(reward.get("potion"), dict) else {}
        potion_id = potion.get("id")
        return not potion_id or action.get("potion_id") == potion_id

    if wanted_type == "CardReward":
        recorded_cards = [
            c.get("id")
            for c in reward.get("cards", []) or []
            if isinstance(c, dict) and isinstance(c.get("id"), str)
        ]
        live_cards = [
            c.get("id")
            for c in action.get("cards", []) or []
            if isinstance(c, dict) and isinstance(c.get("id"), str)
        ]
        return not recorded_cards or not live_cards or recorded_cards == live_cards

    return True


class M1Replayer:
    def __init__(self, env: STS2Env, verbose: bool = False):
        self.env = env
        self.verbose = verbose
        self.pending_input: str | None = None
        self.executed = 0
        self.skipped = 0

    def send(self, msg: dict[str, Any], seq: int | None = None) -> dict[str, Any]:
        if self.verbose:
            prefix = f"seq={seq} " if seq is not None else ""
            print(f"{prefix}-> {msg}", flush=True)
        resp = self.env._send_raw(msg)
        if self.verbose:
            summary = {k: resp.get(k) for k in ("type", "phase", "needs_input", "done", "won", "error") if k in resp}
            print(f"   <- {summary}", flush=True)
        if "error" in resp:
            where = f" at seq {seq}" if seq is not None else ""
            raise ReplayFailure(f"server error{where}: {resp['error']} for {msg}")
        self.pending_input = resp.get("needs_input")
        self.executed += 1
        return resp

    def get_phase(self) -> dict[str, Any]:
        resp = self.send({"type": "get_phase"})
        self.executed -= 1
        return resp

    def skip(self) -> None:
        self.skipped += 1

    @staticmethod
    def is_metadata_row(surface: Any, action: Any) -> bool:
        if surface in {"snapshot"}:
            return True
        if surface == "run" and action in {"start", "history_written", "complete", "proceed_from_terminal_rewards"}:
            return True
        if surface == "combat" and action in {"start", "end"}:
            return True
        if surface == "rewards" and action in {"show", "closed"}:
            return True
        if surface == "shop" and action in {"open", "purchase_completed"}:
            return True
        return False

    def start_run(self, recording_dir: Path, manifest: dict[str, Any]) -> None:
        cmd: dict[str, Any] = {
            "type": "start_run",
            "character": manifest["character"],
            "seed": manifest["seed"],
            "ascension": int(manifest.get("ascension", 0)),
        }
        acts = acts_from_first_snapshot(recording_dir)
        if acts:
            cmd["acts"] = acts
        unlocked_epochs = unlocked_epochs_from_first_snapshot(recording_dir)
        if unlocked_epochs:
            cmd["unlockedEpochs"] = unlocked_epochs
        self.send(cmd)

    def available_actions(self, phase: dict[str, Any], action_type: str) -> list[dict[str, Any]]:
        return [
            a for a in phase.get("available_actions", []) or []
            if isinstance(a, dict) and a.get("type") == action_type
        ]

    def resolve_reward_index(self, recorded_index: int, recorded_reward: dict[str, Any] | None) -> int:
        phase = self.get_phase()
        actions = self.available_actions(phase, "reward_click")

        wanted_type = reward_type_name(recorded_reward)
        if wanted_type and isinstance(recorded_reward, dict):
            matches = [
                a for a in actions
                if reward_action_matches(a, recorded_reward, wanted_type)
            ]
            if matches:
                return int(matches[0].get("index", 0))

            typed_matches = [a for a in actions if a.get("reward_type") == wanted_type]
            if typed_matches:
                return int(typed_matches[0].get("index", 0))

        indexed_matches = [a for a in actions if a.get("index") == recorded_index]
        if indexed_matches:
            return int(indexed_matches[0].get("index", 0))

        if len(actions) == 1:
            return int(actions[0].get("index", 0))
        raise ReplayFailure(
            f"cannot resolve reward index {recorded_index}; "
            f"wanted={wanted_type}, available={actions}"
        )

    def proceed_rewards(self, seq: int) -> None:
        phase = self.get_phase()
        actions = phase.get("available_actions", []) or []
        if any(a.get("type") == "proceed" for a in actions if isinstance(a, dict)):
            self.send({"type": "proceed"}, seq)
        elif any(a.get("type") == "leave_rewards" for a in actions if isinstance(a, dict)):
            self.send({"type": "leave_rewards"}, seq)
        elif phase.get("phase") == "map":
            self.skip()
        else:
            self.send({"type": "leave_rewards"}, seq)

    def leave_shop_if_needed(self) -> None:
        phase = self.get_phase()
        if phase.get("phase") == "shop":
            self.send({"type": "leave_room"})

    def replay_action(self, entry: dict[str, Any]) -> None:
        seq = int(entry.get("seq", 0))
        surface = entry.get("surface")
        action = entry.get("action")
        data = entry.get("data") if isinstance(entry.get("data"), dict) else {}

        if self.is_metadata_row(surface, action):
            self.skip()
            return

        if self.pending_input:
            if self.pending_input == "collect_reward" and surface == "rewards" and action in {
                "click_reward",
                "proceed",
            }:
                pass
            elif self.pending_input == "collect_reward" and surface == "selection" and action == "sync_local_choice":
                self.skip()
                return
            elif self.pending_input == "select_card" and surface in {"selection", "card_reward"} and action in {
                "sync_local_choice",
                "grid_cards_selected",
                "select_card",
            }:
                selected = data.get("selected_indices")
                if selected is None and "selected_index" in data:
                    selected = [data["selected_index"]]
                if selected is None:
                    selected = []
                self.send({"type": "select_card", "selected_indices": selected}, seq)
                return
            else:
                raise ReplayFailure(f"pending {self.pending_input} before seq {seq}, got {surface}/{action}")

        if surface == "selection" and action == "sync_local_choice":
            self.skip()
            return

        if surface == "map" and action == "select_node":
            coord = data.get("selected_coord") or {}
            phase = self.get_phase()
            if phase.get("phase") == "event" and coord.get("row") == 0:
                self.skip()
                return
            if phase.get("phase") == "shop":
                self.send({"type": "leave_room"}, seq)
            self.send({"type": "select_map_node", "col": int(coord["col"]), "row": int(coord["row"])}, seq)
            return

        if surface == "event" and action == "choose_option":
            self.send({"type": "choose_option", "option_index": int(data["selected_index"])}, seq)
            return

        if surface == "combat" and action == "play_card":
            card = data.get("card") or {}
            msg: dict[str, Any] = {
                "type": "play_card",
                "card_id": card.get("id"),
                "hand_index": int(data["hand_index"]),
            }
            if data.get("target_combat_id") is not None:
                msg["target_id"] = int(data["target_combat_id"])
            self.send(msg, seq)
            return

        if surface == "combat" and action == "end_turn":
            self.send({"type": "end_turn"}, seq)
            return

        if surface == "rewards" and action == "click_reward":
            reward = data.get("reward") if isinstance(data.get("reward"), dict) else None
            index = self.resolve_reward_index(int(data["index"]), reward)
            self.send({"type": "reward_click", "index": index}, seq)
            return

        if surface == "card_reward" and action == "select_card":
            self.skip()
            return

        if surface == "rewards" and action == "proceed":
            self.proceed_rewards(seq)
            return

        if surface == "shop" and action == "purchase_attempt":
            section = data.get("section")
            index = data.get("selected_section_index", data.get("section_index", data.get("selected_index", 0)))
            self.send({"type": "shop_click", "section": section, "index": int(index)}, seq)
            return

        if surface == "rest" and action == "choose_option":
            self.send({"type": "choose_rest_option", "option_index": int(data["selected_index"])}, seq)
            return

        if surface == "treasure" and action == "take_relic":
            self.send({"type": "take_relic", "index": int(data.get("selected_index", data.get("index", 0)))}, seq)
            return

        if surface == "treasure" and action == "open_chest":
            self.send({"type": "open_chest"}, seq)
            return

        if surface == "selection" and action == "grid_cards_selected":
            # Non-pending duplicate grid notification.
            self.skip()
            return

        if surface == "potion" and action == "use_potion":
            potion = data.get("potion") or {}
            msg = {"type": "use_potion", "potion_id": potion.get("id"), "potion_slot_index": data.get("slot")}
            if data.get("target_combat_id") is not None:
                msg["target_id"] = int(data["target_combat_id"])
            self.send(msg, seq)
            return

        if surface == "potion" and action == "discard_potion":
            potion = data.get("potion") or {}
            self.send({"type": "discard_potion", "potion_id": potion.get("id"), "potion_slot_index": data.get("slot")}, seq)
            return

        raise ReplayFailure(f"unhandled action seq {seq}: {surface}/{action}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("recording_dir", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9876)
    parser.add_argument("--max-seq", type=int)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    recording_dir = args.recording_dir.expanduser().resolve()
    manifest = load_json(recording_dir / "manifest.json")
    final_run_path = recording_dir / "final.run.json"
    final_run = load_json(final_run_path) if final_run_path.exists() else None
    actions = load_actions(recording_dir / "actions.jsonl")
    if args.max_seq is not None:
        actions = [a for a in actions if int(a.get("seq", 0)) <= args.max_seq]

    env = STS2Env(args.host, args.port)
    env.connect()
    try:
        replay = M1Replayer(env, verbose=args.verbose)
        replay.start_run(recording_dir, manifest)
        for entry in actions:
            replay.replay_action(entry)
        final_phase = replay.get_phase()
        expected_result = expected_result_from_final_run(final_run)
        actual_result = result_from_phase(final_phase)
        if expected_result and expected_result != "abandoned" and actual_result != expected_result:
            raise ReplayFailure(
                f"final result mismatch: expected {expected_result}, "
                f"got {actual_result or final_phase.get('phase')}"
            )
        print(json.dumps({
            "ok": True,
            "run_id": manifest.get("run_id"),
            "actions_seen": len(actions),
            "commands_sent": replay.executed,
            "skipped": replay.skipped,
            "expected_result": expected_result,
            "actual_result": actual_result,
            "final_phase": final_phase.get("phase"),
        }))
    except Exception as exc:
        print(json.dumps({
            "ok": False,
            "error": str(exc),
        }))
        return 1
    finally:
        env.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
