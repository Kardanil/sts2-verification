# sts2-verification

Verification and anti-cheat for the streak / WR ladder. This workspace coordinates
replay verification; it does not reimplement STS2 rules — `sts2-env` runs the real game.

## Verification model (Tier A)

The model is deliberately light: **replay the player's recorded actions through the
real, deterministic engine.** The engine enforces legality move-by-move — you cannot
play a card you do not hold, pick a reward index that does not exist, or proceed past a
boss you did not kill. So a clean replay of the recorded actions *is* the proof.

- **Win** is credited only when the replay reaches the engine's victory terminal
  (Act-3 second boss killed; `OnEnded(isVictory:true)`). The scripted Architect-room
  death afterward is expected and is not a loss.
- **Non-win**: the honest-result proof is that the replay reaches the **same floor/act**
  the recording reports. A mismatch is a hard failure to investigate (cheat or replay
  divergence), never silently accepted.
- **Any illegal / unmappable action** mid-replay is a hard failure.
- **`debug_command` rows** (in-game `~` console use) are rejected unless `--allow-cheats`.

There is no per-boundary HP/deck/relic state comparison — it is heavy and largely
redundant given the deterministic engine. A state cheat that mattered either makes a
later recorded action illegal or changes the win/floor outcome, both of which fail.

What this proves: *"these actions legally win this seed."* It does **not** stop a player
replaying offline to find a winning line (the preview/fork problem) — that is Tier B's
job. Market Tier A honestly.

## Components

- `replay_m1_recording.py` — replays an M1 recording (`manifest.json` + `actions.jsonl`
  + `final.run.json` + `snapshots/`) against `sts2-env`, applies the model above, and
  returns a structured result (`verified_result`, `verified_win`, `verified_floor`).
- `env_client.py` — minimal TCP client for the headless env.
- `streak_worker.py` — polls `streak_validation_jobs` from the backend SQLite DB, claims
  queued jobs, replays the uploaded recording, and writes results back to
  `streak_validation_jobs`, `streak_attempts`, and `streak_leaderboard_entries`.
- `mutations/run_mutation_tests.py` — adversarial suite: mutate one thing in a known-good
  recording and assert the verifier rejects it. This is the evidence behind any
  "verified" claim — passing a real run only proves the happy path.

## Boundary

Do: call `sts2-env`; keep reported result separate from verified result; reject illegal
actions and console commands; keep mutation tests current.

Do not: reimplement STS2 rules in Python; infer wins/floors instead of replaying; accept
choices by name when indices exist; silently repair missing recorder data.

## Running

Replay one recording (needs a headless env on the run's game version):

```bash
python3 replay_m1_recording.py <recording_dir> --host 127.0.0.1 --port 9876
```

Mutation suite against a clean base recording:

```bash
python3 mutations/run_mutation_tests.py <recording_dir> --host 127.0.0.1 --port 9876
```

Worker (one-shot smoke):

```bash
python3 streak_worker.py --db-path /opt/sls2-data/traces.db --host 127.0.0.1 --port 9942 --once
```

## Local dev env

`sts2-env` builds and runs locally against the installed game:

```bash
cd ../sts2-env/headless-sim
dotnet build headless-sim.csproj -v minimal
dotnet bin/Debug/net10.0/headless-sim.dll --server --port 9876
```

## Inputs

- `../LADDER.md`, `../PROTOCOL.md`, `../RNG.md`
- `../sts2-env/AGENT.md`, `../sts2-env/ACTION_MODEL.md`, `../sts2-env/HEADLESS_ARCHITECTURE.md`
