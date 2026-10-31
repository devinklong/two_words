"""Record which games' fantasy scores the NBA's later stat corrections changed, and what the score was BEFORE.

game_log_changes keeps every stat the NBA corrected after we first saved it (old and new value). Sleeper freezes a
week's total at the end of the week, so a team's Sleeper total can include the old score for a game the NBA then
corrected. This script works out, for each corrected player-game, the fantasy score the ORIGINAL stat line would have
earned and stores it next to the current score in game_score_corrections. infer_lock_decisions.py and
nba_week_reconciliation then use it to explain (not hide) a difference between our official-box-score totals and
Sleeper's.

No scoring weights are copied here: the original score comes from game_fantasy_scores_weekly_full itself, by putting the
old stats back for one row inside a savepoint, reading the view, and rolling the savepoint back. Nothing in game_logs
is ever changed. Safe to re-run; it recomputes every corrected row each time.

Usage (from ~/projects/two_words):  python scripts/log_score_corrections.py
"""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))

DDL = """CREATE TABLE IF NOT EXISTS game_score_corrections (
    game_id text NOT NULL, player_id bigint NOT NULL, original_score numeric NOT NULL, current_score numeric NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (game_id, player_id))"""


def main():
    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT to_regclass('game_log_changes')")
    if cur.fetchone()[0] is None:
        print("No game_log_changes yet; nothing to do.")
        return
    cur.execute(DDL)
    cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'game_logs'")
    types = dict(cur.fetchall())
    cur.execute("""SELECT DISTINCT ON (game_id, player_id, column_name) game_id, player_id, column_name, old_value
                   FROM game_log_changes ORDER BY game_id, player_id, column_name, changed_at""")
    rows = defaultdict(dict)   # (game, player) -> {column: the value we first saved}
    for gid, pid, col, old in cur.fetchall():
        if col in types:
            rows[(gid, int(pid))][col] = old
    kept = removed = 0
    for (gid, pid), cols in rows.items():
        cur.execute("SELECT fantasy_score FROM game_fantasy_scores_weekly_full WHERE game_id = %s AND player_id = %s", (gid, pid))
        now = cur.fetchone()
        if not now:
            continue
        cur.execute("SAVEPOINT original_stats")
        for col, old in cols.items():
            cur.execute(f"UPDATE game_logs SET {col} = %s::{types[col]} WHERE game_id = %s AND player_id = %s", (old, gid, pid))
        cur.execute("SELECT fantasy_score FROM game_fantasy_scores_weekly_full WHERE game_id = %s AND player_id = %s", (gid, pid))
        before = cur.fetchone()
        cur.execute("ROLLBACK TO SAVEPOINT original_stats")
        if not before or round(float(now[0]) - float(before[0]), 2) == 0:
            cur.execute("DELETE FROM game_score_corrections WHERE game_id = %s AND player_id = %s", (gid, pid))
            removed += 1
            continue
        cur.execute("""INSERT INTO game_score_corrections (game_id, player_id, original_score, current_score)
                       VALUES (%s,%s,%s,%s)
                       ON CONFLICT (game_id, player_id) DO UPDATE SET original_score = EXCLUDED.original_score,
                           current_score = EXCLUDED.current_score, updated_at = now()""", (gid, pid, before[0], now[0]))
        kept += 1
    conn.commit()
    print(f"{len(rows)} corrected player-game(s) checked: {kept} changed the fantasy score, {removed} did not.")
    cur.close(); conn.close()


if __name__ == "__main__":
    main()
