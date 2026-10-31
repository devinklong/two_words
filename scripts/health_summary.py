"""One-glance health of the pipeline. Prints GREEN / YELLOW / RED per area and overall; exit code 1 only on RED.

  game logs   the last audit_game_logs.py run (nba_game_log_audit): any check with issues is RED
  foul scan   played games not yet scanned for technical/flagrant fouls: RED if any (excluding today's)
  sleeper     age of the newest matchup snapshot: YELLOW over 6 hours, RED over 24
  lock flags  Sleeper's own lock flags for the current week: YELLOW if not refreshed for 6 hours (RED 24), or if a flagged game
              cannot be matched to an NBA game (RED)
  players     rostered players without an NBA id: RED if any
  weeks       closed weeks (nba_week_reconciliation): RED if a team's locked games do not add up to Sleeper's final score;
              YELLOW if scores add up but the lock choice is not proven (several combinations fit)

--notify  also shows a Mac notification when something needs you: any RED, or YELLOW on sleeper / lock flags. It speaks only when
          that set CHANGES (and once when it clears), so an hourly run never repeats itself.
--alert TEXT  shows a notification with that text right away (run_daily uses it when a step fails) and exits.

Usage (from ~/projects/two_words):  python scripts/health_summary.py [--season-id 22026] [--notify]
"""
import argparse
import subprocess
import sys
from pathlib import Path
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
        bad = [f"{n} ({i})" for n, i, _ in audit if i and n != "minutes"]
        notes = [f"{n} ({i})" for n, i, _ in audit if i and n == "minutes"]
        pend = sum(p for _, _, p in audit)
        out.append(("game logs", "RED" if bad else "YELLOW" if notes else "GREEN",
                    ("issues: " + ", ".join(bad)) if bad else (f"{len(audit)} checks run" + (f", note: {', '.join(notes)} minute total off in the NBA's own box score" if notes else " clear")
                                                                 + (f", {pend} pending today" if pend else ""))))
    cur.execute("""SELECT COUNT(DISTINCT ts.game_id), COUNT(DISTINCT l.game_id) FROM team_schedule ts
                   LEFT JOIN technical_flagrant_scan_log l ON l.game_id = ts.game_id
                   WHERE ts.season_id = %s AND ts.wl IS NOT NULL AND ts.game_date < %s""", (season_id, today))
    played, scanned = cur.fetchone()
    out.append(("foul scan", "GREEN" if played == scanned else "RED", f"{scanned} of {played} played games scanned"))
    cur.execute("SELECT to_regclass('game_log_changes')")
    if cur.fetchone()[0]:
        cur.execute("""SELECT COUNT(DISTINCT game_id || '/' || player_id), COUNT(DISTINCT game_id) FROM game_log_changes
                       WHERE changed_at > now() - interval '24 hours'""")
        lines, games = cur.fetchone()
        out.append(("corrections", "GREEN", f"{lines} player line(s) in {games} game(s) corrected by the NBA in the last 24 h"))
    cur.execute("SELECT MAX(synced_at) FROM sleeper_matchup_points_snapshots")
    last = cur.fetchone()[0]
    if last is None:
        out.append(("sleeper", "RED", "no snapshots"))
    else:
        hours = (now - last).total_seconds() / 3600
        out.append(("sleeper", "GREEN" if hours <= 6 else "YELLOW" if hours <= 24 else "RED", f"newest snapshot {hours:.1f} h old"))
    cur.execute("SELECT to_regclass('sleeper_lock_flags'), to_regclass('sleeper_game_map')")
    if all(cur.fetchone()):
        cur.execute("""SELECT w.week_number, COUNT(f.*), MAX(f.last_seen_at), COUNT(*) FILTER (WHERE m.nba_game_id IS NULL AND f.slot IS NOT NULL)
                       FROM fantasy_weeks w
                       LEFT JOIN sleeper_lock_flags f ON f.week_number = w.week_number AND f.season_id = w.season_id
                       LEFT JOIN sleeper_game_map m ON m.sleeper_game_id = f.sleeper_game_id
                       WHERE w.season_id = %s AND %s BETWEEN w.week_start_date AND w.week_end_date GROUP BY w.week_number""", (season_id, today))
        row = cur.fetchone()
        if row:
            wk, n, seen, unmatched = row
            hours = (now - seen).total_seconds() / 3600 if seen else None
            level = "RED" if unmatched or (hours is not None and hours > 24) else "YELLOW" if hours is not None and hours > 6 else "GREEN"
            msg = f"week {wk}: {n} locked starter(s) read from Sleeper" + (f", newest {hours:.1f} h old" if hours is not None else "")
            msg += f", {unmatched} on a game not matched to an NBA game" if unmatched else ""
            out.append(("lock flags", level, msg))
    cur.execute("SELECT COUNT(*) FROM roster_ownership WHERE nba_player_id IS NULL")
    n = cur.fetchone()[0]
    out.append(("players", "GREEN" if n == 0 else "RED", f"{n} rostered player(s) without an NBA id"))
    cur.execute("""SELECT week_number, COUNT(*), COUNT(*) FILTER (WHERE reconciled),
                          COUNT(*) FILTER (WHERE checksum_status IN ('unique', 'offset')),
                          COUNT(*) FILTER (WHERE reconciled AND explained_diff <> 0)
                   FROM nba_week_reconciliation WHERE season_id = %s GROUP BY week_number ORDER BY week_number""", (season_id,))
    for week, teams, ok, proven, explained in cur.fetchall():
        level = "RED" if ok < teams else "GREEN" if proven == teams else "YELLOW"
        msg = f"{ok}/{teams} teams add up to Sleeper's total; lock choice proven for {proven}/{teams}" + (f"; {explained} differ only by a later NBA stat correction" if explained else "")
        out.append((f"week {week}", level, msg))
    return out


ALERT_YELLOW = ("sleeper", "lock flags")   # yellow in these areas needs you; the minutes note and the like do not
STATE = Path(__file__).resolve().parent / "logs" / ".health_notified"


def push(title, text):
    """Mac notification; silently does nothing off a Mac or outside a login session."""
    try:
        subprocess.run(["osascript", "-e", "on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\nend run",
                        title, text], timeout=20, check=False)
    except Exception:
        pass


def notify(rows):
    alerts = [(a, lvl, m) for a, lvl, m in rows if lvl == "RED" or (lvl == "YELLOW" and a in ALERT_YELLOW)]
    key = "|".join(f"{a}:{lvl}" for a, lvl, _ in alerts)
    try:
        last = STATE.read_text() if STATE.exists() else ""
        if key == last:
            return
        STATE.parent.mkdir(exist_ok=True)
        STATE.write_text(key)
    except OSError:
        last = ""
    if alerts:
        push("two_words needs attention", "; ".join(f"{a}: {m}" for a, _, m in alerts)[:240])
    elif last:
        push("two_words is back to normal", "Nothing needs attention.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season-id", default="22026")
    ap.add_argument("--notify", action="store_true")
    ap.add_argument("--alert")
    args = ap.parse_args()
    if args.alert:
        push("two_words needs attention", args.alert)
        return
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
    if args.notify:
        notify(rows)
    sys.exit(1 if overall == "RED" else 0)


if __name__ == "__main__":
    main()
