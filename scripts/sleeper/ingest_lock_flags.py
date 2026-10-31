"""Read Sleeper's own lock flags every run and store them, so the database knows each day which slots are locked and to what game.

Sleeper's website and app run on a GraphQL endpoint that needs no login for this read. The query matchup_legs_raw returns, per
roster, the nine starters and starters_games: {sleeper_player_id: sleeper_game_id} for every starter the manager has explicitly
locked. A starter that is not listed is not locked (at week end Sleeper banks his last game for him). We proved this against all
90 locks recovered for week 1: every non-last-game lock was listed.

Sleeper's game ids are not NBA ids, so this script also reads Sleeper's scores query (date, home and away team) and matches
each game to team_schedule by date and the two teams. Tables, both created here if missing:
  sleeper_game_map   one row per Sleeper game id -> NBA game_id (NULL until the game can be matched)
  sleeper_lock_flags one row per locked starter per week, with first_seen_at (when the lock first showed up) and last_seen_at

infer_lock_decisions.py turns the flags into rows of nba_lock_decisions (source sleeper_flag), which win over every inferred
source. Nothing is written to Sleeper. A failed read is reported loudly but does not stop the run.

Usage (from ~/projects/two_words):  python scripts/sleeper/ingest_lock_flags.py [--season-id 22026] [--week N] [--dry-run]
"""
import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.append(str(Path(__file__).resolve().parents[1]))

URL = "https://sleeper.com/graphql"
HEADERS = {"Content-Type": "application/json", "Origin": "https://sleeper.com", "User-Agent": "Mozilla/5.0 (Macintosh) two_words/1.0"}
SLOT_ORDER = ["PG", "SG", "G", "SF", "PF", "F", "C", "UTIL1", "UTIL2"]
ALIASES = {"GS": "GSW", "NY": "NYK", "NO": "NOP", "SA": "SAS", "PHO": "PHX", "UTAH": "UTA", "WSH": "WAS", "BRK": "BKN", "CHO": "CHA"}
ABBR_COLUMNS = ("abbreviation", "team_abbreviation", "tricode", "abbr")
LOOKBACK_DAYS = 7   # a finished week is still read for a week, in case a flag appears late

DDL = [
    """CREATE TABLE IF NOT EXISTS sleeper_game_map (
        sleeper_game_id text PRIMARY KEY, game_date date NOT NULL, away_team text, home_team text,
        nba_game_id text, scheduled timestamptz, status text, updated_at timestamptz NOT NULL DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS sleeper_lock_flags (
        league_id text NOT NULL, season_id text NOT NULL, week_number integer NOT NULL, roster_id integer NOT NULL,
        slot text NOT NULL, sleeper_player_id text NOT NULL, sleeper_game_id text NOT NULL,
        first_seen_at timestamptz NOT NULL DEFAULT now(), last_seen_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (league_id, week_number, roster_id, sleeper_player_id, sleeper_game_id))""",
]


def gql(query):
    last = None
    for attempt in range(1, 5):
        try:
            with urlopen(Request(URL, data=json.dumps({"query": query}).encode(), headers=HEADERS), timeout=40) as resp:
                out = json.load(resp)
            if out.get("errors") or not out.get("data"):
                raise RuntimeError(json.dumps(out.get("errors"))[:300])
            return out["data"]
        except Exception as e:   # network, HTTP or GraphQL error: retry with a pause
            last = e
            time.sleep(3 * attempt)
    raise RuntimeError(f"Sleeper GraphQL failed after 4 tries: {last}")


def norm(abbr):
    a = (abbr or "").upper()
    return ALIASES.get(a, a)


def team_lookup(cur):
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'teams'")
    cols = {r[0] for r in cur.fetchall()}
    col = next((c for c in ABBR_COLUMNS if c in cols), None)
    if not col:
        raise SystemExit(f"teams table has no abbreviation column (columns: {sorted(cols)}); tell Claude")
    cur.execute(f"SELECT {col}, team_id FROM teams")
    return {norm(a): t for a, t in cur.fetchall() if a}


