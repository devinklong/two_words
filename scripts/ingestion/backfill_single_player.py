"""
Backfill a single player's game log for one season -- useful when the full
roster loop hits an isolated failure (timeout, rate limit) for just one or
two players and you don't want to rerun the whole 500+ player list.

CHAINED (8/10/26): after inserting this player's game_logs rows, also
scopes build_gap_reasons() to their tenure window and runs
sync_game_fantasy_scores_weekly_effective() -- this is the backfill path
most likely to actually add new game_logs rows (unlike
backfill_missing_players.py, which only touches the players table), so
it's the one that most needs both chained.

FIXED 8/21/26: nba_api's bundled static player list
(nba_players.find_players_by_full_name) lags behind on very recent
additions -- confirmed live: a player active in real April 2026 games
returned zero matches. Kept the static lookup as the FIRST attempt
(fast, no API call, correctly covers the vast majority of players
including older/retired ones) and added a live commonallplayers-based
fallback that only runs when the static list finds nothing -- so
existing behavior for already-covered players is unchanged, and the
gap for very recent players is closed without a live API call on every
run.

ALSO FIXED 8/21/26: sys.path.append() was placed AFTER local imports
that depend on it (build_gap_reasons, sync_game_fantasy_scores_weekly_
effective) -- same ordering bug found and fixed in several other files
the same night. This one ran successfully in live testing despite the
bug, most likely because PYTHONPATH already covered scripts/ in that
shell session -- not because the file itself was actually correct.
Fixed anyway so the file doesn't depend on an environment variable
outside its own control.

Run: python scripts/backfill_single_player.py "PLAYER FULL NAME" SEASON
Example: python scripts/backfill_single_player.py "Alex Caruso" 2022-23
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from nba_api.stats.static import players as nba_players
from nba_api.stats.endpoints import commonallplayers
from load_game_logs import build_team_lookup, fetch_and_clean_one_player, load_game_logs
from build_gap_reasons import build_gap_reasons
from sync_game_fantasy_scores_weekly_effective import sync_game_fantasy_scores_weekly_effective
from db_connection import get_connection


def find_players_live(full_name: str) -> list[dict]:
    """Live fallback for nba_players.find_players_by_full_name() -- only
    used when the static list finds nothing, since it lags behind on
    very recent additions. Only searches CURRENT-season players --
    appropriate as a fallback specifically for "not in the static list
    yet" cases, which by definition means very recently added/active
    players, not older ones the static list already correctly covers.
    Returns results in the same shape the rest of this file already
    expects (list of {'full_name', 'id'} dicts) so nothing downstream
    needs to change."""
    df = commonallplayers.CommonAllPlayers(is_only_current_season=1).get_data_frames()[0]
    matched = df[df["DISPLAY_FIRST_LAST"].str.lower() == full_name.lower()]
    return [
        {"full_name": row["DISPLAY_FIRST_LAST"], "id": row["PERSON_ID"]}
        for _, row in matched.iterrows()
    ]


def season_start_date(season: str) -> str:
    """'2022-23' -> '2022-10-01' -- a safe early-October floor for the
    gap_reasons date_from filter; NBA regular seasons start in October."""
    start_year = season.split("-")[0]
    return f"{start_year}-10-01"


def main():
    if len(sys.argv) < 3:
        print('Usage: python scripts/backfill_single_player.py "PLAYER FULL NAME" SEASON')
        sys.exit(1)

    full_name = sys.argv[1]
    season = sys.argv[2]

    matches = nba_players.find_players_by_full_name(full_name)
    if not matches:
        print(f"'{full_name}' not found in nba_api's static player list -- trying a live "
              f"lookup (covers very recent additions the static list hasn't caught up to)...")
        matches = find_players_live(full_name)

    if not matches:
        print(f"No player found matching '{full_name}'")
        sys.exit(1)
    if len(matches) > 1:
        print(f"Multiple matches for '{full_name}':")
        for m in matches:
            print(f"  {m['full_name']} (id {m['id']})")
        sys.exit(1)

    player_id = matches[0]["id"]
    print(f"Found {matches[0]['full_name']} (player_id {player_id}). Fetching {season}...")

    team_lookup = build_team_lookup()
    conn = get_connection()

    try:
        cleaned = fetch_and_clean_one_player(player_id, season, team_lookup)
        n = load_game_logs(cleaned, conn)
        print(f"Inserted {n} rows for {matches[0]['full_name']}, {season}.")

        print(f"\nAnnotating gaps from {season} onward with injury reasons...")
        build_gap_reasons(conn, date_from=season_start_date(season))

        print("\nSyncing game_fantasy_scores_weekly_effective...")
        n_synced = sync_game_fantasy_scores_weekly_effective(conn)
        print(f"Synced {n_synced} new row(s).")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
