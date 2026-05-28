# sts2-verification TODO

## Current Goal

New home for streak verification and anti-cheat orchestration.

## Tier A: Passive Verification

- define public attempt artifact input shape
- call `sts2-env` for strict replay - deployed for M1
- return structured result - deployed for M1:
  - status
  - matched / total actions
  - first divergence
  - state mismatch details
  - strict cheat rejection reason
- normalize verified result for backend leaderboard policy - first-pass deployed

## Mutation Tests

- missing required `select_card`
- changed reward/event/shop/rest index
- inserted `debug_command`
- removed `debug_command`
- reward action before combat really ended
- skipped boss/proceed transition
- changed seed, character, ascension, version, or unlock state

## Service Boundary

- DB-polled Python worker exists for M1 (`streak_worker.py`)
- decide whether long-term verification remains DB-polled or becomes a
  separate HTTP service
- keep `sts2-verification` independent of broad `sts2-ai` runtime

## Later: Tier B/C

- verify live action commit logs
- verify RNG grant logs
- add replay support for server-owned value grants
