"""
scripts/season_opener_preflight.py

Read-only validator for the 2026-27 season opener (and any later week).
Run it BEFORE and AFTER the Sleeper sync to confirm the APIs landed
good data. Never writes: the session is set read-only.

Each check is isolated in its own try/except, so a wrong column guess
gives SKIP (with the DB error) instead of crashing the whole run.
Status levels: OK, INFO, WARN, FAIL, SKIP. Exit code 1 if any FAIL.

ASSUMPTIONS (guessed, not confirmed -- run with --probe to see real columns):
    sleeper_transactions: status, type, created
    fantasy_weeks: season_id
    Sleeper uses '0' for an empty starter slot.

Run (from project root):
    python scripts/season_opener_preflight.py --probe
    python scripts/season_opener_preflight.py --season-id 22026 --week 1
    python scripts/season_opener_preflight.py --week 1 --since-minutes 30
"""

import argparse
import json
import sys

from db_connection import get_connection

EXPECTED_SLOTS = ["PG", "SG", "G", "SF", "PF", "F", "C", "UTIL", "UTIL",
                  "BN", "BN", "BN", "BN", "BN", "BN"]
EXPECTED_ROSTERS = 10
EXPECTED_STARTERS = 9
EXPECTED_WEEKS = 24

TALLY = {"OK": 0, "INFO": 0, "WARN": 0, "FAIL": 0, "SKIP": 0}


def report(status, label, detail=""):
    TALLY[status] += 1
    line = f"[{status:<4}] {label}"
    if detail:
        line += f" -- {detail}"
    print(line)


def run_query(conn, sql, params=None):
    """Returns (rows, cols, err). Rolls back on error so later checks still work."""
    cur = conn.cursor()
    try:
        cur.execute(sql, params or ())
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
        return rows, cols, None
    except Exception as e:  # noqa: BLE001 -- deliberate: SKIP, don't crash
        conn.rollback()
        return None, None, str(e).strip().splitlines()[0]
    finally:
        cur.close()


def section(title):
    print(f"\n=== {title} ===")


def scalar(conn, sql, params=None):
    rows, _, err = run_query(conn, sql, params)
    if err:
        return None, err
    return (rows[0][0] if rows else None), None


# =========================
# 1. League / season config
# =========================

def check_league(conn, season_id):
    section("1. League / season config")
    rows, _, err = run_query(conn, "SELECT league_id FROM sleeper_current_league")
    if err:
        report("SKIP", "sleeper_current_league", err)
        return None
    if not rows:
        report("FAIL", "sleeper_current_league", "no rows -- previous_league_id chain broken")
        return None
    league_id = rows[0][0]
    report("OK", "current league resolved", league_id)

    rows, cols, err = run_query(conn, """
        SELECT season, status, total_rosters, roster_positions, scoring_settings,
               previous_league_id, EXTRACT(EPOCH FROM (now() - synced_at))/60.0
        FROM sleeper_leagues WHERE league_id = %s
    """, (league_id,))
    if err:
        report("SKIP", "sleeper_leagues row", err)
        return league_id
    if not rows:
        report("FAIL", "sleeper_leagues row", "current league has no row")
        return league_id
    season, status, total, positions, scoring, prev_id, age_min = rows[0]

    expected_season = str(season_id)[1:]
    report("OK" if str(season) == expected_season else "FAIL",
           "season matches --season-id", f"league season={season}, expected {expected_season}")
    report("OK" if status == "in_season" else "WARN", "league status", str(status))
    report("OK" if total == EXPECTED_ROSTERS else "FAIL", "total_rosters", str(total))
    pos = list(positions) if positions is not None else []
    report("OK" if pos == EXPECTED_SLOTS else "FAIL", "roster_positions",
           "matches 15-slot layout" if pos == EXPECTED_SLOTS else f"got {pos}")
    if age_min is not None:
        age = float(age_min)
        report("OK" if age < 24 * 60 else "WARN", "league synced_at age", f"{age:.0f} min")

    # scoring settings drift vs previous season
    if prev_id:
        prev, err = scalar(conn, "SELECT scoring_settings FROM sleeper_leagues WHERE league_id = %s", (prev_id,))
        if err:
            report("SKIP", "scoring_settings vs previous season", err)
        elif prev is None:
            report("INFO", "scoring_settings vs previous season", "previous league row missing")
        else:
            a = scoring if isinstance(scoring, dict) else json.loads(scoring)
            b = prev if isinstance(prev, dict) else json.loads(prev)
            diff = {k: (b.get(k), a.get(k)) for k in set(a) | set(b) if a.get(k) != b.get(k)}
            if diff:
                report("WARN", "scoring_settings changed vs last season",
                       "; ".join(f"{k}: {v[0]} -> {v[1]}" for k, v in sorted(diff.items())[:10]))
            else:
                report("OK", "scoring_settings identical to last season")

    rows, cols, err = run_query(conn, "SELECT * FROM current_season_config")
    if err:
        report("SKIP", "current_season_config", err)
    else:
        report("INFO", "current_season_config", f"{cols} -> {rows[:2]}")
    return league_id


