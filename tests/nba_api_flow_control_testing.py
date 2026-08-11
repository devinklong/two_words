"""
Manual test suite for lock_decision_input.py's three paths (DB hit, live
pull fallback, manual stat entry) plus the standalone scoring formula.
Plain assertions/prints, not pytest -- matches the project's existing
tests/*.sql pattern (run it, eyeball PASS/FAIL), just in Python since
this exercises a Python script rather than SQL directly.

FILL IN BEFORE RUNNING (marked TODO below):
  - JOKIC_TEST_STATS' oreb/dreb split -- only the combined reb=21 was
    recorded when the formula was first verified against the real
    113.10 Sleeper score; the OREB bonus depends on the actual split,
    not just the total, so this test is only as good as that number.
  - JOKIC_GAME_ID -- the nba_api Game_ID for Jokić vs PHX, 3/7/25.
  - LOW_USAGE_PLAYER_ID -- any real player_id NOT in ownable_player_pool,
    to confirm the "not in pool" error path fires correctly.

Run: python tests/nba_api_flow_control_testing.py
"""

import os
import subprocess
import sys

# Anchored to this file's own location, not the current working directory --
# a plain sys.path.insert(0, "scripts") only works if you happen to run this
# from the project root; this works from anywhere, including tests/.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "scripts"))

from lock_decision_input import compute_fantasy_score
from db_connection import get_connection

# TODO: confirm the real OREB/DREB split for this game
JOKIC_TEST_STATS = {
    "pts": 31, "oreb": 4, "dreb": 17, "ast": 22, "stl": 3, "blk": 0,
    "tov": 4, "fgm": 13, "fga": 22, "ftm": 2, "fta": 3, "fg3m": 3,
}
JOKIC_EXPECTED_SCORE = 113.10

JOKIC_PLAYER_ID = 203999
JOKIC_SEASON_ID = "22024"
JOKIC_GAME_DATE = "2025-03-07"
JOKIC_TEAM_ID = 1610612743  # Denver
JOKIC_GAME_ID = "0022400909"  # nba_api Game_ID for this specific game

LOW_USAGE_PLAYER_ID = "1642389"  # a real player_id NOT in ownable_player_pool


def run_cli(*args) -> subprocess.CompletedProcess:
    script_path = os.path.join(_PROJECT_ROOT, "scripts", "lock_decision_input.py")
    return subprocess.run(
        [sys.executable, script_path, *args],
        capture_output=True, text=True,
    )


def test_formula_matches_real_sleeper_score():
    """Pure-function test -- no DB, no API. Ground truth: Jokić vs PHX
    3/7/25, confirmed real Sleeper score of 113.10."""
    score = compute_fantasy_score(JOKIC_TEST_STATS)
    assert score == JOKIC_EXPECTED_SCORE, (
        f"compute_fantasy_score() returned {score}, expected {JOKIC_EXPECTED_SCORE}. "
        f"If oreb/dreb above are wrong, this fails even though the formula itself is correct."
    )
    print(f"PASS: formula matches real Sleeper score ({score})")


