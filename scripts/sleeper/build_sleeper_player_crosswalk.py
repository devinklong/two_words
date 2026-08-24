"""
scripts/sleeper/build_sleeper_player_crosswalk.py

Builds sleeper_player_crosswalk via normalized full-name matching --
Sleeper's cross-reference IDs aren't confirmed to equal nba_api's
PERSON_ID, so name matching is the reliable path. Scoped to only the
Sleeper player_ids that actually appear in this league's rosters/
matchups/transactions (not all ~500 NBA players Sleeper tracks), pulled
from /players/nba, per Sleeper's own guidance to call that endpoint
sparingly. Ambiguous or failed matches are logged, not guessed at.

League scoping: pulls every league_id from sleeper_leagues instead of a
hardcoded list. This is a dynasty league (previous_league_id chain never
resets), so a hardcoded list needs a manual edit every season a new
league_id shows up -- confirmed 8/13/26 this was silently missing 30
rookies drafted into the 2026-27 league (league_id not yet in the old
hardcoded LEAGUE_IDS), since get_league_player_ids() never even looked
at their roster rows. Reading league_ids from sleeper_leagues instead
means this stays correct automatically once backfill_sleeper_league.py
has synced the new season -- no edit needed here.

ASSUMPTION flagged for review: assumes `players` has a `full_name`
column to match against -- adjust NBA_NAME_QUERY below if the real
schema differs.

SUFFIX HANDLING (fixed 8/23/26 after 1924/2445/2009/2414 were found
silently mismatched -- see cleaning_logs/ and docs/methodology_notes.md):
normalize_name() already strips punctuation from a suffix token before
comparing, so "Jr." vs "Jr" was never the actual problem. The real gap
was the suffix-stripped fallback tier: when the exact suffix-intact name
didn't match, the old code fell back to comparing suffix-STRIPPED names
and trusted a single resulting candidate blindly. That's unsafe --
Sr/Jr pairs (or any two real people who share a base name) collapse to
the same stripped key, so "only one candidate" doesn't actually prove
it's the right person; a same-named different-generation player being
absent or differently formatted in `players` can make the fallback
confidently pick the wrong row. Kevin Porter Jr./Jaren Jackson Jr./
Jabari Smith Jr. all mismatched this way (an unrelated same-name entry
in `players` was the only candidate at the stripped-key level). Orlando
Robinson Jr. mismatched a different way -- his `players` row has no
suffix on file at all, but that WAS actually the safe case; it just
happened to have gone unmatched previously for other reasons.

Fix: the stripped-name fallback now only auto-accepts a match when the
candidate genuinely carries no suffix in `players` (i.e. it can't be
confused with a same-named other generation) or when there's exactly one
plausible match and the sleeper record's suffix is authoritative. Any
case where nba_api's own row carries a *conflicting* suffix, or the
stripped key maps to more than one nba_player_id, is routed to a new
`suffix_conflict` bucket for manual review instead of being auto-matched
-- never guessed at silently.

FANTASY POSITIONS (added 8/23/26, v3.2): also syncs
sleeper_player_fantasy_positions (see schema/tables/), one row per
eligible position, sourced from Sleeper's real `fantasy_positions` array
-- not the singular `position` field used elsewhere in this file for the
crosswalk's own sleeper_position column, which was confirmed to silently
collapse 68% of multi-eligible players down to one position. Only synced
for players that got a real crosswalk match this run (ambiguous/
suffix-conflict/unmatched players have no crosswalk row for the FK to
point at). DEF (team-defense) is filtered out -- confirmed via a full
directory scan that it never applies to an individual NBA player, and
no generic/group labels (F, G, UTIL) ever appear player-side. Full
delete-then-reinsert per player each run, matching roster_ownership's
current-state-only pattern -- there's no historical position tracking to
preserve, and the row count itself changes whenever eligibility does.
"""

import sys
import re
import json
import unicodedata
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from db_connection import get_connection

import requests

BASE_URL = "https://api.sleeper.app/v1"

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


def has_suffix(name):
    """True if the name carries a Jr/Sr/II/III/IV token -- used to detect when the
    exact-intact and suffix-stripped normalized forms actually differ."""
    return normalize_name(name, strip_suffix=False) != normalize_name(name, strip_suffix=True)


def is_duplicate_placeholder(full_name):
    """Sleeper occasionally carries stale placeholder records for newly-drafted rookies
    with 'DUPLICATE' literally in the name -- not a real name-format mismatch, exclude outright."""
    return bool(full_name) and "duplicate" in full_name.lower()


