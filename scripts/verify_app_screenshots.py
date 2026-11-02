"""Compare slot scores transcribed from Sleeper app screenshots with the lock rows in nba_lock_decisions.

The CSV (data/app_screenshots/week<N>_slots.csv) has one row per roster slot: roster_id, slot, player, score, game.
For each slot this checks that the stored lock is the same player with the same score, and says where a mismatch is a game
(different game locked) or only a score (NBA correction). A slot that is not the player's last game of the week must be a sleeper_flag
(manual) lock; the source column shows that.

Usage (from ~/projects/two_words):  python scripts/verify_app_screenshots.py --week 2 [--csv path]
"""
import argparse
import csv
import sys
import unicodedata
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))


def fold(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--csv")
    args = ap.parse_args()
    path = Path(args.csv) if args.csv else Path(__file__).resolve().parents[1] / "data" / "app_screenshots" / f"week{args.week}_slots.csv"
    rows = list(csv.DictReader(open(path)))

    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT league_id FROM sleeper_current_league")
    league = cur.fetchone()[0]
    cur.execute("""SELECT roster_id, slot, player_name, fantasy_score, game_date, source FROM nba_lock_decisions
                   WHERE league_id = %s AND week_number = %s""", (league, args.week))
    db = {(r[0], r[1]): r for r in cur.fetchall()}

    ok = bad = 0
    totals = {}
    for r in rows:
        roster, slot, score = int(r["roster_id"]), r["slot"], float(r["score"])
        d = db.get((roster, slot))
        totals.setdefault(roster, [0.0, 0.0])
        totals[roster][0] += score
        if r["player"] == "EMPTY":
            continue
        if not d:
            print(f"roster {roster} {slot}: app {r['player']} {score:.2f}, NO lock row in the database")
            bad += 1
            continue
        totals[roster][1] += float(d[3])
        same_player = fold(r["player"].split()[0]) in fold(d[2]) or fold(r["player"]) in fold(d[2])
        diff = float(d[3]) - score
        if same_player and abs(diff) < 0.011:
            ok += 1
            continue
        bad += 1
        why = "different player" if not same_player else f"score differs by {diff:+.2f}"
        print(f"roster {roster} {slot}: app {r['player']} {score:.2f} ({r['game']}) vs database {d[2]} {float(d[3]):.2f} on {d[4]} [{d[5]}]: {why}")
    print(f"\n{ok} of {ok + bad} slots match the app exactly")
    for roster, (app, mine) in sorted(totals.items()):
        flag = "" if abs(app - mine) < 0.011 else f"  <- differs {mine - app:+.2f}"
        print(f"  roster {roster}: app slots {app:.2f}, database locks {mine:.2f}{flag}")
    print("\nsource counts for week", args.week)
    cur.execute("SELECT source, count(*) FROM nba_lock_decisions WHERE league_id = %s AND week_number = %s GROUP BY 1 ORDER BY 2 DESC",
                (league, args.week))
    for s, n in cur.fetchall():
        print(f"  {s}: {n}")
    cur.close(); conn.close()


if __name__ == "__main__":
    main()