# =========================
# 2. Rosters
# =========================

def check_rosters(conn, league_id):
    section("2. Rosters (roster_ownership)")
    rows, _, err = run_query(conn, """
        SELECT roster_id, COUNT(*) FROM roster_ownership
        WHERE league_id = %s GROUP BY roster_id ORDER BY roster_id
    """, (league_id,))
    if err:
        report("SKIP", "roster_ownership", err)
        return
    report("OK" if len(rows) == EXPECTED_ROSTERS else "FAIL",
           "rosters present", f"{len(rows)} of {EXPECTED_ROSTERS}")
    small = [(r, n) for r, n in rows if n < 9]
    report("OK" if not small else "FAIL", "each roster >= 9 players",
           "all fine" if not small else f"short: {small}")
    report("INFO", "roster sizes", ", ".join(f"{r}:{n}" for r, n in rows))

    n, err = scalar(conn, "SELECT COUNT(*) FROM roster_ownership WHERE league_id = %s AND owner_name IS NULL", (league_id,))
    if err:
        report("SKIP", "null owner_name", err)
    else:
        report("OK" if n == 0 else "FAIL", "null owner_name", str(n))

    rows, _, err = run_query(conn, """
        SELECT sleeper_player_id, player_name FROM roster_ownership
        WHERE league_id = %s AND nba_player_id IS NULL ORDER BY player_name
    """, (league_id,))
    if err:
        report("SKIP", "unmatched crosswalk players", err)
    elif rows:
        names = ", ".join(f"{r[1] or r[0]}" for r in rows[:15])
        report("WARN", "rostered players with no nba_player_id", f"{len(rows)}: {names}")
    else:
        report("OK", "all rostered players matched to nba_player_id")

    rows, _, err = run_query(conn, """
        SELECT ro.player_name FROM roster_ownership ro
        LEFT JOIN sleeper_player_fantasy_positions fp ON fp.sleeper_player_id = ro.sleeper_player_id
        WHERE ro.league_id = %s AND ro.nba_player_id IS NOT NULL AND fp.sleeper_player_id IS NULL
    """, (league_id,))
    if err:
        report("SKIP", "players missing fantasy positions", err)
    elif rows:
        report("WARN", "matched players with no fantasy_positions rows",
               f"{len(rows)}: {', '.join(str(r[0]) for r in rows[:15])}")
    else:
        report("OK", "all matched players have fantasy_positions")


# =========================
# 3. Matchups
# =========================

def check_matchups(conn, league_id, week, since_minutes):
    section(f"3. Matchups (week {week})")
    rows, _, err = run_query(conn, """
        SELECT roster_id, matchup_id, cardinality(starters),
               starters, EXTRACT(EPOCH FROM (now() - synced_at))/60.0
        FROM sleeper_matchups WHERE league_id = %s AND week = %s ORDER BY roster_id
    """, (league_id, week))
    if err:
        report("SKIP", "sleeper_matchups", err)
        return
    report("OK" if len(rows) == EXPECTED_ROSTERS else "FAIL",
           "matchup rows", f"{len(rows)} of {EXPECTED_ROSTERS}")
    if not rows:
        return

    counts = {}
    for _, mid, *_ in rows:
        counts[mid] = counts.get(mid, 0) + 1
    bad = {m: c for m, c in counts.items() if c != 2}
    report("OK" if not bad else "FAIL", "matchups paired 2-to-2",
           "all pairs" if not bad else f"bad matchup_id counts: {bad}")

    short = [(r, n) for r, _, n, *_ in rows if n != EXPECTED_STARTERS]
    report("OK" if not short else "FAIL", f"starters length == {EXPECTED_STARTERS}",
           "all fine" if not short else f"off: {short}")

    empty = [(r, sum(1 for s in (st or []) if s in ("0", "", None))) for r, _, _, st, _ in rows]
    empty = [(r, n) for r, n in empty if n]
    report("OK" if not empty else "WARN", "empty starter slots",
           "none" if not empty else f"roster:count {empty}")

    stale = [r for r, *_, age in rows if age is not None and since_minutes and float(age) > since_minutes]
    if since_minutes:
        report("OK" if not stale else "WARN", f"synced within {since_minutes} min",
               "all fresh" if not stale else f"stale rosters: {stale}")
    ages = [float(r[-1]) for r in rows if r[-1] is not None]
    if ages:
        report("INFO", "matchup sync age (min)", f"min {min(ages):.0f}, max {max(ages):.0f}")

    rows2, _, err = run_query(conn, """
        SELECT m.roster_id, s.sid
        FROM sleeper_matchups m
        CROSS JOIN LATERAL unnest(m.starters) AS s(sid)
        LEFT JOIN roster_ownership ro
            ON ro.league_id = m.league_id AND ro.roster_id = m.roster_id AND ro.sleeper_player_id = s.sid
        WHERE m.league_id = %s AND m.week = %s AND s.sid NOT IN ('0', '') AND ro.sleeper_player_id IS NULL
    """, (league_id, week))
    if err:
        report("SKIP", "starters on roster", err)
    elif rows2:
        report("FAIL", "starters not on that roster per roster_ownership",
               f"{len(rows2)}: {rows2[:8]}")
    else:
        report("OK", "every starter is on that roster per roster_ownership")

    rows3, _, err = run_query(conn, """
        SELECT COUNT(*), COUNT(DISTINCT roster_id), MAX(synced_at)
        FROM sleeper_matchup_points_snapshots WHERE league_id = %s AND week = %s
    """, (league_id, week))
    if err:
        report("SKIP", "points snapshots", err)
    else:
        n, r, mx = rows3[0]
        report("INFO", "sleeper_matchup_points_snapshots",
               f"{n} rows, {r} rosters, latest {mx} (0 expected before games finish)")


