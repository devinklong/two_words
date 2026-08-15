"""
scripts/sleeper_crosswalk_regression_testing.py

Standing regression test: for one deterministically-chosen, crosswalked
player per roster (10 rosters), across every week they appear in a real
matchup (both seasons), confirms fantasy_score exists for their actual
game(s) that week. Locks in what verify_sleeper_join_integrity.py already
proved at scale (0 gaps, both seasons) as a permanent re-runnable test
instead of a one-time script.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from db_connection import get_connection

LEAGUE_IDS = {
    "22024": "1113487058661744640",
    "22025": "1214984705477185536",
}


def pick_test_players(cur, league_id):
    """One crosswalked player per roster -- the earliest (alphabetically)
    matched player on that roster's week-1 players array. Deterministic,
    so the same players get tested every run."""
    cur.execute("""
        SELECT DISTINCT ON (sm.roster_id)
            sm.roster_id, swc.sleeper_player_id, swc.nba_player_id, swc.sleeper_full_name
        FROM sleeper_matchups sm
        JOIN sleeper_player_crosswalk swc ON swc.sleeper_player_id = ANY(sm.players)
        WHERE sm.league_id = %s AND sm.week = 1
        ORDER BY sm.roster_id, swc.sleeper_full_name;
    """, (league_id,))
    return cur.fetchall()


def check_player_all_weeks(cur, league_id, season_id, roster_id, sleeper_player_id, nba_player_id):
    cur.execute("""
        WITH week_bounds AS (
            SELECT DISTINCT week_number, week_start_date, week_end_date
            FROM game_fantasy_scores_weekly_effective
            WHERE season_id = %s
        )
        SELECT sm.week, gl.game_date, (gfsw.player_id IS NOT NULL) AS has_fantasy_score
        FROM sleeper_matchups sm
        JOIN week_bounds wb ON wb.week_number = sm.week
        JOIN game_logs gl
            ON gl.player_id = %s
            AND gl.season_id = %s
            AND gl.game_date BETWEEN wb.week_start_date AND wb.week_end_date
        LEFT JOIN game_fantasy_scores_weekly_effective gfsw
            ON gfsw.player_id = %s AND gfsw.game_date = gl.game_date AND gfsw.season_id = %s
        WHERE sm.league_id = %s AND sm.roster_id = %s AND %s = ANY(sm.players)
        ORDER BY sm.week, gl.game_date;
    """, (season_id, nba_player_id, season_id, nba_player_id, season_id,
          league_id, roster_id, sleeper_player_id))
    return cur.fetchall()


def run():
    conn = get_connection()
    cur = conn.cursor()

    total_checks, total_failures = 0, 0

    for season_id, league_id in LEAGUE_IDS.items():
        print("=" * 70)
        print(f"SEASON {season_id}")
        print("=" * 70)

        test_players = pick_test_players(cur, league_id)
        print(f"  {len(test_players)} test player(s) selected (one per roster)")

        for roster_id, sleeper_player_id, nba_player_id, full_name in test_players:
            rows = check_player_all_weeks(cur, league_id, season_id, roster_id,
                                           sleeper_player_id, nba_player_id)
            missing = [r for r in rows if not r[2]]
            total_checks += len(rows)
            total_failures += len(missing)

            status = "PASS" if not missing else "FAIL"
            print(f"  [{status}] roster {roster_id}: {full_name} -- {len(rows)} game(s) checked, "
                  f"{len(missing)} missing fantasy_score")
            for week, game_date, _ in missing:
                print(f"      MISSING: week {week}, {game_date}")

    cur.close()
    conn.close()

    print()
    print(f"{total_checks - total_failures}/{total_checks} game-checks passed across both seasons.")
    if total_failures:
        raise SystemExit(1)


if __name__ == "__main__":
    run()