def get_all_league_ids(cur):
    """Every league_id in the dynasty chain, read from sleeper_leagues instead of a
    hardcoded list -- self-updating each season as backfill_sleeper_league.py syncs
    new league_ids in, no manual edit needed here."""
    cur.execute("SELECT league_id FROM sleeper_leagues;")
    return [row[0] for row in cur.fetchall()]


def get_league_player_ids(cur, league_ids):
    """Collects every Sleeper player_id appearing in this league's rosters, matchups, or
    transactions, across every season in the chain -- the scoping that keeps the
    crosswalk small."""
    ids = set()

    cur.execute("SELECT players, starters FROM sleeper_rosters WHERE league_id = ANY(%s);", (league_ids,))
    for players, starters in cur.fetchall():
        ids.update(players or [])
        ids.update(starters or [])

    cur.execute("SELECT players FROM sleeper_matchups WHERE league_id = ANY(%s);", (league_ids,))
    for (players,) in cur.fetchall():
        ids.update(players or [])

    cur.execute("SELECT adds, drops FROM sleeper_transactions WHERE league_id = ANY(%s);", (league_ids,))
    for adds, drops in cur.fetchall():
        if adds:
            ids.update(adds.keys())
        if drops:
            ids.update(drops.keys())

    return ids


def sync_player_fantasy_positions(cur, sleeper_id, fantasy_positions):
    """Full delete-then-reinsert of this one player's eligible positions --
    matches roster_ownership's current-state-only pattern rather than an
    incremental diff, since the row COUNT changes whenever eligibility
    changes (no stable key to diff against, and nothing historical worth
    preserving). DEF (team-defense) is dropped -- confirmed via a full
    directory scan (inspect_all_fantasy_position_values.py, 8/23/26) to
    never apply to an individual NBA player; only C/PF/PG/SF/SG are ever
    real player-side values."""
    cur.execute(
        "DELETE FROM sleeper_player_fantasy_positions WHERE sleeper_player_id = %s;",
        (sleeper_id,),
    )
    positions = {pos for pos in (fantasy_positions or []) if pos and pos != "DEF"}
    for pos in positions:
        cur.execute(
            """
            INSERT INTO sleeper_player_fantasy_positions (sleeper_player_id, position)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING;
            """,
            (sleeper_id, pos),
        )
    return len(positions)


def fetch_all_nba_players():
    print("Fetching /players/nba (full directory, called once per run per Sleeper's guidance)...")
    resp = requests.get(f"{BASE_URL}/players/nba")
    resp.raise_for_status()
    return resp.json()


def build_nba_name_lookups(cur):
    """Two lookups: suffix-intact (tried first, so real Sr/Jr pairs stay distinct) and
    suffix-stripped (fallback only, for cases where one source includes the suffix and
    the other doesn't). The stripped lookup now carries each candidate's own
    (player_id, original_full_name, had_suffix) so the fallback can tell whether a
    candidate is genuinely suffix-free (safe) or carries a conflicting suffix (unsafe)."""
    cur.execute(NBA_NAME_QUERY)
    rows = cur.fetchall()
    exact_lookup, stripped_lookup = {}, {}
    for player_id, full_name in rows:
        exact_lookup.setdefault(normalize_name(full_name, strip_suffix=False), []).append(player_id)
        stripped_key = normalize_name(full_name, strip_suffix=True)
        stripped_lookup.setdefault(stripped_key, []).append((player_id, full_name, has_suffix(full_name)))
    return exact_lookup, stripped_lookup


def resolve_suffix_stripped_match(sleeper_full_name, stripped_candidates):
    """Decide whether it's SAFE to auto-match via the suffix-stripped fallback, or
    whether this needs manual review instead.

    stripped_candidates: list of (nba_player_id, nba_full_name, nba_had_suffix) that
    share the sleeper record's suffix-stripped normalized name.

    Returns (player_id, match_method) on a safe match, or (None, reason) when this
    should be routed to manual review rather than guessed at.

    This is the fix for the 1924/2445/2009/2414 mismatches (8/23/26): the old code
    accepted ANY single candidate here, which silently mismatched real players whenever
    a same-named different generation existed in `players` under the same stripped key.
    """
    if len(stripped_candidates) > 1:
        # Multiple distinct nba_player_ids share this base name (e.g. both a Sr. and a
        # Jr. are on file) -- picking one would be a guess. Always defer to manual review.
        return None, "suffix_conflict_multiple_candidates"

    if not stripped_candidates:
        return None, "no_candidate"

    player_id, nba_full_name, nba_had_suffix = stripped_candidates[0]
    sleeper_has_suffix = has_suffix(sleeper_full_name)

    if sleeper_has_suffix and nba_had_suffix:
        # Both sides carry an explicit suffix but didn't agree on the exact-intact pass
        # -- the only way that happens is the suffixes themselves differ (e.g. sleeper
        # says "Jr.", nba's own row says "Sr."). That's two different real people --
        # never auto-match.
        return None, "suffix_conflict_mismatched_generation"

    # Remaining case is safe: exactly one nba row shares this base name, and at least
    # one side (usually nba_api's `players` table) simply never recorded a suffix for
    # this specific person -- e.g. Orlando Robinson Jr.'s `players` row is just
    # "Orlando Robinson", no "Jr." on file, with no other candidate to confuse it with.
    return player_id, "exact_name_suffix_stripped"


