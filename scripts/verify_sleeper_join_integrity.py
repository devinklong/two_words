"""
scripts/verify_sleeper_join_integrity.py

Two systematic checks the Nembhard spot-check didn't cover, since that
one used an explicit date rather than week_number:

1. WEEK-BOUNDARY ALIGNMENT: does game_fantasy_scores_weekly_effective's
   own week_start_date/week_end_date actually match Sleeper's real week
   boundaries, for every week in both seasons -- not just week 1.
2. COVERAGE GAPS: for every crosswalked player, every week they were
   actually rostered (sleeper_matchups.players) and actually played a
   real game (game_logs), does a fantasy_score row exist? A silent gap
   here means the join mechanism works but is quietly missing people.

Read-only, prints findings -- doesn't write anything.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from db_connection import get_connection

LEAGUE_IDS = {
    "22024": "1113487058661744640",
    "22025": "1214984705477185536",
}


def check_week_alignment(cur):
    print("=" * 70)
    print("CHECK 1: week_start_date/week_end_date alignment vs Sleeper's real weeks")
    print("=" * 70)

    for season_id, league_id in LEAGUE_IDS.items():
        print(f"\n  Season {season_id}:")

        cur.execute("""
            SELECT DISTINCT week_number, week_start_date, week_end_date
            FROM game_fantasy_scores_weekly_effective
            WHERE season_id = %s
            ORDER BY week_number;
        """, (season_id,))
        our_weeks = cur.fetchall()

        if not our_weeks:
            print("    No rows found -- check season_id format.")
            continue

        mismatches = 0
        for week_number, week_start, week_end in our_weeks:
            # Real games played by anyone rostered that week, per Sleeper -- the
            # ground truth for what that week's date range should contain.
            cur.execute("""
                SELECT MIN(gl.game_date), MAX(gl.game_date)
                FROM sleeper_matchups sm
                JOIN sleeper_player_crosswalk swc ON swc.sleeper_player_id = ANY(sm.players)
                JOIN game_logs gl ON gl.player_id = swc.nba_player_id
                WHERE sm.league_id = %s AND sm.week = %s
                  AND gl.season_id = %s
                  AND gl.game_date BETWEEN %s AND %s;
            """, (league_id, week_number, season_id, week_start, week_end))
            real_min, real_max = cur.fetchone()

            status = "OK"
            if real_min is not None and (real_min < week_start or real_max > week_end):
                status = "MISMATCH"
                mismatches += 1

            print(f"    week {week_number:2d}: ours=[{week_start}, {week_end}]  "
                  f"real_games_in_range=[{real_min}, {real_max}]  [{status}]")

        print(f"  {mismatches} mismatched week(s) out of {len(our_weeks)}.")


def check_coverage_gaps(cur):
    print()
    print("=" * 70)
    print("CHECK 2: rostered + played, but no fantasy_score row (silent gaps)")
    print("=" * 70)

    for season_id, league_id in LEAGUE_IDS.items():
        cur.execute("""
            WITH week_bounds AS (
                SELECT DISTINCT week_number, week_start_date, week_end_date
                FROM game_fantasy_scores_weekly_effective
                WHERE season_id = %s
            )
            SELECT sm.week, swc.sleeper_full_name, swc.nba_player_id, gl.game_date
            FROM sleeper_matchups sm
            JOIN sleeper_player_crosswalk swc ON swc.sleeper_player_id = ANY(sm.players)
            JOIN week_bounds wb ON wb.week_number = sm.week
            JOIN game_logs gl
                ON gl.player_id = swc.nba_player_id
                AND gl.season_id = %s
                AND gl.game_date BETWEEN wb.week_start_date AND wb.week_end_date
            LEFT JOIN game_fantasy_scores_weekly_effective gfsw
                ON gfsw.player_id = swc.nba_player_id
                AND gfsw.game_date = gl.game_date
                AND gfsw.season_id = %s
            WHERE sm.league_id = %s
              AND gfsw.player_id IS NULL
            ORDER BY sm.week, gl.game_date;
        """, (season_id, season_id, season_id, league_id))
        gaps = cur.fetchall()

        print(f"\n  Season {season_id}: {len(gaps)} gap(s)")
        for week, name, player_id, game_date in gaps[:20]:
            print(f"    week {week}: {name} (nba_player_id={player_id}) -- {game_date}")
        if len(gaps) > 20:
            print(f"    ... and {len(gaps) - 20} more")


def run():
    conn = get_connection()
    cur = conn.cursor()

    check_week_alignment(cur)
    check_coverage_gaps(cur)

    cur.close()
    conn.close()

    print()
    print("=" * 70)
    print("Any MISMATCH or gap above needs review before treating week_number-based")
    print("joins as fully trustworthy for every player -- not just the one already")
    print("spot-checked by hand.")


if __name__ == "__main__":
    run()
