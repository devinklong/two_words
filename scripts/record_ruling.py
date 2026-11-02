"""Record that the commissioner changed a team's week total by hand, so it shows as an explained exception instead of RED.

Our locks are NBA truth. When Sleeper's total for a team differs because of a league decision (a restored win, a points adjustment, a
dropped player's slot counted or not counted), no lock choice can add up to it. This stores Sleeper's current total for that team and week,
marks it status 'ruling', and sets explained_diff to the gap between that total and the stored locks, so nba_week_reconciliation shows the
team as reconciled and health_summary stays green. infer_lock_decisions.py never re-derives a ruled team.

Usage (from ~/projects/two_words):  python scripts/record_ruling.py --week 2 --roster 2 --total 375.75 --note "commissioner restored W after waiver drop" [--dry-run]
"""
import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season-id", default="22026")
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--roster", type=int, required=True)
    ap.add_argument("--total", type=float, required=True, help="the team's total as Sleeper shows it now")
    ap.add_argument("--note", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT league_id FROM sleeper_current_league")
    league = cur.fetchone()[0]
    cur.execute("""SELECT COALESCE(SUM(fantasy_score), 0), COUNT(*) FROM nba_lock_decisions
                   WHERE league_id = %s AND season_id = %s AND week_number = %s AND roster_id = %s""",
                (league, args.season_id, args.week, args.roster))
    locks, n = cur.fetchone()
    if n == 0:
        raise SystemExit("no lock rows for that team and week; run infer_lock_decisions.py first")
    gap = round(args.total - float(locks), 2)
    print(f"week {args.week} roster {args.roster}: {n} locks add to {float(locks):.2f}; Sleeper shows {args.total:.2f}; ruling gap {gap:+.2f}")
    if args.dry_run:
        print("(dry run, nothing written)")
        return
    cur.execute("""INSERT INTO sleeper_roster_week_totals (league_id, season_id, week_number, roster_id, final_points, status, note, explained_diff)
                   VALUES (%s,%s,%s,%s,%s,'ruling',%s,%s)
                   ON CONFLICT (league_id, season_id, week_number, roster_id)
                   DO UPDATE SET final_points = EXCLUDED.final_points, status = 'ruling', note = EXCLUDED.note,
                                 explained_diff = EXCLUDED.explained_diff, recorded_at = now()""",
                (league, args.season_id, args.week, args.roster, args.total, "league ruling: " + args.note, gap))
    conn.commit()
    print("recorded")
    cur.close(); conn.close()


if __name__ == "__main__":
    main()
