"""
scripts/backfill_technical_flagrant_fouls.py

Backfills player_technical_flagrant_fouls from PlayByPlayV3, closing
the long-documented gap in game_fantasy_scores: this league's real
Sleeper scoring docks -2.0 for a technical foul (tf) and -2.0 for a
flagrant foul (ff), neither previously countable since game_logs has
no column distinguishing these from ordinary personal fouls.

EVERY subType classification below is hand-verified against real
Sleeper scores or the NBA's own rulebook (8/23/26 investigation), NOT
assumed from label text -- "contains the word Technical" was proven
repeatedly unsafe as a filter on its own:

  COSTS -2 (confirmed):
    'Technical'            -- 2 independent real-game confirmations
    'Flagrant Type 1'
    'Flagrant Type 2'
    'Hanging Technical'    -- DIRECTLY confirmed (Tatum, 11/24/23 vs
                               Magic) after an initial false negative
                               traced to a genuine NBA.com-vs-Sleeper
                               stat discrepancy (TOV 3 vs 2), not the
                               foul itself
    'Double Technical'     -- costs -2 for BOTH players named, but
                               PlayByPlayV3 only puts ONE on the row's
                               personId -- the second is parsed from
                               the free-text description and matched
                               against game_logs for that EXACT
                               game_id only (never a league-wide name
                               search)

  CONFIRMED NO PENALTY (excluded on purpose, not missed):
    'Defense 3 Second'              -- team-attributed (personId is a
    'Delay Technical'                  team ID, not a player)
    'Too Many Players Technical'
    'Double Personal'               -- not a tech/flagrant category at all
    'Flopping'                      -- DIRECTLY confirmed (Pippen Jr.,
                                        11/10/24 vs Grizzlies) -- NBA's
                                        own "Non-Unsportsmanlike Tech
                                        Foul" framing, real score match

  Every ordinary foul subType (Personal, Shooting, Loose Ball,
  Offensive, Offensive Charge, Personal Take, Away From Play, Clear
  Path, Transition Take) is untouched -- never technical-adjacent.

FIXED 8/23/26 (first real run surfaced two bugs):

  1. COACH TECHNICALS: 'Technical' fires for coaches too (e.g. Nick
     Nurse), not just players -- their personId is a coach ID, never
     present in players/game_logs. The original version tried to
     insert a row for every personId unconditionally, which threw an
     FK violation and rolled back the ENTIRE game's transaction --
     silently losing any legitimate PLAYER technicals from that same
     game too, not just the coach's. Fixed: each game's real
     game_logs.player_id set is fetched once, and every personId is
     checked against it before counting -- a coach ID simply doesn't
     match and is cleanly skipped, with the rest of that game's real
     data still saved.

  2. ACCENT MATCHING: Double Technical's second-player lookup used a
     literal ILIKE, which fails for anyone with a diacritic (Schröder
     vs "Schroder", Jokić vs "Jokic", Porziņģis vs "Porzingis",
     Dončić vs "Doncic", Diabaté vs "Diabate" all failed to match).
     Fixed: names are accent-folded on both sides before comparing
     (same unicodedata.normalize('NFKD', ...) approach
     build_sleeper_player_crosswalk.py already established). Also
     handles the "K. Johnson"/"Q. Jackson"-style abbreviated-initial
     format the NBA uses to disambiguate two same-last-name players in
     one game -- the initial narrows an otherwise-ambiguous match
     instead of being ignored.

RESUMABLE: technical_flagrant_scan_log tracks every game_id already
scanned, independent of whether it produced any counts rows (most
games have zero technicals). Since the coach-FK bug rolled back entire
games rather than partially succeeding, EVERY game that failed on the
first run never reached scan_log -- simply rerunning the same command
picks all of them back up automatically, no separate recovery step
needed. --season-id lets you run one season at a time.

Run (all seasons):    python scripts/backfill_technical_flagrant_fouls.py
Run (one season):     python scripts/backfill_technical_flagrant_fouls.py --season-id 22024
"""

import argparse
import re
import sys
import time
import unicodedata
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from db_connection import get_connection

from nba_api.stats.endpoints import playbyplayv3

SLEEP_SECONDS_BETWEEN_CALLS = 0.6

COSTS_2_TECHNICAL = {"Technical", "Hanging Technical"}
COSTS_2_FLAGRANT = {"Flagrant Type 1", "Flagrant Type 2"}
DOUBLE_TECHNICAL = "Double Technical"

DOUBLE_TECHNICAL_PATTERN = re.compile(r"Double Technical\s*-\s*(.+?),\s*(.+?)\s*\(")
# "K. Johnson" / "Q. Jackson" style abbreviated-initial disambiguation
INITIAL_PREFIX_PATTERN = re.compile(r"^([A-Z])\.\s*(.+)$")


def strip_accents(text):
    """Same approach build_sleeper_player_crosswalk.py already uses --
    NFKD decompose, drop combining marks, back to plain ASCII-ish text.
    Schröder -> Schroder, Jokić -> Jokic, Porziņģis -> Porzingis."""
    if not text:
        return text
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))


def fetch_games_needing_scan(conn, season_id=None):
    cur = conn.cursor()
    query = """
        SELECT DISTINCT gl.game_id
        FROM game_logs gl
        WHERE NOT EXISTS (
            SELECT 1 FROM technical_flagrant_scan_log tfsl WHERE tfsl.game_id = gl.game_id
        )
    """
    params = ()
    if season_id:
        query += " AND gl.season_id = %s"
        params = (season_id,)
    cur.execute(query, params)
    rows = [r[0] for r in cur.fetchall()]
    cur.close()
    return rows


