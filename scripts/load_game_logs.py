"""
Populate game_logs by pulling each season-specific roster player's
PlayerGameLog, cleaning it with clean_gamelog(), and bulk-inserting into
Postgres.

Roster source: CommonTeamRoster(team_id, season) per team, NOT
get_active_players() — get_active_players() only reflects TODAY's roster,
which is wrong for any season other than the current one (misses players
who were active back then but have since retired/left the league, and
would incorrectly include current players who weren't active that season).
Looping 30 team rosters (~30 calls) is also far cheaper than looping the
full ~5,100-player historical list.

A player traded mid-season appears on two teams' rosters for the same
season — the player_id set is deduped across teams before fetching game
logs, so their game log (which already carries the correct team_id per
game via MATCHUP) is only fetched once, not once per team.

Prereqs:
  - schema/game_logs.sql has been run (table exists, empty or partially
    loaded)
  - players table is already populated (load_players.py) — game_logs.player_id
    has a NOT NULL FK to players, so this will fail per-player if a player_id
    isn't in players yet. Since load_players.py pulls the FULL historical
    player list (not just active), any player who ever appeared on an NBA
    roster should already be covered.

Run from the project root:
    python scripts/load_game_logs.py [SEASON]

Example:
    python scripts/load_game_logs.py 2022-23
"""

import sys
import time

import pandas as pd
from psycopg2.extras import execute_values

from nba_api.stats.static import teams as nba_teams
from nba_api.stats.endpoints import playergamelog, commonteamroster

from data_cleaning_nba_api import clean_gamelog
from db_connection import get_connection  # same-folder import — run as `python scripts/load_game_logs.py [SEASON]` from project root

# Columns must match game_logs.sql exactly, in order, lowercase
GAME_LOGS_COLUMNS = [
    "game_id", "player_id", "team_id", "opponent_team_id", "season_id",
    "game_date", "is_home", "wl", "minutes", "fgm", "fga", "fg3m", "fg3a",
    "ftm", "fta", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf", "pts",
    "plus_minus",
]

SLEEP_SECONDS_BETWEEN_CALLS = 0.6  # be polite to the unofficial nba_api endpoints


def build_team_lookup() -> dict:
    all_teams = nba_teams.get_teams()
    return {t["abbreviation"]: t["id"] for t in all_teams}


def build_season_roster(season: str) -> list[dict]:
    """
    Loops every team's CommonTeamRoster for the given season and returns a
    deduped list of {player_id, full_name} — deduped since a traded player
    appears on two teams' rosters for the same season, and we only want to
    fetch their game log once.
    """
    all_teams = nba_teams.get_teams()
    seen = {}  # player_id -> full_name, dedupes trades automatically

    for i, t in enumerate(all_teams, start=1):
        try:
            roster = commonteamroster.CommonTeamRoster(
                team_id=t["id"], season=season
            ).get_data_frames()[0]
            for _, row in roster.iterrows():
                seen[row["PLAYER_ID"]] = row["PLAYER"]
            print(f"  Roster [{i}/{len(all_teams)}] {t['full_name']}: {len(roster)} players")
        except Exception as e:
            print(f"  Roster [{i}/{len(all_teams)}] {t['full_name']}: FAILED — {e}")

        time.sleep(SLEEP_SECONDS_BETWEEN_CALLS)

    return [{"id": pid, "full_name": name} for pid, name in seen.items()]


def fetch_and_clean_one_player(player_id: int, season: str, team_lookup: dict) -> pd.DataFrame:
    raw = playergamelog.PlayerGameLog(player_id=player_id, season=season).get_data_frames()[0]
    if raw.empty:
        return raw

    cleaned = clean_gamelog(raw, team_lookup)

    # Rename to match DDL exactly — verified against clean_gamelog()'s actual
    # output columns (nba_api's raw casing survives for most fields)
    cleaned = cleaned.rename(columns={
        "SEASON_ID": "season_id",
        "Player_ID": "player_id",
        "Game_ID": "game_id",
        "GAME_DATE": "game_date",
        "WL": "wl",
        "MIN": "minutes",
        "FGM": "fgm", "FGA": "fga", "FG3M": "fg3m", "FG3A": "fg3a",
        "FTM": "ftm", "FTA": "fta",
        "OREB": "oreb", "DREB": "dreb",
        "AST": "ast", "STL": "stl", "BLK": "blk", "TOV": "tov",
        "PF": "pf", "PTS": "pts", "PLUS_MINUS": "plus_minus",
        # team_id, opponent_team_id, is_home are already lowercase from clean_gamelog()
    })

    missing = [c for c in GAME_LOGS_COLUMNS if c not in cleaned.columns]
    if missing:
        raise ValueError(f"clean_gamelog() output is missing expected columns: {missing}")

    return cleaned[GAME_LOGS_COLUMNS]


def load_game_logs(df: pd.DataFrame, conn) -> int:
    if df.empty:
        return 0

    cur = conn.cursor()
    rows = list(df.itertuples(index=False, name=None))

    execute_values(
        cur,
        f"""
        INSERT INTO game_logs ({", ".join(GAME_LOGS_COLUMNS)})
        VALUES %s
        ON CONFLICT (game_id, player_id) DO NOTHING
        """,
        rows,
    )
    conn.commit()
    cur.close()
    return len(rows)


def main():
    season = sys.argv[1] if len(sys.argv) > 1 else "2025-26"

    team_lookup = build_team_lookup()

    print(f"Building season-specific roster for {season}...")
    roster = build_season_roster(season)
    print(f"\n{len(roster)} unique players rostered in {season} (deduped across trades).")

    conn = get_connection()
    total_inserted = 0
    failures = []

    for i, p in enumerate(roster, start=1):
        player_id = p["id"]
        try:
            cleaned = fetch_and_clean_one_player(player_id, season, team_lookup)
            n = load_game_logs(cleaned, conn)
            total_inserted += n
            print(f"[{i}/{len(roster)}] {p['full_name']}: {n} rows")
        except Exception as e:
            print(f"[{i}/{len(roster)}] {p['full_name']}: FAILED — {e}")
            failures.append((p["full_name"], str(e)))

        time.sleep(SLEEP_SECONDS_BETWEEN_CALLS)

    conn.close()

    print(f"\nDone. Inserted {total_inserted} total rows.")
    if failures:
        print(f"\n{len(failures)} player(s) failed:")
        for name, err in failures:
            print(f"  - {name}: {err}")


if __name__ == "__main__":
    main()
