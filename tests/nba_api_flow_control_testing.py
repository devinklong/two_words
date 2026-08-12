"""
Test suite for lock_decision_input.py, data_cleaning_boxscore.py, and
get_scoreboard_games.py. Plain assertions/prints, not pytest -- matches
the project's existing tests/*.sql pattern (run it, eyeball PASS/FAIL).

Covers the original 6 checks (formula, DB-hit path, fallback trigger,
error handling) plus additions from tonight's real bugs and features:
  - team_id auto-resolution (added 8/11/26)
  - regression tests locking in two real bugs found and fixed tonight,
    so neither can silently reappear: the minutes "MM:SS" -> integer
    parse (data_cleaning_boxscore.py), and the Final-status filter that
    originally excluded every overtime game (get_scoreboard_games.py)

NOT covered here, left as manual verification: the full
load_daily_game_logs.py chain (box score pull -> clean -> insert ->
gap_reasons -> sync) -- that needs live nba_api access and real
transaction/timing behavior a fast unit-style suite isn't well-suited to
faking convincingly. Rerun that manually against a real date after any
future change to the daily pipeline.

FILL IN IF NOT ALREADY (see constants below): LOW_USAGE_PLAYER_ID should
be any real player_id NOT in ownable_player_pool for JOKIC_SEASON_ID.

Run from anywhere: python tests/nba_api_flow_control_testing.py
(also works if left at the project root, but tests/ is the intended home)
"""

import os
import subprocess
import sys

# Anchored to this file's own location, not the current working directory --
# works whether this file lives at the project root or in tests/.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "scripts"))

from lock_decision_input import compute_fantasy_score, resolve_team_id
from data_cleaning_boxscore import clean_boxscore
from get_scoreboard_games import get_completed_games_with_home_away
from db_connection import get_connection

JOKIC_TEST_STATS = {
    "pts": 31, "oreb": 4, "dreb": 17, "ast": 22, "stl": 3, "blk": 0,
    "tov": 4, "fgm": 13, "fga": 22, "ftm": 2, "fta": 3, "fg3m": 3,
}
JOKIC_EXPECTED_SCORE = 113.10

JOKIC_PLAYER_ID = 203999
JOKIC_TEAM_ID = 1610612743  # Denver -- confirmed via resolve_team_id() and real box score
OTHER_TEAM_ID = 1610612756  # Phoenix -- used to confirm --team-id override is honored
JOKIC_SEASON_ID = "22024"
JOKIC_GAME_DATE = "2025-03-07"
JOKIC_GAME_ID = "0022400909"  # confirmed real -- also an OT game, see regression test below

LOW_USAGE_PLAYER_ID = "1642389"  # confirmed real, NOT in ownable_player_pool for JOKIC_SEASON_ID


def run_cli(*args) -> subprocess.CompletedProcess:
    script_path = os.path.join(_PROJECT_ROOT, "scripts", "lock_decision_input.py")
    return subprocess.run(
        [sys.executable, script_path, *args],
        capture_output=True, text=True,
    )


# =========================
# Formula
# =========================

def test_formula_matches_real_sleeper_score():
    """Pure-function test -- no DB, no API. Ground truth: Jokić vs PHX
    3/7/25, confirmed real Sleeper score of 113.10, real oreb/dreb split
    (4/17) confirmed via a live box score pull 8/10/26."""
    score = compute_fantasy_score(JOKIC_TEST_STATS)
    assert score == JOKIC_EXPECTED_SCORE, (
        f"compute_fantasy_score() returned {score}, expected {JOKIC_EXPECTED_SCORE}."
    )
    print(f"PASS: formula matches real Sleeper score ({score})")


# =========================
# DB-first lookup / fallback
# =========================