# =========================
# 4. Transactions
# =========================

def check_transactions(conn, league_id):
    section("4. Transactions")
    rows, _, err = run_query(conn, """
        SELECT type, status, COUNT(*) FROM sleeper_transactions
        WHERE league_id = %s GROUP BY type, status ORDER BY 3 DESC
    """, (league_id,))
    if err:
        report("SKIP", "transaction counts (column guess?)", err)
    elif not rows:
        report("INFO", "transactions this league", "none yet")
    else:
        report("INFO", "type/status counts", "; ".join(f"{t}/{s}={n}" for t, s, n in rows))

    mx, err = scalar(conn, "SELECT MAX(created) FROM sleeper_transactions WHERE league_id = %s", (league_id,))
    if err:
        report("SKIP", "latest transaction", err)
    elif mx is None:
        report("INFO", "latest transaction", "none")
    else:
        shown = mx
        if isinstance(mx, (int, float)) and mx > 1e11:  # ms epoch
            from datetime import datetime, timezone
            shown = datetime.fromtimestamp(mx / 1000, tz=timezone.utc).isoformat()
        report("INFO", "latest transaction created", str(shown))


# =========================
# 5. nba_api side
# =========================

def check_nba_side(conn, season_id):
    section("5. nba_api-side tables (expected empty/low tonight)")
    checks = [
        ("game_logs rows this season", "SELECT COUNT(DISTINCT game_id) FROM game_logs WHERE season_id = %s", (season_id,)),
        ("team_schedule", "SELECT COUNT(*) FROM team_schedule", ()),
        ("game_fantasy_scores_weekly_effective rows",
         "SELECT COUNT(*) FROM game_fantasy_scores_weekly_effective WHERE season_id = %s", (season_id,)),
        ("gap_reasons", "SELECT COUNT(*) FROM gap_reasons", ()),
    ]
    for label, sql, params in checks:
        n, err = scalar(conn, sql, params)
        if err:
            report("SKIP", label, err)
        else:
            report("INFO", label, str(n))

    n, err = scalar(conn, "SELECT COUNT(*) FROM fantasy_weeks WHERE season_id = %s", (season_id,))
    if err:
        report("SKIP", "fantasy_weeks (column guess?)", err)
    else:
        report("OK" if n == EXPECTED_WEEKS else "FAIL", "fantasy_weeks rows",
               f"{n} (expected {EXPECTED_WEEKS}; regen schema/fantasy_weeks.sql if wrong)")


# =========================
# 6. Probe
# =========================

PROBE_TABLES = ["current_season_config", "sleeper_transactions", "fantasy_weeks",
                "gap_reasons", "team_schedule", "game_logs", "roster_ownership"]


def probe(conn):
    section("Probe: real column names")
    for t in PROBE_TABLES:
        rows, _, err = run_query(conn, """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = %s ORDER BY ordinal_position
        """, (t,))
        if err:
            report("SKIP", t, err)
        elif not rows:
            report("WARN", t, "not found in information_schema")
        else:
            report("INFO", t, ", ".join(r[0] for r in rows))


def main():
    p = argparse.ArgumentParser(description="Read-only season-opener preflight validator.")
    p.add_argument("--season-id", default="22026")
    p.add_argument("--week", type=int, default=1)
    p.add_argument("--since-minutes", type=int, default=None,
                   help="WARN if matchup rows were synced longer ago than this")
    p.add_argument("--probe", action="store_true", help="print real column names for guessed tables")
    args = p.parse_args()

    conn = get_connection()
    conn.set_session(readonly=True)

    if args.probe:
        probe(conn)
    league_id = check_league(conn, args.season_id)
    if league_id:
        check_rosters(conn, league_id)
        check_matchups(conn, league_id, args.week, args.since_minutes)
        check_transactions(conn, league_id)
    check_nba_side(conn, args.season_id)
    conn.close()

    print("\n--- Summary ---")
    print("  ".join(f"{k}={v}" for k, v in TALLY.items()))
    sys.exit(1 if TALLY["FAIL"] else 0)


if __name__ == "__main__":
    main()
