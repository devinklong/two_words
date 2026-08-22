"""
scripts/sleeper/backfill_sleeper_league.py

Backfills both Sleeper seasons (2024, 2025) starting from the current
league_id, following previous_league_id to find the prior season
automatically. Populates sleeper_leagues, sleeper_rosters, sleeper_users,
sleeper_matchups (weeks 1-24), sleeper_transactions (rounds 1-24).
No auth needed -- Sleeper's API is public read-only.

ALL-OR-NOTHING (fixed 8/19/26, docs/architecture_risks.md #6): the
whole run -- every season, every step -- is one transaction. A single
commit() happens only after everything succeeds; any exception triggers
a full rollback, leaving the DB exactly as it was before the run
started. Previously each step (league/rosters/users/matchups/
transactions) committed separately, so a run interrupted partway
through left a real but incomplete state with nothing flagging that it
hadn't finished. Trade-off worth knowing: this holds one DB transaction
open for the full run duration (multiple minutes, given REQUEST_DELAY
across ~24 weeks x 2 seasons of API calls) -- fine for an infrequent
backfill script; a high-frequency or very long-running job would want
checkpointing instead of one giant transaction.

FIXED 8/21/26: upsert_transactions(cur, league_id) was being called
TWICE per season -- once before upsert_matchups (result discarded,
never printed, pure wasted API round-trip) and again after. Removed
the first, dead call.

CENTRALIZED 8/22/26 (docs/architecture_risks.md #8): MAX_WEEK now
imported from scripts/constants.py instead of redefined here -- no
behavior change, same literal value as before.

Usage: python scripts/backfill_sleeper_league.py
"""

import sys
import time
import json
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from db_connection import get_connection
from constants import MAX_WEEK

import requests

BASE_URL = "https://api.sleeper.app/v1"
CURRENT_LEAGUE_ID = "1347268546727514112"
REQUEST_DELAY = 0.3  # be a reasonable citizen of a free public API


def fetch(path):
    url = f"{BASE_URL}{path}"
    resp = requests.get(url)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY)
    return resp.json()


def get_season_chain(start_league_id):
    """Follows previous_league_id backward from the given league to find all linked seasons."""
    chain = []
    league_id = start_league_id
    while league_id:
        league = fetch(f"/league/{league_id}")
        chain.append(league)
        league_id = league.get("previous_league_id")
    return chain


def upsert_league(cur, league):
    cur.execute("""
        INSERT INTO sleeper_leagues
            (league_id, previous_league_id, season, name, status, total_rosters,
             roster_positions, scoring_settings, settings)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (league_id) DO UPDATE SET
            previous_league_id = EXCLUDED.previous_league_id,
            season = EXCLUDED.season, name = EXCLUDED.name, status = EXCLUDED.status,
            total_rosters = EXCLUDED.total_rosters, roster_positions = EXCLUDED.roster_positions,
            scoring_settings = EXCLUDED.scoring_settings, settings = EXCLUDED.settings,
            synced_at = now();
    """, (
        league["league_id"], league.get("previous_league_id"), league.get("season"),
        league.get("name"), league.get("status"), league.get("total_rosters"),
        league.get("roster_positions"), json.dumps(league.get("scoring_settings")),
        json.dumps(league.get("settings")),
    ))


def upsert_rosters(cur, league_id):
    rosters = fetch(f"/league/{league_id}/rosters")
    for r in rosters:
        cur.execute("""
            INSERT INTO sleeper_rosters (league_id, roster_id, owner_id, players, starters, settings)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (league_id, roster_id) DO UPDATE SET
                owner_id = EXCLUDED.owner_id, players = EXCLUDED.players,
                starters = EXCLUDED.starters, settings = EXCLUDED.settings, synced_at = now();
        """, (
            league_id, r["roster_id"], r.get("owner_id"),
            r.get("players") or [], r.get("starters") or [], json.dumps(r.get("settings")),
        ))
    return len(rosters)


def upsert_users(cur, league_id):
    users = fetch(f"/league/{league_id}/users")
    for u in users:
        cur.execute("""
            INSERT INTO sleeper_users (league_id, user_id, display_name, is_owner, metadata)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (league_id, user_id) DO UPDATE SET
                display_name = EXCLUDED.display_name, is_owner = EXCLUDED.is_owner,
                metadata = EXCLUDED.metadata, synced_at = now();
        """, (
            league_id, u["user_id"], u.get("display_name"),
            u.get("is_owner", False), json.dumps(u.get("metadata")),
        ))
    return len(users)


