"""
Populates game_logs: for each team's CommonTeamRoster (per season, not
get_active_players() -- that only reflects TODAY's roster, wrong for any
past season), pulls PlayerGameLog, cleans it, and bulk-inserts. Run from
project root: python scripts/load_game_logs.py [SEASON]. Prereqs: game_logs.sql
run, players table populated (load_players.py pulls the full historical list).

FIXED 8/21/26: load_game_logs() was returning len(rows) -- the count of
rows ATTEMPTED, not the count Postgres actually inserted. Since the
INSERT uses ON CONFLICT (game_id, player_id) DO NOTHING, any row that
already existed was silently skipped, but the function reported the
full attempted count regardless -- so "N rows" in every caller's output
(this file's own main(), and load_daily_game_logs.py) never actually
proved anything was new. Found while testing load_daily_game_logs.py
against dates that may have already been loaded: the printed insert
counts couldn't be trusted to answer whether the data was really new.
Now uses cur.rowcount after the INSERT, which correctly reflects only
the rows Postgres actually wrote, not rows skipped by the conflict
clause.

ALSO FIXED 8/21/26: sys.path.append() was placed AFTER the local import
that depends on it (data_cleaning_nba_api, which lives one level up in
flat scripts/) -- same ordering bug already found and fixed the same
night in load_daily_game_logs.py and load_daily_team_schedule.py, missed
here on the first pass. Surfaced via backfill_single_player.py, which
imports from this file and hit the same ModuleNotFoundError. Moved to
the top, right after importing sys/Path, before any local import.
"""

import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pandas as pd
from psycopg2.extras import execute_values

from nba_api.stats.static import teams as nba_teams
from nba_api.stats.endpoints import playergamelog, commonteamroster

from data_cleaning_nba_api import clean_gamelog
from db_connection import get_connection

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
    all_teams = nba_teams.get_teams()
    seen = {}  # player_id -> full_name; dedupes a mid-season trade across two teams' rosters

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
    n_inserted = cur.rowcount  # actual rows Postgres wrote -- excludes ON CONFLICT DO NOTHING skips
    conn.commit()
    cur.close()
    return n_inserted


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
