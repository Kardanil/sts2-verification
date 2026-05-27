# sts2-verification TODO

## Current Goal

New home for streak verification and anti-cheat orchestration.

## Tier A: Passive Verification

- define attempt artifact input shape
- call `sts2-env` / `RunValidator` for strict replay
- return structured result:
  - status
  - matched / total actions
  - first divergence
  - state mismatch details
  - strict cheat rejection reason
- normalize verified result for backend leaderboard policy

## Mutation Tests

- missing required `select_card`
- changed reward/event/shop/rest index
- inserted `debug_command`
- removed `debug_command`
- reward action before combat really ended
- skipped boss/proceed transition
- changed seed, character, ascension, version, or unlock state

## Service Boundary

- decide whether verification is:
  - a backend-polled Python worker
  - a separate HTTP service
  - a CLI first, service later
- avoid importing broad `sts2-ai` runtime until structure is chosen

## Later: Tier B/C

- verify live action commit logs
- verify RNG grant logs
- add replay support for server-owned value grants
