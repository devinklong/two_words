"""
scripts/rebuild_lock_pipeline.py

Rebuilds the ENTIRE lock/hold pipeline in the one true dependency
order, confirmed live 8/19/26 by Postgres's own CASCADE notice when
rebuilding game_fantasy_scores: "drop cascades to 6 other objects" --
game_fantasy_scores_weekly, player_season_fantasy_stats, player_tiers,
game_fantasy_scores_weekly_percentage_to_lock, ownable_player_pool,
AND game_lock_signal. That's proof this is ONE connected tree rooted
at game_fantasy_scores, not two independent chains.

REPLACES rebuild_percentage_to_lock_chain.py and
rebuild_game_fantasy_scores_chain.py -- delete both, they modeled the
dependency graph incorrectly as two separate pieces, which is why
running them independently (in either order) kept failing: each one
only knew about half the tree.

Full order:
  game_fantasy_scores_view.sql
  player_season_fantasy_stats_view.sql
  player_tiers.sql
  game_fantasy_scores_weekly_view.sql
  game_fantasy_scores_weekly_context_view.sql
  sync_game_fantasy_scores_weekly_effective.sql
  percentage_to_lock.sql
  fit_hold_value_curve_by_tier.py
  ownable_player_pool.sql
  lock_bar_function.sql
  game_lock_signal.sql

Each step is verified by checking directly whether the object it
produces exists -- not by relying on psql's exit code (this project's
SQL files intentionally bundle core DDL with trailing human-review
verification queries that aren't meant to be fatal gates).

CORRECTED 8/19/26 (fifth pass): psql output is now captured to
scripts/logs/<filename>.log instead of streamed to the terminal.
Several files' trailing verification queries have no LIMIT and can
print thousands of rows -- e.g. game_fantasy_scores_weekly_view.sql's
orphan check legitimately lists every in-progress-season game not yet
covered by fantasy_weeks, which is real and expected (not a bug) but
floods the terminal when run as part of an automated rebuild. Nothing
is lost -- full output is still saved per step -- it's just not
printed by default.

The FINAL check is a real regression test against the exact numbers
confirmed correct on 8/16/26 (patch #1) and re-confirmed by hand
8/19/26: LOCK/HOLD/PASS split and Nikola Jokić's lock_bar specifically
on 2024-11-22 (season 22024, lock_bar=79.490 -- Jokić has a DIFFERENT
lock_bar per season since it's derived from that season's avg/stddev,
so the check pins to a specific game_date, not just player_id, or it
can silently grab a different season's correct-but-different value and
falsely report a mismatch). A script that only checks object existence
can pass while the underlying numbers are silently wrong -- this one
can't.

Run from the project root:
    python scripts/rebuild_lock_pipeline.py
"""

import subprocess
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from db_connection import get_connection

PGCONN = "postgres"

EXPECTED_SPLIT = {"HOLD": 52.7, "LOCK": 24.2, "PASS": 23.0}
EXPECTED_JOKIC_LOCK_BAR = 79.490
JOKIC_PLAYER_ID = 203999

LOG_DIR = Path(__file__).resolve().parent / "logs"


def run_psql_quiet(sql_file):
    """Runs a .sql file via psql, capturing all output to a log file
    instead of streaming it to the terminal. Some of these files'
    trailing verification queries have no LIMIT and can print thousands
    of rows (e.g. game_fantasy_scores_weekly_view.sql's orphan check,
    which legitimately lists every in-progress-season game not yet
    covered by fantasy_weeks -- real, expected, not a bug, just noisy).
    Full output is still saved to logs/<filename>.log if you need to
    inspect it -- nothing is lost, it's just not dumped to the screen."""
    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / f"{Path(sql_file).name}.log"
    result = subprocess.run(["psql", PGCONN, "-f", sql_file], capture_output=True, text=True)
    log_path.write_text(result.stdout + result.stderr)
    return log_path


def object_exists(cur, name, kind):
    if kind == "table":
        cur.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=%s",
            (name,),
        )
    elif kind == "view":
        cur.execute(
            "SELECT 1 FROM information_schema.views WHERE table_schema='public' AND table_name=%s",
            (name,),
        )
    else:  # function
        cur.execute(
            "SELECT 1 FROM pg_proc WHERE pronamespace = 'public'::regnamespace AND proname=%s",
            (name,),
        )
    return cur.fetchone() is not None


