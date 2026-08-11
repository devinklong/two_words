"""
One-off discovery script -- same purpose tonight's box-score check served:
pull ScoreboardV3 for a real date and print exactly what comes back before
writing any code against it. Don't assume V3 mirrors V2's shape just
because the endpoint name is similar -- BoxScoreTraditionalV3 turned out
to be full camelCase, not a renamed drop-in, and Scoreboard could easily
follow the same pattern.

Date format is UNCONFIRMED for ScoreboardV3 -- V2 wanted MM/DD/YYYY;
several other V3 endpoints in nba_api accept ISO (YYYY-MM-DD) instead.
This tries ISO first since that's more common in this library's newer
endpoints; if it errors, retry with MM/DD/YYYY and note which one worked
in the file that replaces this one.

Run: python scripts/discover_scoreboard_v3_schema.py YYYY-MM-DD
Example: python scripts/discover_scoreboard_v3_schema.py 2025-03-07
"""

import sys

from nba_api.stats.endpoints import scoreboardv3


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/discover_scoreboard_v3_schema.py YYYY-MM-DD")
        sys.exit(1)

    game_date = sys.argv[1]  # trying ISO format as-is first

    print(f"Requesting ScoreboardV3 for game_date={game_date}...")
    sb = scoreboardv3.ScoreboardV3(game_date=game_date)
    dfs = sb.get_data_frames()

    print(f"\n{len(dfs)} dataframe(s) returned.\n")
    for i, df in enumerate(dfs):
        print(f"--- frame {i} ({len(df)} rows) ---")
        print(df.columns.tolist())
        print(df.head(5).to_string())
        print()

    print("Look specifically for: a game identifier column (compare against")
    print("GAME_ID from the box score side), and home/visitor team ID fields")
    print("(V2 called these HOME_TEAM_ID/VISITOR_TEAM_ID -- V3 may use")
    print("homeTeamId/awayTeamId, or something else entirely, or nest them")
    print("under a different frame than expected).")


if __name__ == "__main__":
    main()
