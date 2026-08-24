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

The FINAL check is a real regression test against known-good numbers,
updated as of 8/23/26 -- see below for the full history of what
changed and why.

UPDATED 8/23/26: baseline moved from the original 8/16/26 values
(HOLD/LOCK/PASS 52.7/24.2/23.0, Jokić lock_bar=79.490) after TWO real,
confirmed formula corrections went live the same day, not a
regression:
  (1) Technical/flagrant foul penalties (-2.0 each) were entirely
      missing from game_fantasy_scores until 8/23/26 -- confirmed via
      a full hand-verified investigation (multiple real games checked
      directly against Sleeper's actual recorded scores) and backfilled
      via scripts/backfill_technical_flagrant_fouls.py. A handful of
      games that used to clear lock_bar correctly no longer do once
      their real penalty is subtracted.
  (2) ownable_player_pool.sql had a real bug from the #9 season-
      bootstrap redesign (8/22/26): its current_season_stats CTE
      filtered to ONLY the live current season, silently zeroing out
      every OTHER historical season in the view -- collapsing
      game_lock_signal from ~122,573 rows down to ~9,509 (roughly one
      season's worth of data total across the whole 5-season history).
      Fixed 8/23/26: historical seasons now read
      player_season_fantasy_stats directly again, exactly as before
      #9; the rolling bootstrap is correctly scoped to the current
      season only.
Both fixes are confirmed correct and intentional -- the new baseline
below reflects the formula actually being right, not a target to keep
matching forever if it changes again for a real reason. If this check
ever fails again, verify whether the underlying formula/data
genuinely changed on purpose (like today) before assuming a bug.

Run from the project root:
    python scripts/rebuild_lock_pipeline.py
"""

import subprocess
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from db_connection import get_connection

PGCONN = "postgres"

# UPDATED 8/23/26 -- see module docstring above for the full history of
# why these moved from the original 8/16/26 values (52.7/24.2/23.0,
# 79.490). Both changes are confirmed correct formula fixes, not a
# regression to chase.
EXPECTED_SPLIT = {"HOLD": 52.7, "LOCK": 24.3, "PASS": 23.0}
EXPECTED_JOKIC_LOCK_BAR = 79.465
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