def match_nba_game(cur, season_id, teams, home, away, day):
    h, a = teams.get(norm(home)), teams.get(norm(away))
    if h is None or a is None:
        return None
    cur.execute("""SELECT game_id FROM team_schedule WHERE season_id = %s AND team_id = %s AND opponent_team_id = %s
                   AND game_date BETWEEN %s AND %s ORDER BY abs(game_date - %s::date) LIMIT 1""",
                (str(season_id), h, a, day - timedelta(days=1), day + timedelta(days=1), day))
    row = cur.fetchone()
    return row[0] if row else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season-id", default="22026")
    ap.add_argument("--week", type=int)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    for ddl in DDL:
        cur.execute(ddl)
    cur.execute("SELECT league_id FROM sleeper_current_league")
    league = cur.fetchone()[0]
    today = date.today()
    cur.execute("""SELECT week_number, week_start_date, week_end_date FROM fantasy_weeks
                   WHERE season_id = %s AND week_start_date <= %s AND week_end_date >= %s ORDER BY week_number""",
                (args.season_id, today, today - timedelta(days=LOOKBACK_DAYS)))
    weeks = [w for w in cur.fetchall() if not args.week or w[0] == args.week]
    if not weeks:
        print("no fantasy week to read today")
        return
    teams = team_lookup(cur)
    sleeper_season = str(args.season_id)[1:]
    failed = False

    for week, start, end in weeks:
        try:
            legs = gql(f'{{ matchup_legs_raw(round: {week}, league_id: "{league}") {{ roster_id starters starters_games }} }}')["matchup_legs_raw"]
        except Exception as e:
            print(f"week {week}: FAILED to read Sleeper's lock flags: {e}")
            failed = True
            continue
        if not legs or any(len(r.get("starters") or []) != len(SLOT_ORDER) for r in legs):
            print(f"week {week}: unexpected reply shape ({len(legs or [])} rosters); nothing stored")
            failed = True
            continue

        flags = []   # (roster, slot, sleeper_player_id, sleeper_game_id)
        for r in legs:
            sg = r.get("starters_games") or {}
            for i, sid in enumerate(r["starters"]):
                gid = sg.get(str(sid))
                if gid:
                    flags.append((r["roster_id"], SLOT_ORDER[i], str(sid), str(gid)))
        new = 0
        if not args.dry_run:
            for roster, slot, sid, gid in flags:
                cur.execute("""INSERT INTO sleeper_lock_flags (league_id, season_id, week_number, roster_id, slot, sleeper_player_id, sleeper_game_id)
                               VALUES (%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (league_id, week_number, roster_id, sleeper_player_id, sleeper_game_id)
                               DO UPDATE SET last_seen_at = now(), slot = EXCLUDED.slot
                               RETURNING (xmax = 0)""", (league, str(args.season_id), week, roster, slot, sid, gid))
                new += 1 if cur.fetchone()[0] else 0

        wanted = {gid for _, _, _, gid in flags}
        cur.execute("SELECT sleeper_game_id FROM sleeper_game_map WHERE nba_game_id IS NOT NULL")
        have = {r[0] for r in cur.fetchall()}
        missing = wanted - have
        if missing and not args.dry_run:
            day = start
            while day <= min(end, today + timedelta(days=1)) and missing:
                try:
                    scores = gql(f'{{ scores(sport: "nba", season_type: "regular", season: "{sleeper_season}", date: "{day}") '
                                 f'{{ game_id date start_time status metadata }} }}')["scores"] or []
                except Exception as e:
                    print(f"week {week}: FAILED to read Sleeper's games for {day}: {e}")
                    failed = True
                    break
                for s in scores:
                    m = s.get("metadata") or {}
                    away, home = (m.get("away_team") or {}).get("team"), (m.get("home_team") or {}).get("team")
                    gdate = date.fromisoformat(s["date"])
                    nba = match_nba_game(cur, args.season_id, teams, home, away, gdate)
                    cur.execute("""INSERT INTO sleeper_game_map (sleeper_game_id, game_date, away_team, home_team, nba_game_id, scheduled, status)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                                   ON CONFLICT (sleeper_game_id) DO UPDATE SET nba_game_id = COALESCE(EXCLUDED.nba_game_id, sleeper_game_map.nba_game_id),
                                       status = EXCLUDED.status, updated_at = now()""",
                                (str(s["game_id"]), gdate, away, home, nba, m.get("scheduled"), s.get("status")))
                    if nba:
                        missing.discard(str(s["game_id"]))
                day += timedelta(days=1)
        unmapped = [] if args.dry_run else sorted(missing)
        print(f"week {week}: {len(flags)} locked starter(s) on {len(legs)} rosters, {new} new"
              + (f"; {len(unmapped)} Sleeper game(s) could not be matched to an NBA game: {', '.join(unmapped)}" if unmapped else ""))
        if unmapped:
            failed = True
        if not args.dry_run:
            cur.execute("""SELECT roster_id, sleeper_player_id, count(*) FROM sleeper_lock_flags
                           WHERE league_id = %s AND week_number = %s GROUP BY 1,2 HAVING count(*) > 1""", (league, week))
            for roster, sid, n in cur.fetchall():
                print(f"  WARNING roster {roster} player {sid} has {n} different locked games this week")

    if not args.dry_run:
        conn.commit()
    else:
        print("(dry run, nothing written)")
    cur.close(); conn.close()
    if failed:
        print("lock flags: some reads failed or could not be matched (see above); the run continues")


if __name__ == "__main__":
    main()