def test_db_hit_path_matches_sql_directly():
    """Confirms get_from_db() returns exactly what a direct SQL query
    against game_lock_signal would."""
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
        print("SKIP: game not found in game_lock_signal -- check game_lock_signal was rebuilt")
        return

    expected_fantasy_score, expected_lock_bar, expected_lock_signal, _ = row

    # DB-hit path deliberately omits --team-id -- confirms team_id
    # resolution is never even attempted when the DB already has the answer
    result = run_cli(
        str(JOKIC_PLAYER_ID), "--game-id", JOKIC_GAME_ID,
        "--season-id", JOKIC_SEASON_ID, "--game-date", JOKIC_GAME_DATE,
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
    print("PASS: DB-hit path matches direct SQL query exactly (no --team-id needed)")


def test_fallback_path_triggers_on_missing_game():
    """Fake game_id forces get_from_db() to return None. Confirms the
    FALLBACK branch is reached and gets past team_id resolution cleanly
    before failing later on the real (fake) nba_api call -- not testing a
    full live round trip, just which branch runs and how far it gets."""
    result = run_cli(
        str(JOKIC_PLAYER_ID), "--game-id", "0000000000",  # deliberately fake
        "--season-id", JOKIC_SEASON_ID, "--game-date", JOKIC_GAME_DATE,
    )
    assert "[from database]" not in result.stdout, (
        f"fake game_id unexpectedly matched a DB row:\n{result.stdout}"
    )
    combined = result.stdout + result.stderr
    assert "team_id" not in combined.lower() or "No game found" in combined, (
        f"expected to fail on the live pull itself, not on team_id resolution:\n{combined}"
    )
    print("PASS: fallback path correctly triggered, got past team_id resolution cleanly")


# =========================
# team_id auto-resolution (added 8/11/26)
# =========================

def test_resolve_team_id_matches_known_team():
    """Direct unit test of resolve_team_id() against real game_logs data
    -- no CLI involved."""
    conn = get_connection()
    team_id = resolve_team_id(conn, JOKIC_PLAYER_ID)
    conn.close()
    assert team_id == JOKIC_TEAM_ID, f"expected {JOKIC_TEAM_ID}, got {team_id}"
    print(f"PASS: resolve_team_id() correctly resolved to {team_id} (Denver)")


def test_manual_mode_auto_resolves_team_id():
    """No --team-id given -- should silently auto-resolve and still
    produce the correct, known-good result."""
    result = run_cli(
        str(JOKIC_PLAYER_ID), "--manual",
        "--pts", "31", "--oreb", "4", "--dreb", "17", "--ast", "22",
        "--stl", "3", "--blk", "0", "--tov", "4",
        "--fgm", "13", "--fga", "22", "--ftm", "2", "--fta", "3", "--fg3m", "3",
        "--season-id", JOKIC_SEASON_ID, "--game-date", JOKIC_GAME_DATE,
    )
    assert result.returncode == 0, f"unexpected failure:\n{result.stdout}\n{result.stderr}"
    assert f"Fantasy score: {JOKIC_EXPECTED_SCORE}" in result.stdout, (
        f"unexpected score with auto-resolved team_id:\n{result.stdout}"
    )
    print("PASS: manual mode auto-resolves team_id and produces the correct score")


def test_team_id_override_is_honored():
    """Explicit --team-id should short-circuit resolve_team_id() entirely
    -- confirmed at the code level (args.team_id or resolve_team_id(...)),
    this just confirms passing it doesn't break anything end to end."""
    result = run_cli(
        str(JOKIC_PLAYER_ID), "--manual",
        "--pts", "31", "--oreb", "4", "--dreb", "17", "--ast", "22",
        "--stl", "3", "--blk", "0", "--tov", "4",
        "--fgm", "13", "--fga", "22", "--ftm", "2", "--fta", "3", "--fg3m", "3",
        "--season-id", JOKIC_SEASON_ID, "--game-date", JOKIC_GAME_DATE,
        "--team-id", str(OTHER_TEAM_ID),
    )
    assert result.returncode == 0, f"unexpected failure with --team-id override:\n{result.stdout}\n{result.stderr}"
    print("PASS: --team-id override accepted without error")


# =========================
# Error handling
# =========================

def test_manual_mode_missing_stat_errors_cleanly():
    result = run_cli(
        str(JOKIC_PLAYER_ID), "--manual", "--pts", "31",
        "--season-id", JOKIC_SEASON_ID, "--game-date", JOKIC_GAME_DATE,
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
    )
    assert result.returncode == 1, f"expected exit code 1, got {result.returncode}"
    assert "--game-id is required" in result.stdout, (
        f"missing --game-id error message not found:\n{result.stdout}"
    )
    print("PASS: non-manual mode without --game-id correctly errors")


def test_player_not_in_ownable_pool_raises_clearly():
    """A fully bogus/non-pool player fails on get_player_context()'s pool
    check BEFORE resolve_team_id() ever runs (confirmed 8/11/26 -- pool
    membership requires games_played>=20, which already implies
    game_logs history exists, so resolve_team_id()'s own "no history"
    branch is effectively unreachable through this flow). This test
    checks for the pool-membership message specifically, not a
    team_id-resolution message."""
    if LOW_USAGE_PLAYER_ID == "TODO_FILL_IN":
        print("SKIP: fill in a real LOW_USAGE_PLAYER_ID not in ownable_player_pool")
        return

    result = run_cli(
        str(LOW_USAGE_PLAYER_ID), "--manual",
        "--pts", "5", "--oreb", "0", "--dreb", "2", "--ast", "1",
        "--stl", "0", "--blk", "0", "--tov", "1",
        "--fgm", "2", "--fga", "5", "--ftm", "0", "--fta", "0", "--fg3m", "0",
        "--season-id", JOKIC_SEASON_ID, "--game-date", JOKIC_GAME_DATE,
    )
    combined_output = result.stdout + result.stderr
    assert "not in the ownable pool" in combined_output, (
        f"expected a clear 'not in the ownable pool' error:\n{combined_output}"
    )
    print("PASS: non-pool player correctly raises a clear error instead of a silent wrong answer")


# =========================
# Regression tests -- lock in two real bugs found tonight (8/10-11/26)
# so neither can silently reappear
# =========================

def test_minutes_mm_ss_format_parses_to_integer():
    """REGRESSION (8/10/26): game_logs.minutes is INTEGER, but
    BoxScoreTraditionalV3 reports "MM:SS" -- inserting the raw string
    failed with "invalid input syntax for type integer". Confirms
    clean_boxscore() converts correctly (seconds dropped, not rounded)."""
    import pandas as pd

    fake_player_stats = pd.DataFrame([
        {"teamId": JOKIC_TEAM_ID, "personId": JOKIC_PLAYER_ID, "minutes": "31:39",
         "comment": "", "fieldGoalsMade": 1, "fieldGoalsAttempted": 1,
         "threePointersMade": 0, "threePointersAttempted": 0,
         "freeThrowsMade": 0, "freeThrowsAttempted": 0,
         "reboundsOffensive": 0, "reboundsDefensive": 0,
         "assists": 0, "steals": 0, "blocks": 0, "turnovers": 0,
         "foulsPersonal": 0, "points": 2, "plusMinusPoints": 0.0},
    ])
    fake_team_stats = pd.DataFrame([
        {"teamId": JOKIC_TEAM_ID, "points": 100},
        {"teamId": OTHER_TEAM_ID, "points": 90},
    ])

    cleaned = clean_boxscore(
        fake_player_stats, fake_team_stats, JOKIC_GAME_ID, JOKIC_GAME_DATE,
        JOKIC_SEASON_ID, JOKIC_TEAM_ID, OTHER_TEAM_ID,
    )
    minutes_value = cleaned.iloc[0]["minutes"]
    assert minutes_value == 31, f"expected 31 (seconds dropped from '31:39'), got {minutes_value}"
    assert isinstance(minutes_value, int) or float(minutes_value).is_integer(), (
        f"minutes should be a whole number, got {minutes_value} ({type(minutes_value)})"
    )
    print(f"PASS: '31:39' correctly parsed to integer minutes ({minutes_value})")


def test_final_status_filter_includes_overtime_games():
    """REGRESSION (8/10/26): get_completed_games_with_home_away() used to
    exact-match gameStatusText == 'Final', silently excluding every OT
    game (real value is 'Final/OT', 'Final/2OT', etc.). Confirms the real
    Jokić game -- itself an OT game -- is now included for a live pull."""
    games = get_completed_games_with_home_away(JOKIC_GAME_DATE)
    game_ids = [g["game_id"] for g in games]
    assert JOKIC_GAME_ID in game_ids, (
        f"OT game {JOKIC_GAME_ID} missing from completed games on {JOKIC_GAME_DATE} -- "
        f"got: {game_ids}. Check gameStatusText filtering uses startswith('Final'), not =='Final'."
    )
    print(f"PASS: OT game {JOKIC_GAME_ID} correctly included ({len(games)} total games found)")


def main():
    tests = [
        test_formula_matches_real_sleeper_score,
        test_db_hit_path_matches_sql_directly,
        test_fallback_path_triggers_on_missing_game,
        test_resolve_team_id_matches_known_team,
        test_manual_mode_auto_resolves_team_id,
        test_team_id_override_is_honored,
        test_manual_mode_missing_stat_errors_cleanly,
        test_non_manual_without_game_id_errors_cleanly,
        test_player_not_in_ownable_pool_raises_clearly,
        test_minutes_mm_ss_format_parses_to_integer,
        test_final_status_filter_includes_overtime_games,
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
