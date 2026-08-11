"""
Daily version of load_game_logs.py -- pulls only YESTERDAY's games
(default) via a date-scoped box score approach instead of looping every
rostered player's full season (that shape is right for a one-time
backfill, wrong for a nightly job -- see project notes on why). Uses
ScoreboardV2 to find the night's game_ids + home/visitor teams, then
BoxScoreTraditionalV2 per game -- a handful of API calls instead of
hundreds. Reuses game_logs.py's own load_game_logs() insert function so
the ON CONFLICT/composite-PK logic stays a single source of truth.

FIRST-PASS, PARTIALLY VERIFIED (8/10/26) -- BoxScoreTraditionalV2 was
confirmed dead for the 2025-26 season (no data published); switched to V3,
whose player/team-stats column shapes ARE confirmed against a real pull.
Still NOT verified: ScoreboardV2 carries the same "known issues" warning
for 2025-26 games specifically -- get_games_for_date() below still calls
V2 and needs the same column-check treatment before trusting it, exactly
like verify_boxscore_columns.py just did for the box score endpoint.
Also still unconfirmed: the raw `minutes` string FORMAT from V3 (column
name is confirmed, its value format -- "MM:SS" vs something else -- was
cut off in the terminal output that confirmed everything else).

  - This does NOT run team_schedule_gaps/gap_reasons/rebuild_materialized_
    views.sql after loading -- those still need to be triggered
    separately (or folded into this script) for injury-return flags and
    the B2B/effective-games materialized views to reflect last night's
    games.

Run: python scripts/load_daily_game_logs.py [YYYY-MM-DD]
     (defaults to yesterday if no date given)
"""

import sys
from datetime import date, timedelta

from nba_api.stats.endpoints import scoreboardv2, boxscoretraditionalv3

from data_cleaning_boxscore import clean_boxscore
from load_game_logs import load_game_logs, GAME_LOGS_COLUMNS
from db_connection import get_connection


def season_for_date(d: date) -> str:
    """'2026-02-14' -> '22025'. Same Oct-cutoff heuristic as
    load_daily_team_schedule.py -- confirm near season boundaries."""
    start_year = d.year if d.month >= 10 else d.year - 1
    return f"2{start_year}"  # matches this project's season_id format, e.g. '22024'


def get_games_for_date(target_date: date) -> list[dict]:
    """
    STILL UNVERIFIED (see file docstring) -- ScoreboardV2 carries the same
    kind of "known issues for 2025-26" warning BoxScoreTraditionalV2 did
    before it turned out to return no real data at all. Run this against
    a real date and confirm HOME_TEAM_ID/VISITOR_TEAM_ID/GAME_ID actually
    populate correctly before trusting it -- if V2 is similarly broken,
    this needs the same V2->V3 migration data_cleaning_boxscore.py just got.
    """
    date_str = target_date.strftime("%m/%d/%Y")
    sb = scoreboardv2.ScoreboardV2(game_date=date_str)
    header = sb.get_data_frames()[0]  # GameHeader
    return [
        {
            "game_id": row["GAME_ID"],
            "home_team_id": row["HOME_TEAM_ID"],
            "visitor_team_id": row["VISITOR_TEAM_ID"],
        }
        for _, row in header.iterrows()
    ]


def main():
    target_date = (
        date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1
        else date.today() - timedelta(days=1)
    )
    season_id = season_for_date(target_date)

    print(f"Finding games for {target_date.isoformat()}...")
    games = get_games_for_date(target_date)
    print(f"{len(games)} game(s) found.")

    conn = get_connection()
    total_inserted = 0
    failures = []

    for i, g in enumerate(games, start=1):
        try:
            box = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=g["game_id"])
            dfs = box.get_data_frames()
            player_stats, team_stats = dfs[0], dfs[2]  # confirmed frame order, 8/10/26

            cleaned = clean_boxscore(
                player_stats, team_stats, g["game_id"], target_date.isoformat(), season_id,
                g["home_team_id"], g["visitor_team_id"],
            )[GAME_LOGS_COLUMNS]

            n = load_game_logs(cleaned, conn)
            total_inserted += n
            print(f"[{i}/{len(games)}] game_id {g['game_id']}: {n} rows")
        except Exception as e:
            print(f"[{i}/{len(games)}] game_id {g['game_id']}: FAILED — {e}")
            failures.append((g["game_id"], str(e)))

    conn.close()

    print(f"\nDone. Inserted {total_inserted} total rows for {target_date.isoformat()}.")
    if failures:
        print(f"\n{len(failures)} game(s) failed:")
        for gid, err in failures:
            print(f"  - {gid}: {err}")

    print("\nNOTE: this does not run gap_reasons/materialized-view rebuilds --")
    print("run those separately if you need injury flags or B2B-adjusted")
    print("views to reflect tonight's games.")


if __name__ == "__main__":
    main()
