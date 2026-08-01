"""
Populate game_logs by pulling each active player's PlayerGameLog for a given
season, cleaning it with clean_gamelog(), and bulk-inserting into Postgres.

Prereqs:
  - schema/game_logs.sql has been run (table exists, empty)
  - players table is already populated (load_players.py) — game_logs.player_id
    has a NOT NULL FK to players, so this will fail per-player if a player_id
    isn't in players yet.

Run from the project root:
    python load_game_logs.py [SEASON]

Example:
    python load_game_logs.py 2025-26
"""

import sys
import time

import pandas as pd
from psycopg2.extras import execute_values

from nba_api.stats.static import players as nba_players
from nba_api.stats.static import teams as nba_teams
from nba_api.stats.endpoints import playergamelog

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
    active = nba_players.get_active_players()
    print(f"Loading game logs for {len(active)} active players, season {season}...")

    conn = get_connection()
    total_inserted = 0
    failures = []

    for i, p in enumerate(active, start=1):
        player_id = p["id"]
        try:
            cleaned = fetch_and_clean_one_player(player_id, season, team_lookup)
            n = load_game_logs(cleaned, conn)
            total_inserted += n
            print(f"[{i}/{len(active)}] {p['full_name']}: {n} rows")
        except Exception as e:
            print(f"[{i}/{len(active)}] {p['full_name']}: FAILED — {e}")
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
