"""Season-to-date accuracy check: every data layer, week 1 through today, one PASS / FAIL / NOTE line each. Read-only; exit code 1 on any FAIL.

  1 game log audit      latest run of every audit check (box scores vs official team results, points, results, completeness) has no issues
  2 games loaded        every played team game in team_schedule has box-score rows in game_logs
  3 scores table        every game_logs row has its fantasy score in game_fantasy_scores_weekly_effective
  4 lock scores         every stored lock's score equals the current official score for that player and game
  5 lock flags          every Sleeper lock flag that maps to an NBA game is the lock stored for that slot (same player, same game)
  6 closed weeks        per closed week: how many teams add up to Sleeper's final total, how many lock choices are proven, rulings
  7 lock rows           every team in every closed week has 9 lock rows; none left 'unverified' or ambiguous (those are NOTES: scores still match)
  8 current week        games already played this week and locks recorded so far (information)
Run run_daily.py first (see docs/daily_runbook.md); this only reads what it left in the database.

Usage (from ~/projects/two_words):  python scripts/verify_season_to_date.py [--season-id 22026]
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))

failures = 0


def line(level, name, msg):
    global failures
    if level == "FAIL":
        failures += 1
    print(f"{level:<5} {name:<14} {msg}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season-id", default="22026")
    args = ap.parse_args()
    sid = str(args.season_id)

    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SET TRANSACTION READ ONLY")
    cur.execute("SELECT league_id FROM sleeper_current_league")
    league = cur.fetchone()[0]
    print(f"season {sid}, checked {date.today()}\n")

    # 1 audit
    cur.execute("""SELECT DISTINCT ON (check_name) check_name, issues, pending, run_at FROM nba_game_log_audit
                   WHERE season_id = %s ORDER BY check_name, run_at DESC""", (sid,))
    rows = cur.fetchall()
    if not rows:
        line("FAIL", "audit", "audit_game_logs.py has never run for this season")
    else:
        soft = ("minutes", "roster size")   # same list as SOFT_CHECKS in audit_game_logs.py
        bad = [(c, i) for c, i, _p, _t in rows if i and c not in soft]
        notes = [(c, i) for c, i, _p, _t in rows if i and c in soft]
        pend = sum(p for _c, _i, p, _t in rows)
        line("FAIL" if bad else "PASS", "audit", ("issues in " + ", ".join(f"{c} ({i})" for c, i in bad)) if bad
             else f"{len(rows)} checks clean (last run {max(t for *_x, t in rows):%m/%d %H:%M})" + (f"; {pend} pending (today's games)" if pend else ""))
        for c, i in notes:
            line("NOTE", "audit", f"{c}: {i} game(s); the NBA's own box score (short minutes or a short bench), points and results still match")

    # 2 games loaded
    cur.execute("""SELECT count(DISTINCT s.game_id) FROM team_schedule s
                   WHERE s.season_id = %s AND s.wl IS NOT NULL
                     AND NOT EXISTS (SELECT 1 FROM game_logs g WHERE g.game_id = s.game_id AND g.team_id = s.team_id)""", (sid,))
    missing = cur.fetchone()[0]
    cur.execute("SELECT count(DISTINCT game_id), max(game_date) FROM team_schedule WHERE season_id = %s AND wl IS NOT NULL", (sid,))
    played, last_day = cur.fetchone()
    line("FAIL" if missing else "PASS", "games loaded", f"{missing} played game(s) without box scores" if missing else f"all {played} played games have box scores (latest {last_day})")

    # 3 scores table
    cur.execute("""SELECT count(*) FROM game_logs g WHERE g.season_id = %s AND NOT EXISTS
                   (SELECT 1 FROM game_fantasy_scores_weekly_effective e WHERE e.game_id = g.game_id AND e.player_id = g.player_id)""", (sid,))
    n = cur.fetchone()[0]
    line("FAIL" if n else "PASS", "scores table", f"{n} box-score row(s) without a fantasy score" if n else "every box-score row has its fantasy score")

    # 4 lock scores
    cur.execute("""SELECT count(*) FROM nba_lock_decisions d JOIN game_fantasy_scores_weekly_effective e
                   ON e.game_id = d.game_id AND e.player_id = d.nba_player_id
                   WHERE d.season_id = %s AND d.league_id = %s AND d.fantasy_score IS DISTINCT FROM e.fantasy_score""", (sid, league))
    n = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM nba_lock_decisions WHERE season_id = %s AND league_id = %s", (sid, league))
    total = cur.fetchone()[0]
    line("FAIL" if n else "PASS", "lock scores", f"{n} of {total} stored lock score(s) differ from the official score" if n else f"all {total} stored lock scores equal the official score")

    # 5 lock flags
    cur.execute("""SELECT f.week_number, f.roster_id, f.slot, d.player_name, d.game_id, m.nba_game_id
                   FROM sleeper_lock_flags f JOIN sleeper_game_map m ON m.sleeper_game_id = f.sleeper_game_id AND m.nba_game_id IS NOT NULL
                   LEFT JOIN nba_lock_decisions d ON d.league_id = f.league_id AND d.week_number = f.week_number
                        AND d.roster_id = f.roster_id AND d.sleeper_player_id = f.sleeper_player_id
                   WHERE f.league_id = %s AND d.game_id IS DISTINCT FROM m.nba_game_id
                   ORDER BY 1, 2""", (league,))
    bad = cur.fetchall()
    cur.execute("SELECT count(*) FROM sleeper_lock_flags WHERE league_id = %s", (league,))
    nflags = cur.fetchone()[0]
    if bad:
        line("FAIL", "lock flags", f"{len(bad)} of {nflags} flag(s) disagree with the stored lock: " + "; ".join(f"wk {w} roster {r} {s}" for w, r, s, *_ in bad[:6]))
    else:
        line("PASS", "lock flags", f"all {nflags} flags match the stored locks")

    # 6 closed weeks
    cur.execute("""SELECT week_number, count(*), count(*) FILTER (WHERE reconciled),
                          count(*) FILTER (WHERE checksum_status IN ('unique','offset','ruling')),
                          count(*) FILTER (WHERE checksum_status = 'ruling'),
                          count(*) FILTER (WHERE reconciled AND explained_diff <> 0)
                   FROM nba_week_reconciliation WHERE season_id = %s AND league_id = %s GROUP BY 1 ORDER BY 1""", (sid, league))
    weeks = cur.fetchall()
    for w, teams, ok, proven, ruled, explained in weeks:
        extra = []
        if explained:
            extra.append(f"{explained} explained by a stat correction or ruling")
        if ruled:
            extra.append(f"{ruled} league ruling(s)")
        line("PASS" if ok == teams else "FAIL", f"week {w}", f"{ok}/{teams} teams add up to Sleeper's total; lock choice proven for {proven}/{teams}"
             + (f" ({'; '.join(extra)})" if extra else ""))
    if not weeks:
        line("NOTE", "closed weeks", "no closed week stored yet")

    # 7 lock rows
    cur.execute("""SELECT week_number, roster_id, count(*), count(*) FILTER (WHERE source IN ('unverified','checksum_ambiguous'))
                   FROM nba_lock_decisions WHERE season_id = %s AND league_id = %s AND week_number IN
                        (SELECT week_number FROM nba_week_reconciliation WHERE season_id = %s AND league_id = %s)
                   GROUP BY 1, 2 ORDER BY 1, 2""", (sid, league, sid, league))
    short = [(w, r, n) for w, r, n, _u in cur.fetchall() if n != 9]
    cur.execute("""SELECT week_number, roster_id, slot, player_name, source FROM nba_lock_decisions
                   WHERE season_id = %s AND league_id = %s AND source IN ('unverified','checksum_ambiguous') ORDER BY 1, 2, 3""", (sid, league))
    unproven = cur.fetchall()
    line("FAIL" if short else "PASS", "lock rows", ("team-weeks without 9 lock rows: " + "; ".join(f"wk {w} roster {r} has {n}" for w, r, n in short)) if short else "every closed team-week has 9 lock rows")
    if unproven:
        line("NOTE", "unproven", f"{len(unproven)} lock(s) not proven by a total, scores still match: " + "; ".join(f"wk {w} roster {r} {s} {p}" for w, r, s, p, _src in unproven[:8]))

    # 8 current week
    cur.execute("""SELECT week_number, week_start_date, week_end_date FROM fantasy_weeks WHERE season_id = %s AND %s BETWEEN week_start_date AND week_end_date""",
                (sid, date.today()))
    cw = cur.fetchone()
    if cw:
        cur.execute("SELECT count(DISTINCT game_id) FROM game_logs WHERE season_id = %s AND game_date BETWEEN %s AND %s", (sid, cw[1], cw[2]))
        g = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM nba_lock_decisions WHERE season_id = %s AND league_id = %s AND week_number = %s", (sid, league, cw[0]))
        l = cur.fetchone()[0]
        line("NOTE", f"week {cw[0]}", f"in progress ({cw[1]} to {cw[2]}): {g} game(s) loaded, {l} lock(s) recorded so far; totals are checked when the week closes")
    cur.close(); conn.close()
    print(f"\n{'ALL CLEAR' if not failures else str(failures) + ' CHECK(S) FAILED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
