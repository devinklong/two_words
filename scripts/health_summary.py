"""One-glance health of the pipeline. Prints GREEN / YELLOW / RED per area and overall; exit code 1 only on RED.

  game logs   the last audit_game_logs.py run (nba_game_log_audit): any check with issues is RED
  foul scan   played games not yet scanned for technical/flagrant fouls: RED if any (excluding today's)
  sleeper     age of the newest matchup snapshot: YELLOW over 6 hours, RED over 24
  players     rostered players without an NBA id: RED if any
  weeks       closed weeks (nba_week_reconciliation): RED if a team's locked games do not add up to Sleeper's final score;
              YELLOW if scores add up but the lock choice is not proven (several combinations fit)

Usage (from ~/projects/two_words):  python scripts/health_summary.py [--season-id 22026]
"""
import argparse
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

ORDER = {"GREEN": 0, "YELLOW": 1, "RED": 2}


def gather(cur, season_id, today, now):
    """-> list of (area, level, message)."""
    out = []
    cur.execute("""SELECT check_name, issues, pending FROM nba_game_log_audit
                   WHERE season_id = %s AND run_at = (SELECT MAX(run_at) FROM nba_game_log_audit WHERE season_id = %s)""",
                (season_id, season_id))
    audit = cur.fetchall()
    if not audit:
        out.append(("game logs", "RED", "audit has never run"))
    else:
        bad = [f"{n} ({i})" for n, i, _ in audit if i]
        pend = sum(p for _, _, p in audit)
        out.append(("game logs", "RED" if bad else "GREEN",
                    ("issues: " + ", ".join(bad)) if bad else f"{len(audit)} checks clear" + (f", {pend} pending today" if pend else "")))
    cur.execute("""SELECT COUNT(DISTINCT ts.game_id), COUNT(DISTINCT l.game_id) FROM team_schedule ts
                   LEFT JOIN technical_flagrant_scan_log l ON l.game_id = ts.game_id
                   WHERE ts.season_id = %s AND ts.wl IS NOT NULL AND ts.game_date < %s""", (season_id, today))
    played, scanned = cur.fetchone()
    out.append(("foul scan", "GREEN" if played == scanned else "RED", f"{scanned} of {played} played games scanned"))
    cur.execute("SELECT MAX(synced_at) FROM sleeper_matchup_points_snapshots")
    last = cur.fetchone()[0]
    if last is None:
        out.append(("sleeper", "RED", "no snapshots"))
    else:
        hours = (now - last).total_seconds() / 3600
        out.append(("sleeper", "GREEN" if hours <= 6 else "YELLOW" if hours <= 24 else "RED", f"newest snapshot {hours:.1f} h old"))
    cur.execute("SELECT COUNT(*) FROM roster_ownership WHERE nba_player_id IS NULL")
    n = cur.fetchone()[0]
    out.append(("players", "GREEN" if n == 0 else "RED", f"{n} rostered player(s) without an NBA id"))
    cur.execute("""SELECT week_number, COUNT(*), COUNT(*) FILTER (WHERE reconciled),
                          COUNT(*) FILTER (WHERE checksum_status IN ('unique', 'offset'))
                   FROM nba_week_reconciliation WHERE season_id = %s GROUP BY week_number ORDER BY week_number""", (season_id,))
    for week, teams, ok, proven in cur.fetchall():
        level = "RED" if ok < teams else "GREEN" if proven == teams else "YELLOW"
        msg = f"{ok}/{teams} teams add up to Sleeper's total; lock choice proven for {proven}/{teams}"
        out.append((f"week {week}", level, msg))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season-id", default="22026")
    args = ap.parse_args()
    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.now(ZoneInfo("America/Chicago"))
    rows = gather(cur, args.season_id, now.date(), now)
    overall = max((lvl for _, lvl, _ in rows), key=ORDER.get, default="GREEN")
    print(f"=== Pipeline health {now:%Y-%m-%d %H:%M}: {overall} ===")
    for area, level, msg in rows:
        print(f"  {level:<6} {area:<10} {msg}")
    cur.close(); conn.close()
    sys.exit(1 if overall == "RED" else 0)


if __name__ == "__main__":
    main()