def run():
    conn = get_connection()
    cur = conn.cursor()

    league_ids = get_all_league_ids(cur)
    print(f"{len(league_ids)} league_id(s) found in sleeper_leagues: {league_ids}")

    league_player_ids = get_league_player_ids(cur, league_ids)
    print(f"{len(league_player_ids)} distinct Sleeper player_ids found across this league's data.")

    all_sleeper_players = fetch_all_nba_players()
    relevant = {pid: all_sleeper_players[pid] for pid in league_player_ids if pid in all_sleeper_players}
    print(f"{len(relevant)} of those resolved to a real Sleeper player record.")

    nba_exact_lookup, nba_stripped_lookup = build_nba_name_lookups(cur)

    matched, ambiguous, suffix_conflicts, unmatched, skipped_duplicates = 0, [], [], [], []
    positions_synced_players, positions_synced_rows = 0, 0

    for sleeper_id, p in relevant.items():
        full_name = p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()

        if is_duplicate_placeholder(full_name):
            skipped_duplicates.append((sleeper_id, full_name))
            continue

        # Try suffix-intact first (keeps real Sr/Jr pairs distinct); fall back to a
        # suffix-stripped comparison only if the exact form finds nothing -- and even
        # then, only auto-accept if resolve_suffix_stripped_match() judges it safe.
        norm_exact = normalize_name(full_name, strip_suffix=False)
        candidates = nba_exact_lookup.get(norm_exact, [])
        match_method = "exact_name"

        if not candidates:
            norm_stripped = normalize_name(full_name, strip_suffix=True)
            stripped_candidates = nba_stripped_lookup.get(norm_stripped, [])
            resolved_id, result = resolve_suffix_stripped_match(full_name, stripped_candidates)
            if resolved_id is not None:
                candidates = [resolved_id]
                match_method = result
            elif result in ("suffix_conflict_multiple_candidates", "suffix_conflict_mismatched_generation"):
                suffix_conflicts.append((sleeper_id, full_name, [c[0] for c in stripped_candidates], result))
                continue
            # else result == "no_candidate": candidates stays [] and falls through
            # to the normal unmatched bucket below, same as before.

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

            n_positions = sync_player_fantasy_positions(cur, sleeper_id, p.get("fantasy_positions"))
            positions_synced_players += 1
            positions_synced_rows += n_positions
        elif len(candidates) > 1:
            ambiguous.append((sleeper_id, full_name, candidates))
        else:
            unmatched.append((sleeper_id, full_name))

    conn.commit()
    cur.close()
    conn.close()

    print(f"\nMatched: {matched}")
    print(f"Fantasy positions synced: {positions_synced_players} player(s), "
          f"{positions_synced_rows} position row(s) total "
          f"({positions_synced_rows / positions_synced_players:.2f} avg/player)"
          if positions_synced_players else "Fantasy positions synced: 0 players")
    print(f"Skipped as Sleeper DUPLICATE placeholder records: {len(skipped_duplicates)}")
    for sid, name in skipped_duplicates:
        print(f"  {name} (sleeper_id={sid})")
    print(f"Ambiguous (multiple nba_player_id candidates on the exact-intact pass): {len(ambiguous)}")
    for sid, name, cands in ambiguous:
        print(f"  {name} (sleeper_id={sid}) -> candidates: {cands}")
    print(f"Suffix conflicts (Jr/Sr/etc. -- needs manual review, NOT auto-matched): {len(suffix_conflicts)}")
    for sid, name, cands, reason in suffix_conflicts:
        print(f"  {name} (sleeper_id={sid}) -> candidates: {cands} [{reason}]")
    print(f"Unmatched (no candidate found -- likely a name-format mismatch, needs manual review): {len(unmatched)}")
    for sid, name in unmatched:
        print(f"  {name} (sleeper_id={sid})")
    print("\nAmbiguous/suffix-conflict/unmatched rows are NOT in sleeper_player_crosswalk yet -- resolve")
    print("manually (check cleaning_logs/ convention) then insert with match_method='manual'.")


if __name__ == "__main__":
    run()
