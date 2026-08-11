"""
Confirms clean_boxscore()'s actual output against a real
BoxScoreTraditionalV3 pull -- V2 was confirmed dead for the 2025-26
season (no data published), so this checks V3's shape instead. Prints
the RAW pre-clean `minutes` column specifically un-truncated (the
initial column-discovery pull, 8/10/26, confirmed the column NAME but
its value FORMAT got cut off by pandas' terminal truncation) alongside
the cleaned version, so a real mismatch would be visible at a glance.

Run: python scripts/verify_boxscore_columns.py GAME_ID GAME_DATE [SEASON_ID]
Example: python scripts/verify_boxscore_columns.py 0022400909 2025-03-07
"""

import sys
from datetime import date

from nba_api.stats.endpoints import scoreboardv2, boxscoretraditionalv3

from data_cleaning_boxscore import clean_boxscore
from load_daily_game_logs import season_for_date


def find_home_visitor(game_id: str, game_date: str) -> tuple:
    date_str = date.fromisoformat(game_date).strftime("%m/%d/%Y")
    header = scoreboardv2.ScoreboardV2(game_date=date_str).get_data_frames()[0]
    match = header[header["GAME_ID"] == game_id]
    if match.empty:
        raise ValueError(f"game_id={game_id} not found in ScoreboardV2 for {game_date} "
                          f"-- double check the date matches the actual game night")
    row = match.iloc[0]
    return row["HOME_TEAM_ID"], row["VISITOR_TEAM_ID"]


def main():
    if len(sys.argv) < 3:
        print("Usage: python scripts/verify_boxscore_columns.py GAME_ID GAME_DATE [SEASON_ID]")
        sys.exit(1)

    game_id = sys.argv[1]
    game_date = sys.argv[2]
    season_id = sys.argv[3] if len(sys.argv) > 3 else season_for_date(date.fromisoformat(game_date))

    home_team_id, visitor_team_id = find_home_visitor(game_id, game_date)
    print(f"home_team_id={home_team_id}  visitor_team_id={visitor_team_id}  season_id={season_id}")

    dfs = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=game_id).get_data_frames()
    player_stats, team_stats = dfs[0], dfs[2]

    # Printed with to_string() and no column truncation -- the whole point
    # of this script is seeing the ACTUAL values, not a pandas-truncated summary
    print("\n=== RAW box score -- 'minutes' column, UN-truncated ===")
    print(player_stats[["firstName", "familyName", "minutes", "comment"]].head(10).to_string())
    print("dtype:", player_stats["minutes"].dtype)
    print("any strings with ':' in them?", player_stats["minutes"].astype(str).str.contains(":").any())
    print("any empty-string values?", (player_stats["minutes"].astype(str).str.strip() == "").any())

    print("\n=== RAW team_stats frame (used for WL derivation) ===")
    print(team_stats[["teamId", "teamName", "points"]].to_string())

    cleaned = clean_boxscore(player_stats, team_stats, game_id, game_date, season_id,
                              home_team_id, visitor_team_id)

    print("\n=== CLEANED output -- dtypes ===")
    print(cleaned.dtypes)

    print("\n=== CLEANED output -- first 5 rows ===")
    print(cleaned.head().to_string())

    print("\n=== CLEANED 'minutes' column, UN-truncated (compare against RAW above) ===")
    print(cleaned["minutes"].head(10).to_string())

    null_cols = cleaned.columns[cleaned.isnull().all()].tolist()
    if null_cols:
        print("\nWarning — columns that are entirely null in this sample:", null_cols)

    print(f"\n{len(player_stats)} raw player rows -> {len(cleaned)} cleaned rows "
          f"(difference should be DNP/inactive players dropped via the comment filter, nothing else)")


if __name__ == "__main__":
    main()
