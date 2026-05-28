# sts2-verification TODO

## Tier A: passive verification — DONE

The verifier is the lighter action-replay model (see README). Implemented and verified
against a real recording on a locally-built v0.106.1 env:

- replay recorded actions through `sts2-env`; fail on the first illegal/unmappable action
- credit win from the engine victory terminal (Act-3 2nd boss); else compare final
  floor/act and fail on mismatch
- reject `debug_command` rows unless `--allow-cheats`
- worker writes structured result + advances `streak_leaderboard_entries` on a verified win

## Mutation tests — DONE

`mutations/run_mutation_tests.py`, 8 cases green against a real recording:
baseline (PASS), insert_debug_command, bad_event_index, illegal_play_card,
drop_map_select, changed_seed, changed_character, reward_before_combat_end.

Add as needed: changed reward/shop/rest index, missing required `select_card`,
skipped boss/proceed transition, changed ascension/version.

## Open

- Confirm the win path end-to-end once a winning Act-3 recording exists (the env victory
  hook is in place; no winning recording captured yet).
- Audit `replay_m1_recording.py` skip rules so no meaningful player decision can be
  skipped (the mutation suite is the regression guard).
- Decide whether long-term verification stays DB-polled (`streak_worker.py`) or becomes a
  separate HTTP service.

## Later: Tier B/C

- verify live action-commit logs
- verify RNG grant logs; replay server-owned value grants
