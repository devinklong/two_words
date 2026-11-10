"""Load every completed draft of every league in sleeper_leagues (startup and rookie drafts) into sleeper_drafts / sleeper_draft_picks.

First piece of the pick-value work (scripts/pick_value.py): to price a draft pick we need to know who each slot actually turned into.
Two Sleeper endpoints per league: /league/{id}/drafts (one row per draft) and /draft/{id}/picks (one row per pick). Players are matched
to NBA ids through sleeper_player_crosswalk; a pick with no match is kept with nba_player_id NULL so the gap shows up instead of
vanishing. Traded picks (/league/{id}/traded_picks) are not loaded: this records who WAS drafted at each slot, not who owned the pick.

Additive and safe to re-run: rows are upserted by (draft_id, pick_no); a draft that is not complete yet is skipped.

Usage (from ~/projects/two_words):  python scripts/sleeper/ingest_drafts.py [--league-id ID] [--dry-run]
"""
import argparse
import json
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.append(str(Path(__file__).resolve().parents[1]))

BASE = "https://api.sleeper.app/v1"
DDL = [
    """CREATE TABLE IF NOT EXISTS sleeper_drafts (
        draft_id   text PRIMARY KEY,
        league_id  text NOT NULL,
        season     text,
        draft_type text,
        status     text,
        rounds     integer,
        teams      integer,
        start_time timestamptz,
        loaded_at  timestamptz NOT NULL DEFAULT now())""",
    """CREATE TABLE IF NOT EXISTS sleeper_draft_picks (
        draft_id          text    NOT NULL,
        league_id         text    NOT NULL,
        season            text,
        round             integer,
        pick_no           integer NOT NULL,
        draft_slot        integer,
        roster_id         integer,
        sleeper_player_id text,
        nba_player_id     integer,
        player_name       text,
        position          text,
        loaded_at         timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (draft_id, pick_no))""",
]


def get(path, tries=3):
    last = None
    for attempt in range(tries):
        try:
            req = Request(f"{BASE}{path}", headers={"User-Agent": "two_words-drafts"})
            with urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except Exception as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise SystemExit(f"Sleeper request {path} failed {tries} times ({last})")


def pick_row(draft_id, league_id, season, p, xwalk):
    """One /draft/{id}/picks entry -> a sleeper_draft_picks row."""
    meta = p.get("metadata") or {}
    pid = p.get("player_id")
    name = " ".join(x for x in (meta.get("first_name"), meta.get("last_name")) if x) or None
    return (draft_id, league_id, season, p.get("round"), p["pick_no"], p.get("draft_slot"), p.get("roster_id"),
            str(pid) if pid else None, xwalk.get(str(pid)) if pid else None, name, meta.get("position"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--league-id", help="one league (default: every league in sleeper_leagues)")
    ap.add_argument("--dry-run", action="store_true", help="fetch and report, write nothing")
    args = ap.parse_args()

    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT league_id, season FROM sleeper_leagues ORDER BY season")
    leagues = [(l, s) for l, s in cur.fetchall() if not args.league_id or l == args.league_id]
    cur.execute("SELECT sleeper_player_id::text, nba_player_id FROM sleeper_player_crosswalk WHERE nba_player_id IS NOT NULL")
    xwalk = dict(cur.fetchall())
    if not args.dry_run:
        for stmt in DDL:
            cur.execute(stmt)

    for league_id, season in leagues:
        for d in get(f"/league/{league_id}/drafts") or []:
            did, status = d["draft_id"], d.get("status")
            st = d.get("settings") or {}
            if status != "complete":
                print(f"{season} league {league_id} draft {did}: status '{status}', skipped")
                continue
            picks = get(f"/draft/{did}/picks") or []
            rows = [pick_row(did, league_id, season, p, xwalk) for p in picks]
            unmatched = [r[9] or r[7] for r in rows if r[8] is None]
            print(f"{season} draft {did} ({d.get('type')}, {st.get('rounds')} rounds, {st.get('teams')} teams): {len(rows)} picks, "
                  f"{len(unmatched)} without an NBA id" + (f" ({', '.join(map(str, unmatched[:5]))}{' ...' if len(unmatched) > 5 else ''})" if unmatched else ""))
            if args.dry_run:
                continue
            cur.execute("""INSERT INTO sleeper_drafts (draft_id, league_id, season, draft_type, status, rounds, teams, start_time)
                           VALUES (%s,%s,%s,%s,%s,%s,%s, to_timestamp(%s / 1000.0))
                           ON CONFLICT (draft_id) DO UPDATE SET status = EXCLUDED.status, rounds = EXCLUDED.rounds,
                               teams = EXCLUDED.teams, loaded_at = now()""",
                        (did, league_id, season, d.get("type"), status, st.get("rounds"), st.get("teams"), d.get("start_time")))
            cur.executemany("""INSERT INTO sleeper_draft_picks (draft_id, league_id, season, round, pick_no, draft_slot, roster_id,
                                   sleeper_player_id, nba_player_id, player_name, position)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (draft_id, pick_no) DO UPDATE SET nba_player_id = EXCLUDED.nba_player_id,
                                   sleeper_player_id = EXCLUDED.sleeper_player_id, player_name = EXCLUDED.player_name,
                                   position = EXCLUDED.position, roster_id = EXCLUDED.roster_id, loaded_at = now()""", rows)
            conn.commit()
        time.sleep(0.5)
    cur.close(); conn.close()


if __name__ == "__main__":
    main()
