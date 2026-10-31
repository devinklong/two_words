"""Audit the box scores in game_logs for a season, against the official team results in team_schedule.

Runs on the whole season each time (a few thousand rows), so an old problem can never hide behind "the daily check passed".
Results are printed and stored in nba_game_log_audit (one row per check per run); exit code 1 if any check has issues.

  completeness   every played team-game in team_schedule has box-score rows, and every game has both teams
  team points    sum of the players' pts == the team's official pts (team_schedule)
  result         game_logs.wl == team_schedule.wl for every player row
  row points     each row: pts == 2*(fgm-fg3m) + 3*fg3m + ftm
  minutes        a team's minutes add up to 235-300 (240 + overtime)
  roster size    at least 8 players per team per game
  foul scan      every played game has been scanned for technical/flagrant fouls
  scores table   every game_logs row has its row in game_fantasy_scores_weekly_effective
Today's games (not necessarily published yet) are reported as pending, not as issues.

Usage (from ~/projects/two_words):  python scripts/audit_game_logs.py [--season-id 22026]
"""
import argparse
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

DDL = """CREATE TABLE IF NOT EXISTS nba_game_log_audit (
    run_at timestamptz NOT NULL DEFAULT now(), season_id text NOT NULL, check_name text NOT NULL,
    issues integer NOT NULL, pending integer NOT NULL DEFAULT 0, sample text)"""

PLAYED = "ts.wl IS NOT NULL AND ts.season_id = %(s)s"

CHECKS = {
    "completeness": (
        f"""SELECT ts.game_id || ' team ' || ts.team_id, ts.game_date FROM team_schedule ts
            WHERE {PLAYED} AND NOT EXISTS (SELECT 1 FROM game_logs g WHERE g.game_id = ts.game_id AND g.team_id = ts.team_id)
            UNION ALL
            SELECT ts.game_id || ' has ' || COUNT(*) || ' team row(s)', MIN(ts.game_date) FROM team_schedule ts
            WHERE {PLAYED} GROUP BY ts.game_id HAVING COUNT(*) <> 2""", True),
    "team points": (
        f"""SELECT ts.game_id || ' team ' || ts.team_id || ': players ' || SUM(g.pts) || ' vs official ' || ts.pts, ts.game_date
            FROM team_schedule ts JOIN game_logs g ON g.game_id = ts.game_id AND g.team_id = ts.team_id
            WHERE {PLAYED} GROUP BY ts.game_id, ts.team_id, ts.pts, ts.game_date HAVING SUM(g.pts) <> ts.pts""", True),
    "result": (
        f"""SELECT g.game_id || ' team ' || g.team_id || ': logs ' || COALESCE(g.wl, '-') || ' vs schedule ' || ts.wl, ts.game_date
            FROM team_schedule ts JOIN game_logs g ON g.game_id = ts.game_id AND g.team_id = ts.team_id
            WHERE {PLAYED} AND g.wl IS DISTINCT FROM ts.wl GROUP BY g.game_id, g.team_id, g.wl, ts.wl, ts.game_date""", False),
    "row points": (
        """SELECT g.game_id || ' player ' || g.player_id || ': pts ' || g.pts || ' vs ' || (2*(g.fgm-g.fg3m) + 3*g.fg3m + g.ftm), g.game_date
           FROM game_logs g WHERE g.season_id = %(s)s AND g.pts <> 2*(g.fgm-g.fg3m) + 3*g.fg3m + g.ftm""", False),
    "minutes": (
        f"""SELECT g.game_id || ' team ' || g.team_id || ': ' || SUM(g.minutes) || ' min', ts.game_date
            FROM team_schedule ts JOIN game_logs g ON g.game_id = ts.game_id AND g.team_id = ts.team_id
            WHERE {PLAYED} GROUP BY g.game_id, g.team_id, ts.game_date HAVING SUM(g.minutes) NOT BETWEEN 235 AND 300""", True),
    "roster size": (
        f"""SELECT g.game_id || ' team ' || g.team_id || ': ' || COUNT(*) || ' players', ts.game_date
            FROM team_schedule ts JOIN game_logs g ON g.game_id = ts.game_id AND g.team_id = ts.team_id
            WHERE {PLAYED} GROUP BY g.game_id, g.team_id, ts.game_date HAVING COUNT(*) < 8""", False),
    "foul scan": (
        f"""SELECT ts.game_id, MIN(ts.game_date) FROM team_schedule ts
            WHERE {PLAYED} AND NOT EXISTS (SELECT 1 FROM technical_flagrant_scan_log l WHERE l.game_id = ts.game_id)
            GROUP BY ts.game_id""", True),
    "scores table": (
        """SELECT g.game_id || ' player ' || g.player_id, g.game_date FROM game_logs g
           WHERE g.season_id = %(s)s AND NOT EXISTS (SELECT 1 FROM game_fantasy_scores_weekly_effective e
                                                      WHERE e.game_id = g.game_id AND e.player_id = g.player_id)""", True),
}


def run_checks(cur, season_id, today):
    """-> [(check_name, issues, pending, sample)]. Rows dated today (or later) count as pending where the check allows it."""
    out = []
    for name, (sql, pend_ok) in CHECKS.items():
        cur.execute(sql, {"s": season_id})
        rows = cur.fetchall()
        pending = [r for r in rows if pend_ok and r[1] >= today]
        real = [r for r in rows if r not in pending]
        out.append((name, len(real), len(pending), "; ".join(str(r[0]) for r in real[:5])))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season-id", default="22026")
    args = ap.parse_args()
    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(DDL)
    today = datetime.now(ZoneInfo("America/Chicago")).date()
    results = run_checks(cur, args.season_id, today)
    bad = 0
    for name, issues, pending, sample in results:
        flag = "OK " if not issues else "ISSUES"
        extra = f"  ({pending} pending today)" if pending else ""
        print(f"  {flag:<6} {name:<14} {issues}{extra}" + (f"   e.g. {sample}" if sample else ""))
        cur.execute("INSERT INTO nba_game_log_audit (season_id, check_name, issues, pending, sample) VALUES (%s,%s,%s,%s,%s)",
                    (args.season_id, name, issues, pending, sample or None))
        bad += bool(issues)
    conn.commit()
    cur.execute("SELECT COUNT(DISTINCT game_id), COUNT(*) FROM game_logs WHERE season_id = %s", (args.season_id,))
    games, rows = cur.fetchone()
    print(f"Audited {games} games / {rows} box-score rows for season {args.season_id}: " + ("ALL CLEAR" if not bad else f"{bad} check(s) with issues"))
    cur.close(); conn.close()
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