def upsert_matchups(cur, league_id, weeks=None):
    """Stores roster/lineup STRUCTURE only. players_points and points
    (Sleeper's own computed fantasy totals) are deliberately dropped
    here, at ingestion -- never inserted, never available to be joined
    against by mistake later. This project computes its own
    fantasy_score; Sleeper is a source for who played on which roster,
    never for how many points anyone scored.

    weeks: iterable of week numbers to sync. Defaults to the full
    1..MAX_WEEK range (full backfill); the daily sync passes a narrow
    list (e.g. just the current week) instead."""
    if weeks is None:
        weeks = range(1, MAX_WEEK + 1)

    total = 0
    for week in weeks:
        matchups = fetch(f"/league/{league_id}/matchups/{week}")
        if not matchups:
            continue
        for m in matchups:
            cur.execute("""
                INSERT INTO sleeper_matchups
                    (league_id, week, roster_id, matchup_id, players, starters)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (league_id, week, roster_id) DO UPDATE SET
                    matchup_id = EXCLUDED.matchup_id,
                    players = EXCLUDED.players, starters = EXCLUDED.starters,
                    synced_at = now();
            """, (
                league_id, week, m["roster_id"], m.get("matchup_id"),
                m.get("players") or [], m.get("starters") or [],
            ))
            total += 1
    return total


def upsert_transactions(cur, league_id, rounds=None):
    """rounds: iterable of round/week numbers to sync. Defaults to the
    full 1..MAX_WEEK range (full backfill); the daily sync passes a
    narrow list (current + prior week, to catch late waiver settling)."""
    if rounds is None:
        rounds = range(1, MAX_WEEK + 1)

    total = 0
    for round_ in rounds:
        transactions = fetch(f"/league/{league_id}/transactions/{round_}")
        if not transactions:
            continue
        for t in transactions:
            cur.execute("""
                INSERT INTO sleeper_transactions
                    (transaction_id, league_id, type, status, week, roster_ids, adds, drops, created)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, to_timestamp(%s / 1000.0))
                ON CONFLICT (transaction_id) DO UPDATE SET
                    type = EXCLUDED.type, status = EXCLUDED.status, week = EXCLUDED.week,
                    roster_ids = EXCLUDED.roster_ids, adds = EXCLUDED.adds, drops = EXCLUDED.drops,
                    created = EXCLUDED.created, synced_at = now();
            """, (
                t["transaction_id"], league_id, t.get("type"), t.get("status"), round_,
                t.get("roster_ids") or [], json.dumps(t.get("adds")), json.dumps(t.get("drops")),
                t.get("created"),
            ))
            total += 1
    return total


def run():
    conn = get_connection()
    cur = conn.cursor()

    try:
        print(f"Following season chain from league_id={CURRENT_LEAGUE_ID}...")
        chain = get_season_chain(CURRENT_LEAGUE_ID)
        print(f"Found {len(chain)} season(s): {[(l['season'], l['league_id']) for l in chain]}")

        for league in chain:
            league_id, season = league["league_id"], league.get("season")
            print(f"\n--- Season {season} (league_id={league_id}) ---")

            upsert_league(cur, league)
            print("  league settings staged")

            n_rosters = upsert_rosters(cur, league_id)
            print(f"  {n_rosters} rosters staged")

            n_users = upsert_users(cur, league_id)
            print(f"  {n_users} users staged")

            n_matchups = upsert_matchups(cur, league_id)
            print(f"  {n_matchups} matchup rows staged")

            n_transactions = upsert_transactions(cur, league_id)
            print(f"  {n_transactions} transactions staged")

        conn.commit()
        print("\nDone. All seasons committed in one all-or-nothing transaction.")

    except Exception:
        conn.rollback()
        print("\nERROR: run failed partway through. ALL staged changes rolled "
              "back -- the DB is exactly as it was before this run started. "
              "Fix the underlying issue and rerun from scratch; there is no "
              "partial state to clean up.")
        raise

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    run()