def run_step(sql_file, expect_name, expect_kind):
    print(f"Running {sql_file}...")
    log_path = run_psql_quiet(sql_file)

    conn = get_connection()
    cur = conn.cursor()
    ok = object_exists(cur, expect_name, expect_kind)
    cur.close()
    conn.close()

    if not ok:
        print(f"STOP: {expect_name} ({expect_kind}) does not exist after running {sql_file}.")
        print(f"  full output: {log_path}")
        sys.exit(1)
    print(f"  confirmed: {expect_name} exists. (full output: {log_path})")


def run_python_script(script_path):
    print(f"Running {script_path}...")
    subprocess.run([sys.executable, script_path], check=True)


def verify_params_populated():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM hold_value_curve_params_by_tier")
    populated = cur.fetchone()[0] > 0
    cur.close()
    conn.close()

    if not populated:
        print("FAIL: hold_value_curve_params_by_tier exists but is EMPTY -- "
              "fit_hold_value_curve_by_tier.py did not populate it.")
        return False
    print("PASS: hold_value_curve_params_by_tier is populated.")
    return True


def verify_effective_table_caught_up():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM game_fantasy_scores_weekly_full")
    full_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM game_fantasy_scores_weekly_effective")
    effective_count = cur.fetchone()[0]
    cur.close()
    conn.close()

    gap = full_count - effective_count
    print(f"full view count: {full_count} | effective table count: {effective_count} | gap: {gap}")
    if gap != 0:
        print("FAIL: game_fantasy_scores_weekly_effective has not caught up.")
        return False
    print("PASS: effective table fully caught up.")
    return True


def verify_against_known_good():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT lock_signal, ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
        FROM game_lock_signal
        GROUP BY lock_signal
    """)
    actual_split = {row[0]: float(row[1]) for row in cur.fetchall()}

    cur.execute("""
        SELECT lock_bar FROM game_lock_signal
        WHERE player_id = %s AND game_date = '2024-11-22'
        LIMIT 1
    """, (JOKIC_PLAYER_ID,))
    row = cur.fetchone()
    actual_jokic_lock_bar = float(row[0]) if row else None

    cur.close()
    conn.close()

    print(f"\nLOCK/HOLD/PASS split -- expected {EXPECTED_SPLIT}, got {actual_split}")
    print(f"Jokić lock_bar -- expected {EXPECTED_JOKIC_LOCK_BAR}, got {actual_jokic_lock_bar}")

    split_ok = all(
        key in actual_split and abs(actual_split[key] - expected) < 0.1
        for key, expected in EXPECTED_SPLIT.items()
    )
    jokic_ok = actual_jokic_lock_bar is not None and abs(actual_jokic_lock_bar - EXPECTED_JOKIC_LOCK_BAR) < 0.001

    if not split_ok:
        print("FAIL: LOCK/HOLD/PASS split does not match the known-good values.")
    if not jokic_ok:
        print("FAIL: Jokić lock_bar does not match the known-good value.")

    return split_ok and jokic_ok


def main():
    # --- game_fantasy_scores tree ---
    run_step("schema/views/game_fantasy_scores_view.sql", "game_fantasy_scores", "view")
    run_step("schema/views/player_season_fantasy_stats_view.sql", "player_season_fantasy_stats", "view")
    run_step("models/player_tiers.sql", "player_tiers", "view")
    run_step("schema/views/game_fantasy_scores_weekly_view.sql", "game_fantasy_scores_weekly", "view")
    run_step("schema/views/game_fantasy_scores_weekly_context_view.sql", "game_fantasy_scores_weekly_full", "view")
    print("Running schema/tables/sync_game_fantasy_scores_weekly_effective.sql...")
    log_path = run_psql_quiet("schema/tables/sync_game_fantasy_scores_weekly_effective.sql")
    print(f"  done. (full output: {log_path})")

    if not verify_effective_table_caught_up():
        sys.exit(1)

    # --- percentage_to_lock / lock_signal tree ---
    run_step("models/percentage_to_lock.sql", "hold_value_curve_params_by_tier", "table")
    run_python_script("scripts/fit_hold_value_curve_by_tier.py")

    if not verify_params_populated():
        sys.exit(1)

    run_step("models/ownable_player_pool.sql", "ownable_player_pool", "view")
    run_step("models/lock_bar_function.sql", "lock_bar", "function")
    run_step("models/game_lock_signal.sql", "game_lock_signal", "view")

    print("\nRunning final regression check against known-good values...")
    if not verify_against_known_good():
        print("\nRebuild ran without errors, but the RESULTING NUMBERS don't "
              "match the known-good baseline. Do not trust this output until "
              "investigated.")
        sys.exit(1)

    print("\nFull pipeline rebuild complete and verified against known-good baseline.")


if __name__ == "__main__":
    main()
