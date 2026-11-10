"""Correct game_logs.minutes for games loaded while the box-score cleaner dropped the seconds (2026-27 through 11/9/26).

Re-reads each regular-season box score from the NBA (BoxScoreTraditionalV3), rounds "MM:SS" to the nearest minute with the
same parse_minutes() the loader now uses, and updates ONLY the minutes column of rows that already exist and differ. No
row is added or removed and no other column is touched, so scores, locks and totals cannot change. If the stored fantasy
table (game_fantasy_scores_weekly_effective) carries its own minutes column, that copy is brought in line too.

Only games whose id starts with 002 (regular season) are read, so it cannot reach into the preseason. One request per
game, about 2 to 3 minutes for 150 games. Safe to re-run: a second run finds nothing to change.

Usage (from ~/projects/two_words):  python scripts/fix_truncated_minutes.py [--season-id 22026] [--since 2026-10-20] [--dry-run]
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from data_cleaning_boxscore import parse_minutes


def box_minutes(game_id, tries=3):
    """-> {player_id: rounded minutes} for everyone with minutes in the NBA's box score."""
    from nba_api.stats.endpoints import boxscoretraditionalv3
    last = None
    for attempt in range(tries):
        try:
            df = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=game_id, timeout=60).get_data_frames()[0]
            out = {}
            for pid, raw in zip(df["personId"], df["minutes"]):
                m = parse_minutes(raw)
                if m is not None:
                    out[int(pid)] = m
            return out
        except Exception as e:   # the NBA's stats site drops requests now and then
            last = e
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"{game_id}: {last}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season-id", default="22026")
    ap.add_argument("--since", help="only games on or after this date (YYYY-MM-DD)")
    ap.add_argument("--dry-run", action="store_true", help="report what would change, write nothing")
    args = ap.parse_args()

    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""SELECT game_id, min(game_date) FROM game_logs
                   WHERE season_id = %s AND game_id LIKE '002%%' AND (%s::date IS NULL OR game_date >= %s::date)
                   GROUP BY 1 ORDER BY 2, 1""", (args.season_id, args.since, args.since))
    games = cur.fetchall()
    cur.execute("SELECT game_id, player_id, minutes FROM game_logs WHERE season_id = %s AND game_id LIKE '002%%'", (args.season_id,))
    stored = {}
    for gid, pid, m in cur.fetchall():
        stored.setdefault(gid, {})[pid] = m

    changes, failed, before, after = [], [], 0, 0
    for i, (gid, gdate) in enumerate(games, 1):
        try:
            real = box_minutes(gid)
        except RuntimeError as e:
            failed.append(str(e))
            continue
        for pid, old in stored[gid].items():
            new = real.get(pid)
            before += old or 0
            after += new if new is not None else (old or 0)
            if new is not None and new != old:
                changes.append((new, gid, pid))
        if i % 25 == 0:
            print(f"  ... {i}/{len(games)} games read", file=sys.stderr)
        time.sleep(0.6)

    print(f"season {args.season_id}: {len(games)} games read, {len(changes)} of {sum(len(v) for v in stored.values())} rows need a different minutes value")
    print(f"total minutes in those games: {before:,} stored -> {after:,} after rounding")
    if failed:
        print(f"{len(failed)} game(s) could not be read and were left alone (run again to pick them up): {'; '.join(failed[:3])}{' ...' if len(failed) > 3 else ''}")
    if args.dry_run:
        print("dry run, nothing written")
    elif changes:
        cur.executemany("UPDATE game_logs SET minutes = %s WHERE game_id = %s AND player_id = %s", changes)
        print(f"updated {len(changes)} row(s) in game_logs")
        cur.execute("""SELECT 1 FROM information_schema.columns WHERE table_schema = 'public'
                       AND table_name = 'game_fantasy_scores_weekly_effective' AND column_name = 'minutes'""")
        if cur.fetchone():
            cur.execute("""UPDATE game_fantasy_scores_weekly_effective e SET minutes = g.minutes FROM game_logs g
                           WHERE g.game_id = e.game_id AND g.player_id = e.player_id AND g.season_id = %s
                             AND e.minutes IS DISTINCT FROM g.minutes""", (args.season_id,))
            print(f"updated {cur.rowcount} row(s) in game_fantasy_scores_weekly_effective (its own copy of minutes)")
        conn.commit()
    cur.close(); conn.close()


if __name__ == "__main__":
    main()