def fetch_valid_players_for_game(conn, game_id):
    """Every real player_id that actually logged THIS game, plus their
    accent-stripped full_name for the Double Technical name match.
    Fetched once per game -- personIds not in this set are coaches/
    officials, not players, and get skipped rather than crash the
    whole game's insert."""
    cur = conn.cursor()
    cur.execute("""
        SELECT gl.player_id, p.full_name
        FROM game_logs gl
        JOIN players p ON p.player_id = gl.player_id
        WHERE gl.game_id = %s
    """, (game_id,))
    rows = cur.fetchall()
    cur.close()
    return {player_id: strip_accents(full_name) for player_id, full_name in rows}


def find_second_player_id(valid_players, name_guess):
    """Resolves the second player in a Double Technical event by
    accent-folded last-name match, scoped strictly to players who
    logged THIS exact game (valid_players) -- never a league-wide
    search. Handles the NBA's own "K. Johnson" initial-disambiguation
    format when two players share a last name in the same game."""
    initial = None
    last_name = name_guess
    m = INITIAL_PREFIX_PATTERN.match(name_guess)
    if m:
        initial, last_name = m.group(1), m.group(2)

    folded_last = strip_accents(last_name).lower()
    candidates = [
        (pid, full_name) for pid, full_name in valid_players.items()
        if folded_last in full_name.lower()
    ]

    if initial and len(candidates) > 1:
        # narrow by first-initial: full_name is "First Last" or
        # "First Middle Last" -- check the first name component
        candidates = [
            (pid, full_name) for pid, full_name in candidates
            if full_name.strip()[0].upper() == initial.upper()
        ]

    if len(candidates) == 1:
        return candidates[0][0]
    return None  # 0 or still-ambiguous matches -- skip rather than guess


def process_game(conn, game_id, valid_players):
    """Returns {player_id: {"technical": n, "flagrant": n}} for one
    game. personIds not in valid_players (coaches/officials) are
    silently skipped -- never inserted, never crash the game."""
    pbp = playbyplayv3.PlayByPlayV3(game_id=game_id)
    df = pbp.get_data_frames()[0]
    foul_like = df[df["actionType"].str.contains("foul", case=False, na=False)]

    counts = {}

    def bump(player_id, kind):
        if player_id is None or player_id not in valid_players:
            return  # coach/official ID, or unresolved -- not an error, just excluded
        if player_id not in counts:
            counts[player_id] = {"technical": 0, "flagrant": 0}
        counts[player_id][kind] += 1

    for _, row in foul_like.iterrows():
        st = row["subType"]
        person_id = row["personId"]

        if st in COSTS_2_TECHNICAL:
            bump(person_id, "technical")
        elif st in COSTS_2_FLAGRANT:
            bump(person_id, "flagrant")
        elif st == DOUBLE_TECHNICAL:
            # personId already covers the first named player when it's
            # a real player -- confirmed from real examples ("Double
            # Technical - Okongwu, Thomas" with personId=Okongwu's id).
            bump(person_id, "technical")
            match = DOUBLE_TECHNICAL_PATTERN.search(row["description"] or "")
            if match:
                _, second_name = match.groups()
                second_id = find_second_player_id(valid_players, second_name.strip())
                if second_id is not None:
                    bump(second_id, "technical")
                else:
                    print(f"  WARNING: could not resolve 2nd player '{second_name}' "
                          f"in Double Technical, game_id={game_id} -- skipped, not guessed.")
        # every other subType (confirmed no-penalty or ordinary fouls) -- ignored

    return counts


def upsert_counts(conn, game_id, counts):
    cur = conn.cursor()
    for player_id, c in counts.items():
        if c["technical"] == 0 and c["flagrant"] == 0:
            continue
        cur.execute("""
            INSERT INTO player_technical_flagrant_fouls (player_id, game_id, technical_fouls, flagrant_fouls)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (player_id, game_id) DO UPDATE SET
                technical_fouls = EXCLUDED.technical_fouls,
                flagrant_fouls = EXCLUDED.flagrant_fouls;
        """, (player_id, game_id, c["technical"], c["flagrant"]))
    cur.execute("""
        INSERT INTO technical_flagrant_scan_log (game_id) VALUES (%s)
        ON CONFLICT (game_id) DO NOTHING;
    """, (game_id,))
    conn.commit()
    cur.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--season-id", default=None, help="e.g. 22024 -- omit to backfill all seasons")
    args = parser.parse_args()

    conn = get_connection()
    games = fetch_games_needing_scan(conn, args.season_id)
    print(f"{len(games)} game(s) need scanning" +
          (f" (season {args.season_id})" if args.season_id else " (all seasons)") + ".")

    total_technical = 0
    total_flagrant = 0
    failures = []

    for i, game_id in enumerate(games, start=1):
        try:
            valid_players = fetch_valid_players_for_game(conn, game_id)
            counts = process_game(conn, game_id, valid_players)
            upsert_counts(conn, game_id, counts)
            for c in counts.values():
                total_technical += c["technical"]
                total_flagrant += c["flagrant"]
        except Exception as e:
            failures.append((game_id, str(e)))
            conn.rollback()

        if i % 25 == 0:
            print(f"  [{i}/{len(games)}] scanned...")

        time.sleep(SLEEP_SECONDS_BETWEEN_CALLS)

    conn.close()

    print(f"\nDone. {len(games) - len(failures)}/{len(games)} games scanned successfully.")
    print(f"{total_technical} technical foul(s), {total_flagrant} flagrant foul(s) recorded.")

    if failures:
        print(f"\n{len(failures)} game(s) genuinely failed (safe to rerun -- scan_log only advances on success):")
        for game_id, err in failures:
            print(f"  {game_id}: {err}")


if __name__ == "__main__":
    main()
