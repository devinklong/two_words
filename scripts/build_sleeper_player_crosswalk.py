"""
scripts/build_sleeper_player_crosswalk.py

Builds sleeper_player_crosswalk via normalized full-name matching --
Sleeper's cross-reference IDs aren't confirmed to equal nba_api's
PERSON_ID, so name matching is the reliable path. Scoped to only the
Sleeper player_ids that actually appear in this league's rosters/
matchups/transactions (not all ~500 NBA players Sleeper tracks), pulled
from /players/nba, per Sleeper's own guidance to call that endpoint
sparingly. Ambiguous or failed matches are logged, not guessed at.

ASSUMPTION flagged for review: assumes `players` has a `full_name`
column to match against -- adjust NBA_NAME_QUERY below if the real
schema differs.
"""

import sys
import re
import json
import unicodedata
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from db_connection import get_connection

import requests

BASE_URL = "https://api.sleeper.app/v1"
LEAGUE_IDS = [
    "1113487058661744640",  # 2024
    "1214984705477185536",  # 2025
]

NBA_NAME_QUERY = "SELECT player_id, full_name FROM players;"  # ASSUMPTION -- see docstring


def strip_accents(name):
    """NFKD-decomposes then drops combining marks, folding e.g. 'Mićić' -> 'Micic',
    'Şengün' -> 'Sengun' -- needed since nba_api's list is plain ASCII but Sleeper's isn't."""
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize_name(name, strip_suffix=False):
    if not name:
        return ""
    name = strip_accents(name).lower().strip()
    if strip_suffix:
        name = re.sub(r"\b(jr|sr|ii|iii|iv)\.?\b", "", name)
    name = re.sub(r"[^\w\s]", "", name)
    return re.sub(r"\s+", " ", name).strip()


def is_duplicate_placeholder(full_name):
    """Sleeper occasionally carries stale placeholder records for newly-drafted rookies
    with 'DUPLICATE' literally in the name -- not a real name-format mismatch, exclude outright."""
    return bool(full_name) and "duplicate" in full_name.lower()


def get_league_player_ids(cur):
    """Collects every Sleeper player_id appearing in this league's rosters, matchups, or
    transactions, across both seasons -- the scoping that keeps the crosswalk small."""
    ids = set()

    cur.execute("SELECT players, starters FROM sleeper_rosters WHERE league_id = ANY(%s);", (LEAGUE_IDS,))
    for players, starters in cur.fetchall():
        ids.update(players or [])
        ids.update(starters or [])

    cur.execute("SELECT players FROM sleeper_matchups WHERE league_id = ANY(%s);", (LEAGUE_IDS,))
    for (players,) in cur.fetchall():
        ids.update(players or [])

    cur.execute("SELECT adds, drops FROM sleeper_transactions WHERE league_id = ANY(%s);", (LEAGUE_IDS,))
    for adds, drops in cur.fetchall():
        if adds:
            ids.update(adds.keys())
        if drops:
            ids.update(drops.keys())

    return ids


def fetch_all_nba_players():
    print("Fetching /players/nba (full directory, called once per run per Sleeper's guidance)...")
    resp = requests.get(f"{BASE_URL}/players/nba")
    resp.raise_for_status()
    return resp.json()


def build_nba_name_lookups(cur):
    """Two lookups: suffix-intact (tried first, so real Sr/Jr pairs stay distinct) and
    suffix-stripped (fallback only, for cases where one source includes the suffix and
    the other doesn't)."""
    cur.execute(NBA_NAME_QUERY)
    rows = cur.fetchall()
    exact_lookup, stripped_lookup = {}, {}
    for player_id, full_name in rows:
        exact_lookup.setdefault(normalize_name(full_name, strip_suffix=False), []).append(player_id)
        stripped_lookup.setdefault(normalize_name(full_name, strip_suffix=True), []).append(player_id)
    return exact_lookup, stripped_lookup


def run():
    conn = get_connection()
    cur = conn.cursor()

    league_player_ids = get_league_player_ids(cur)
    print(f"{len(league_player_ids)} distinct Sleeper player_ids found across this league's data.")

    all_sleeper_players = fetch_all_nba_players()
    relevant = {pid: all_sleeper_players[pid] for pid in league_player_ids if pid in all_sleeper_players}
    print(f"{len(relevant)} of those resolved to a real Sleeper player record.")

    nba_exact_lookup, nba_stripped_lookup = build_nba_name_lookups(cur)

    matched, ambiguous, unmatched, skipped_duplicates = 0, [], [], []

    for sleeper_id, p in relevant.items():
        full_name = p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()

        if is_duplicate_placeholder(full_name):
            skipped_duplicates.append((sleeper_id, full_name))
            continue

        # Try suffix-intact first (keeps real Sr/Jr pairs distinct); fall back to
        # suffix-stripped only if the exact form finds nothing.
        norm_exact = normalize_name(full_name, strip_suffix=False)
        candidates = nba_exact_lookup.get(norm_exact, [])
        match_method = "exact_name"
        if not candidates:
            norm_stripped = normalize_name(full_name, strip_suffix=True)
            candidates = nba_stripped_lookup.get(norm_stripped, [])
            match_method = "exact_name_suffix_stripped"

        metadata = json.dumps({
            "espn_id": p.get("espn_id"), "sportradar_id": p.get("sportradar_id"),
            "yahoo_id": p.get("yahoo_id"), "stats_id": p.get("stats_id"),
            "rotowire_id": p.get("rotowire_id"),
        })

        if len(candidates) == 1:
            cur.execute("""
                INSERT INTO sleeper_player_crosswalk
                    (sleeper_player_id, nba_player_id, sleeper_full_name, sleeper_team,
                     sleeper_position, match_method, sleeper_metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (sleeper_player_id) DO UPDATE SET
                    nba_player_id = EXCLUDED.nba_player_id, sleeper_full_name = EXCLUDED.sleeper_full_name,
                    sleeper_team = EXCLUDED.sleeper_team, sleeper_position = EXCLUDED.sleeper_position,
                    match_method = EXCLUDED.match_method, sleeper_metadata = EXCLUDED.sleeper_metadata,
                    matched_at = now();
            """, (sleeper_id, candidates[0], full_name, p.get("team"), p.get("position"),
                  match_method, metadata))
            matched += 1
        elif len(candidates) > 1:
            ambiguous.append((sleeper_id, full_name, candidates))
        else:
            unmatched.append((sleeper_id, full_name))

    conn.commit()
    cur.close()
    conn.close()

    print(f"\nMatched: {matched}")
    print(f"Skipped as Sleeper DUPLICATE placeholder records: {len(skipped_duplicates)}")
    for sid, name in skipped_duplicates:
        print(f"  {name} (sleeper_id={sid})")
    print(f"Ambiguous (multiple nba_player_id candidates for the same normalized name): {len(ambiguous)}")
    for sid, name, cands in ambiguous:
        print(f"  {name} (sleeper_id={sid}) -> candidates: {cands}")
    print(f"Unmatched (no candidate found -- likely a name-format mismatch, needs manual review): {len(unmatched)}")
    for sid, name in unmatched:
        print(f"  {name} (sleeper_id={sid})")
    print("\nAmbiguous/unmatched rows are NOT in sleeper_player_crosswalk yet -- resolve manually")
    print("(check cleaning_logs/ convention) then insert with match_method='manual'.")


if __name__ == "__main__":
    run()
