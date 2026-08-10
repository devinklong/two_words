"""
Populates the players table from nba_api's static (full historical) player
list. Run once after schema/players.sql; safe to rerun (ON CONFLICT DO NOTHING).
"""

import pandas as pd
from psycopg2.extras import execute_values

from nba_api.stats.static import players as nba_players
from db_connection import get_connection


def build_players_df() -> pd.DataFrame:
    all_players = nba_players.get_players()
    df = pd.DataFrame(all_players)
    df = df.rename(columns={"id": "player_id"})
    df = df[["player_id", "full_name", "first_name", "last_name", "is_active"]]
    return df


def load_players(df: pd.DataFrame) -> None:
    conn = get_connection()
    cur = conn.cursor()
    rows = list(df.itertuples(index=False, name=None))

    execute_values(
        cur,
        """
        INSERT INTO players (player_id, full_name, first_name, last_name, is_active)
        VALUES %s
        ON CONFLICT (player_id) DO NOTHING
        """,
        rows,
    )

    conn.commit()
    print(f"Inserted (or skipped existing) {len(rows)} players.")
    cur.close()
    conn.close()


def main():
    df = build_players_df()
    print(f"Pulled {len(df)} players from nba_api.")
    print(df.head())
    load_players(df)


if __name__ == "__main__":
    main()
