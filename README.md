# sts2-verification

New home for streak-ladder verification and anti-cheat work.

## Purpose

This workspace should own the verification side of official attempts:

- strict full-run replay orchestration
- attempt artifact validation
- mutation tests for known bad traces
- verification workers / service boundaries
- result policy helpers for streak and WR leaderboards
- future live action-commit and server-RNG experiments

Use `sts2-env` for the actual game replay engine. This project should coordinate
verification; it should not reimplement STS2 rules.

## Boundary

Do:

- call `sts2-env` / `RunValidator`
- store clear pass/fail/error details
- keep reported result separate from verified result
- build mutation tests before making strong anti-cheat claims
- copy useful validator code from `sts2-ai` only after choosing structure

Do not:

- add new verification code to `sts2-ai`
- make Python infer rewards, combat wins, or floor transitions
- accept card/reward/event choices by name when indices exist
- silently repair missing recorder data

## First Milestone

Tier A passive verified streak:

1. Given an uploaded attempt directory, run strict replay.
2. Return structured verification status.
3. Fail known mutations for the right reasons.
4. Emit a leaderboard-usable result only after replay passes.

## Inputs

Relevant current docs:

- `../LADDER.md`
- `../PROTOCOL.md`
- `../RNG.md`
- `../sts2-env/AGENT.md`
- `../sts2-env/ACTION_MODEL.md`
- `../sts2-env/HEADLESS_ARCHITECTURE.md`
