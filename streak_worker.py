#!/usr/bin/env python3
"""Poll and process streak verification jobs from the backend SQLite DB."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from replay_m1_recording import ReplayFailure, load_actions, load_json, replay_recording


DEFAULT_DB_PATH = "/opt/sls2-data/traces.db"
DEFAULT_POLL_INTERVAL = 5.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_STALE_MINUTES = 30


class ArtifactError(RuntimeError):
    pass


def db_connect(db_path: str) -> sqlite3.Connection:
    db = sqlite3.connect(db_path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=30000")
    return db


def reset_stale_jobs(db: sqlite3.Connection, stale_minutes: int) -> int:
    cur = db.execute(
        """UPDATE streak_validation_jobs
           SET status = 'queued',
               error = 'stale processing job reset',
               started_at = NULL
           WHERE status = 'processing'
             AND started_at < datetime('now', ?)""",
        (f"-{max(1, stale_minutes)} minutes",),
    )
    db.execute(
        """UPDATE streak_attempts
           SET status = 'queued_for_verification',
               verification_status = 'queued',
               verification_error = 'stale processing job reset'
           WHERE verification_job_id IN (
               SELECT id FROM streak_validation_jobs
               WHERE status = 'queued'
                 AND error = 'stale processing job reset'
           )""",
    )
    db.commit()
    return int(cur.rowcount or 0)


def claim_next_job(db: sqlite3.Connection) -> sqlite3.Row | None:
    db.execute("BEGIN IMMEDIATE")
    try:
        row = db.execute(
            """SELECT j.id, j.attempt_id, j.attempts,
                      a.steam_id, a.category, a.character, a.ascension,
                      a.seed, a.game_version, a.run_dir, a.reported_floor,
                      a.reported_result
               FROM streak_validation_jobs j
               JOIN streak_attempts a ON a.attempt_id = j.attempt_id
               WHERE j.status = 'queued'
                 AND a.upload_complete = 1
               ORDER BY j.created_at ASC, j.id ASC
               LIMIT 1"""
        ).fetchone()
        if row is None:
            db.commit()
            return None

        db.execute(
            """UPDATE streak_validation_jobs
               SET status = 'processing',
                   attempts = attempts + 1,
                   error = '',
                   started_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (row["id"],),
        )
        db.execute(
            """UPDATE streak_attempts
               SET status = 'verification_running',
                   verification_status = 'processing',
                   verification_error = '',
                   verification_job_id = ?
               WHERE attempt_id = ?""",
            (row["id"], row["attempt_id"]),
        )
        db.commit()
        return db.execute(
            """SELECT j.id, j.attempt_id, j.attempts,
                      a.steam_id, a.category, a.character, a.ascension,
                      a.seed, a.game_version, a.run_dir, a.reported_floor,
                      a.reported_result
               FROM streak_validation_jobs j
               JOIN streak_attempts a ON a.attempt_id = j.attempt_id
               WHERE j.id = ?""",
            (row["id"],),
        ).fetchone()
    except Exception:
        db.rollback()
        raise


def required_artifacts(recording_dir: Path) -> None:
    missing = [
        name
        for name in ("manifest.json", "actions.jsonl", "final.run.json")
        if not (recording_dir / name).is_file()
    ]
    if missing:
        raise ArtifactError(f"missing required artifact(s): {', '.join(missing)}")


def actions_total(recording_dir: Path) -> int:
    try:
        return len(load_actions(recording_dir / "actions.jsonl"))
    except Exception:
        return 0


def compact_error(exc: BaseException) -> str:
    message = str(exc).strip()
    if not message:
        message = exc.__class__.__name__
    return message[:1000]