def test_db_hit_path_matches_sql_directly():
    """Confirms get_from_db() returns exactly what a direct SQL query
    against game_lock_signal would -- the whole point of checking the DB
    first instead of trusting the Python formula for already-loaded games."""
    if JOKIC_GAME_ID == "TODO_FILL_IN":
        print("SKIP: fill in a real JOKIC_GAME_ID")
        return

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT fantasy_score, lock_bar, lock_signal, percentage_to_lock
        FROM game_lock_signal
        WHERE player_id = %s AND game_id = %s AND season_id = %s
        """,
        (JOKIC_PLAYER_ID, JOKIC_GAME_ID, JOKIC_SEASON_ID),
    )
    row = cur.fetchone()
    cur.close()
    conn.close()

    if row is None:
        print("SKIP: game not found in game_lock_signal -- check JOKIC_GAME_ID")
        return

    expected_fantasy_score, expected_lock_bar, expected_lock_signal, _ = row

    result = run_cli(
        str(JOKIC_PLAYER_ID), "--game-id", JOKIC_GAME_ID,
        "--season-id", JOKIC_SEASON_ID, "--game-date", JOKIC_GAME_DATE,
        "--team-id", str(JOKIC_TEAM_ID),
    )

    assert "[from database]" in result.stdout, (
        f"expected the DB-hit path, got:\n{result.stdout}"
    )
    assert f"Fantasy score: {float(expected_fantasy_score)}" in result.stdout, (
        f"CLI output didn't match direct SQL fantasy_score ({expected_fantasy_score}):\n{result.stdout}"
    )
    assert expected_lock_signal in result.stdout, (
        f"CLI output didn't match direct SQL lock_signal ({expected_lock_signal}):\n{result.stdout}"
    )
    print("PASS: DB-hit path matches direct SQL query exactly")


def test_fallback_path_triggers_on_missing_game():
    """Live-pull fallback is hard to trigger naturally since almost every
    backfilled game already has a DB row. Uses a fake game_id to force
    get_from_db() to return None and confirm the FALLBACK branch is
    reached -- it will fail past that point without real nba_api access
    in this environment, which is expected/fine; the goal here is only
    confirming which branch runs, not a full live round trip."""
    result = run_cli(
        str(JOKIC_PLAYER_ID), "--game-id", "0000000000",  # deliberately fake
        "--season-id", JOKIC_SEASON_ID, "--game-date", JOKIC_GAME_DATE,
        "--team-id", str(JOKIC_TEAM_ID),
    )
    assert "[from database]" not in result.stdout, (
        f"fake game_id unexpectedly matched a DB row:\n{result.stdout}"
    )
    print("PASS: fallback path correctly triggered on a game_id not in the DB")


def test_manual_mode_missing_stat_errors_cleanly():
    result = run_cli(
        str(JOKIC_PLAYER_ID), "--manual", "--pts", "31",
        "--season-id", JOKIC_SEASON_ID, "--game-date", JOKIC_GAME_DATE,
        "--team-id", str(JOKIC_TEAM_ID),
    )
    assert result.returncode == 1, f"expected exit code 1, got {result.returncode}"
    assert "--manual requires all of" in result.stdout, (
        f"missing-stat error message not found:\n{result.stdout}"
    )
    print("PASS: manual mode correctly errors on missing stats")


def test_non_manual_without_game_id_errors_cleanly():
    result = run_cli(
        str(JOKIC_PLAYER_ID),
        "--season-id", JOKIC_SEASON_ID, "--game-date", JOKIC_GAME_DATE,
        "--team-id", str(JOKIC_TEAM_ID),
    )
    assert result.returncode == 1, f"expected exit code 1, got {result.returncode}"
    assert "--game-id is required" in result.stdout, (
        f"missing --game-id error message not found:\n{result.stdout}"
    )
    print("PASS: non-manual mode without --game-id correctly errors")


def test_player_not_in_ownable_pool_raises_clearly():
    """Confirms get_player_context() fails loudly (not silently) for a
    player who isn't in player_tiers -- a silent wrong answer here would
    be worse than a crash."""
    if LOW_USAGE_PLAYER_ID == "TODO_FILL_IN":
        print("SKIP: fill in a real LOW_USAGE_PLAYER_ID not in ownable_player_pool")
        return

    result = run_cli(
        str(LOW_USAGE_PLAYER_ID), "--manual",
        "--pts", "5", "--oreb", "0", "--dreb", "2", "--ast", "1",
        "--stl", "0", "--blk", "0", "--tov", "1",
        "--fgm", "2", "--fga", "5", "--ftm", "0", "--fta", "0", "--fg3m", "0",
        "--season-id", JOKIC_SEASON_ID, "--game-date", JOKIC_GAME_DATE,
        "--team-id", str(JOKIC_TEAM_ID),
    )
    combined_output = result.stdout + result.stderr
    assert "not in the ownable pool" in combined_output, (
        f"expected a clear 'not in the ownable pool' error:\n{combined_output}"
    )
    print("PASS: non-pool player correctly raises a clear error instead of a silent wrong answer")


def main():
    tests = [
        test_formula_matches_real_sleeper_score,
        test_db_hit_path_matches_sql_directly,
        test_fallback_path_triggers_on_missing_game,
        test_manual_mode_missing_stat_errors_cleanly,
        test_non_manual_without_game_id_errors_cleanly,
        test_player_not_in_ownable_pool_raises_clearly,
    ]

    failures = []
    for test in tests:
        try:
            test()
        except AssertionError as e:
            failures.append(test.__name__)
            print(f"FAIL: {test.__name__}\n  {e}")
        except Exception as e:
            failures.append(test.__name__)
            print(f"ERROR: {test.__name__} raised an unexpected exception: {e}")

    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed.")
    if failures:
        print(f"Failed: {', '.join(failures)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
