"""
Run this locally (where nba_api + your scripts/ package are available) to confirm
clean_gamelog()'s actual output before trusting game_logs.sql's column types —
specifically the minutes column, which nba_api sometimes returns as "MM:SS" or
a float rather than a clean number.

Lives in scripts/ — run as:
    python scripts/verify_gamelog_columns.py PLAYER_ID [SEASON]

Example:
    python scripts/verify_gamelog_columns.py 1642875 2025-26
"""

import sys
from data_cleaning_nba_api import clean_gamelog
from nba_api.stats.endpoints import playergamelog
from nba_api.stats.static import teams


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/verify_gamelog_columns.py PLAYER_ID [SEASON]")
        sys.exit(1)

    player_id = sys.argv[1]
    season = sys.argv[2] if len(sys.argv) > 2 else "2024-25"

    # Same team_lookup construction as 01_explore_game_logs.ipynb —
    # abbreviation -> team_id, used by clean_gamelog() to derive
    # opponent_team_id/is_home from the MATCHUP column.
    all_teams = teams.get_teams()
    team_lookup = {t["abbreviation"]: t["id"] for t in all_teams}

    raw = playergamelog.PlayerGameLog(player_id=player_id, season=season).get_data_frames()[0]
    cleaned = clean_gamelog(raw, team_lookup)

    print("=== dtypes ===")
    print(cleaned.dtypes)

    print("\n=== first 5 rows ===")
    print(cleaned.head())

    # Focus on the minutes column specifically — check whatever it's named
    # post-cleaning (MIN, min, minutes, etc.)
    min_col_candidates = [c for c in cleaned.columns if c.lower() in ("min", "minutes")]
    if min_col_candidates:
        col = min_col_candidates[0]
        print(f"\n=== '{col}' column sample values + dtype ===")
        print(cleaned[col].head(10))
        print("dtype:", cleaned[col].dtype)
        print("any strings with ':' in them?",
              cleaned[col].astype(str).str.contains(":").any())
    else:
        print("\nNo min/minutes column found — check clean_gamelog() output columns above.")

    # Also flag any all-null columns, which might hint at a schema mismatch
    null_cols = cleaned.columns[cleaned.isnull().all()].tolist()
    if null_cols:
        print("\nWarning — columns that are entirely null in this sample:", null_cols)


if __name__ == "__main__":
    main()