def json_report(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def int_or_zero(value: Any) -> int:
    try:
        if value is None or value == "":
            return 0
        return int(value)
    except (TypeError, ValueError):
        return 0


def apply_leaderboard(db: sqlite3.Connection, job: sqlite3.Row, report: dict[str, Any]) -> None:
    steam_id = str(job["steam_id"])
    category = str(job["category"])
    floor = int_or_zero(report.get("verified_floor")) or int_or_zero(job["reported_floor"])
    won = bool(report.get("verified_win"))

    current = db.execute(
        """SELECT current_streak, best_streak, best_floor
           FROM streak_leaderboard_entries
           WHERE steam_id = ? AND category = ?""",
        (steam_id, category),
    ).fetchone()

    old_current = int(current["current_streak"] if current else 0)
    old_best = int(current["best_streak"] if current else 0)
    old_floor = int(current["best_floor"] if current else 0)
    new_current = old_current + 1 if won else 0
    new_best = max(old_best, new_current)
    new_floor = max(old_floor, floor)

    db.execute(
        """INSERT INTO streak_leaderboard_entries
           (steam_id, category, current_streak, best_streak, best_floor,
            last_attempt_id, verification_policy, last_verified_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 'passive_strict_v1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
           ON CONFLICT(steam_id, category) DO UPDATE SET
             current_streak = excluded.current_streak,
             best_streak = excluded.best_streak,
             best_floor = excluded.best_floor,
             last_attempt_id = excluded.last_attempt_id,
             verification_policy = excluded.verification_policy,
             last_verified_at = excluded.last_verified_at,
             updated_at = excluded.updated_at""",
        (
            steam_id,
            category,
            new_current,
            new_best,
            new_floor,
            job["attempt_id"],
        ),
    )


def finish_passed(db_path: str, job: sqlite3.Row, report: dict[str, Any]) -> None:
    matched = int_or_zero(report.get("actions_matched"))
    total = int_or_zero(report.get("actions_seen"))
    env_version = str(report.get("game_version") or job["game_version"] or "")
    verified_result = str(report.get("verified_result") or "unknown")
    verified_floor = int_or_zero(report.get("verified_floor")) or int_or_zero(job["reported_floor"])
    verified_win = 1 if report.get("verified_win") else 0

    db = db_connect(db_path)
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """UPDATE streak_validation_jobs
               SET status = 'done',
                   matched = ?,
                   total = ?,
                   env_version = ?,
                   report_json = ?,
                   error = '',
                   finished_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (matched, total, env_version, json_report(report), job["id"]),
        )
        db.execute(
            """UPDATE streak_attempts
               SET status = 'verification_passed',
                   verification_status = 'passed',
                   verification_error = '',
                   verified_result = ?,
                   verified_floor = ?,
                   verified_win = ?,
                   verified_at = CURRENT_TIMESTAMP,
                   finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP)
               WHERE attempt_id = ?""",
            (verified_result, verified_floor, verified_win, job["attempt_id"]),
        )
        apply_leaderboard(db, job, report)
        db.execute(
            """UPDATE streak_attempts
               SET leaderboard_applied = 1
               WHERE attempt_id = ?""",
            (job["attempt_id"],),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def finish_verification_failed(db_path: str, job: sqlite3.Row, exc: BaseException, total: int) -> None:
    error = compact_error(exc)
    report = {
        "ok": False,
        "attempt_id": job["attempt_id"],
        "error": error,
        "error_type": exc.__class__.__name__,
    }
    db = db_connect(db_path)
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            """UPDATE streak_validation_jobs
               SET status = 'done',
                   matched = 0,
                   total = ?,
                   env_version = ?,
                   report_json = ?,
                   error = ?,
                   finished_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (total, job["game_version"] or "", json_report(report), error, job["id"]),
        )
        db.execute(
            """UPDATE streak_attempts
               SET status = 'verification_failed',
                   verification_status = 'failed',
                   verification_error = ?,
                   verified_result = '',
                   verified_floor = NULL,
                   verified_win = NULL,
                   verified_at = CURRENT_TIMESTAMP,
                   leaderboard_applied = 0,
                   finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP)
               WHERE attempt_id = ?""",
            (error, job["attempt_id"]),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def finish_worker_error(db_path: str, job: sqlite3.Row, exc: BaseException, max_attempts: int) -> None:
    error = compact_error(exc)
    attempts = int(job["attempts"] or 0)
    retry = attempts < max_attempts
    report = {
        "ok": False,
        "attempt_id": job["attempt_id"],
        "error": error,
        "error_type": exc.__class__.__name__,
        "retry": retry,
        "traceback": traceback.format_exc(limit=4),
    }
    db = db_connect(db_path)
    try:
        db.execute("BEGIN IMMEDIATE")
        if retry:
            db.execute(
                """UPDATE streak_validation_jobs
                   SET status = 'queued',
                       report_json = ?,
                       error = ?,
                       started_at = NULL
                   WHERE id = ?""",
                (json_report(report), error, job["id"]),
            )
            db.execute(
                """UPDATE streak_attempts
                   SET status = 'queued_for_verification',
                       verification_status = 'queued',
                       verification_error = ?
                   WHERE attempt_id = ?""",
                (error, job["attempt_id"]),
            )
        else:
            db.execute(
                """UPDATE streak_validation_jobs
                   SET status = 'failed',
                       report_json = ?,
                       error = ?,
                       finished_at = CURRENT_TIMESTAMP
                   WHERE id = ?""",
                (json_report(report), error, job["id"]),
            )
            db.execute(
                """UPDATE streak_attempts
                   SET status = 'verification_error',
                       verification_status = 'error',
                       verification_error = ?,
                       finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP)
                   WHERE attempt_id = ?""",
                (error, job["attempt_id"]),
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def process_job(
    db_path: str,
    job: sqlite3.Row,
    *,
    host: str,
    port: int,
    timeout: float,
    max_attempts: int,
    verbose: bool,
) -> bool:
    recording_dir = Path(str(job["run_dir"]))
    total = actions_total(recording_dir)
    try:
        required_artifacts(recording_dir)
        manifest = load_json(recording_dir / "manifest.json")
        if manifest.get("seed") and manifest.get("seed") != job["seed"]:
            raise ArtifactError(f"seed mismatch: manifest={manifest.get('seed')} db={job['seed']}")
        if manifest.get("character") and manifest.get("character") != job["character"]:
            raise ArtifactError(
                f"character mismatch: manifest={manifest.get('character')} db={job['character']}"
            )
        report = replay_recording(
            recording_dir,
            host=host,
            port=port,
            verbose=verbose,
            timeout=timeout,
        )
        finish_passed(db_path, job, report)
        print(
            f"[streak job {job['id']}] passed {job['attempt_id']} "
            f"{report.get('verified_result')} actions={report.get('actions_seen')}",
            flush=True,
        )
        return True
    except (ArtifactError, ReplayFailure) as exc:
        finish_verification_failed(db_path, job, exc, total)
        print(f"[streak job {job['id']}] verification failed: {compact_error(exc)}", flush=True)
        return True
    except Exception as exc:
        finish_worker_error(db_path, job, exc, max_attempts)
        print(f"[streak job {job['id']}] worker error: {compact_error(exc)}", flush=True)
        return False


def run_worker(args: argparse.Namespace) -> int:
    db_path = args.db_path
    processed = 0
    while True:
        db = db_connect(db_path)
        try:
            reset_count = reset_stale_jobs(db, args.stale_minutes)
            if reset_count:
                print(f"reset stale streak jobs: {reset_count}", flush=True)
            job = claim_next_job(db)
        finally:
            db.close()

        if job is None:
            if args.once or (args.limit is not None and processed >= args.limit):
                return 0
            time.sleep(args.poll_interval)
            continue

        processed += 1
        process_job(
            db_path,
            job,
            host=args.host,
            port=args.port,
            timeout=args.timeout,
            max_attempts=args.max_attempts,
            verbose=args.verbose,
        )

        if args.once or (args.limit is not None and processed >= args.limit):
            return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", default=os.environ.get("DB_PATH", DEFAULT_DB_PATH))
    parser.add_argument("--host", default=os.environ.get("STS2_ENV_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("STS2_ENV_PORT", "9876")))
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("STS2_ENV_TIMEOUT", "300")))
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL)
    parser.add_argument("--max-attempts", type=int, default=DEFAULT_MAX_ATTEMPTS)
    parser.add_argument("--stale-minutes", type=int, default=DEFAULT_STALE_MINUTES)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if not Path(args.db_path).exists():
        print(f"db does not exist: {args.db_path}", file=sys.stderr)
        return 2
    return run_worker(args)


if __name__ == "__main__":
    raise SystemExit(main())
