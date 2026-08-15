"""
scripts/waiver_wire_finder.py

Step 9 -- Waiver-wire target finder. Ranks every FREE AGENT (not on any
roster in the current league) by spike-score threat, using the exact
same player_tiers-based logic as opponent_scout.py (Step 8) --
get_spike_profile is intentionally copy-identical between the two
scripts rather than shared via import, so a future change to one
doesn't silently change the other without a deliberate edit.

"Spike threat" uses the same ownable-pool threshold as Step 8
(mean + 1.25*stddev >= 35, via the real player_tiers view) -- see
opponent_scout.py's docstring for the lock_bar vs spike_bar distinction.

Fallback is capped to exactly ONE year back, derived automatically from
season_id's format (e.g. '22026' -> '22025') rather than a hardcoded
literal -- self-updating every season with no code change needed. An
earlier version searched arbitrarily far back for a player's most
recent qualifying season, which surfaced players like Lonzo Ball (last
qualifying season 22021) ranked as top threats off 5-year-old data. A
player who doesn't qualify in either the target season or the year
before is excluded entirely.

Player universe = sleeper_player_crosswalk (the ~394 Sleeper-relevant
players this league actually tracks), minus whoever's currently on any
roster in the current league (via roster_ownership). Player-level only
-- no matchup_id, no week, no dependency on Step 6's stalled points
data at all.

LIMITATION: same as Step 8 -- this ranks season-long capability, not a
prediction that a specific free agent goes off in an upcoming week.

Run:
    python scripts/waiver_wire_finder.py --season-id 22026
    python scripts/waiver_wire_finder.py --season-id 22026 --limit 10
"""

import argparse

from db_connection import get_connection


# =========================
# League / roster resolution
# =========================

def get_current_league_id(conn) -> str:
    cur = conn.cursor()
    cur.execute("SELECT league_id FROM sleeper_current_league")
    row = cur.fetchone()
    cur.close()
    if row is None:
        raise ValueError("sleeper_current_league returned no rows -- check the previous_league_id chain.")
    return row[0]


def get_free_agents(conn, league_id: str) -> list[dict]:
    """
    Crosswalk players (this league's tracked Sleeper universe) minus
    anyone currently on any roster. LEFT JOIN + IS NULL rather than
    NOT IN, so a NULL nba_player_id somewhere in roster_ownership can't
    silently poison the exclusion (a known NOT IN / NULL footgun).
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT c.sleeper_player_id, c.nba_player_id, c.sleeper_full_name
        FROM sleeper_player_crosswalk c
        LEFT JOIN roster_ownership ro
            ON ro.sleeper_player_id = c.sleeper_player_id AND ro.league_id = %s
        WHERE ro.sleeper_player_id IS NULL
        ORDER BY c.sleeper_full_name
    """, (league_id,))
    rows = cur.fetchall()
    cur.close()
    return [
        {"sleeper_player_id": r[0], "nba_player_id": r[1], "player_name": r[2]}
        for r in rows
    ]


# =========================
# Spike profile (identical logic to opponent_scout.py's get_spike_profile)
# =========================

def get_spike_profile(conn, nba_player_id: int, season_id: str) -> dict | None:
    """
    Queries the real player_tiers view, which already encodes the
    ownable-pool spike threshold (games_played >= 20 AND
    avg + 1.25*stddev >= 35) in its WHERE clause -- so a row existing
    at all means this player clears that bar.

    Tries season_id first. If that season has no row (true before any
    games are played), tries EXACTLY ONE fallback: season_id - 1,
    derived automatically from season_id's format (e.g. '22026' ->
    '22025') rather than a hardcoded literal -- self-updating every
    season with no code change needed. Capped to one year back, not
    open-ended -- an earlier version searched arbitrarily far back for
    a player's most recent qualifying season, which surfaced players
    like Lonzo Ball (last qualifying season 22021) ranked as top
    threats off 5-year-old data.

    Returns None if the player clears the bar in neither season.
    """
    fallback_season_id = str(int(season_id) - 1)

    cur = conn.cursor()
    cur.execute("""
        SELECT avg_fantasy_score, stddev_fantasy_score, tier, rank_in_season
        FROM player_tiers
        WHERE player_id = %s AND season_id = %s
    """, (nba_player_id, season_id))
    row = cur.fetchone()
    used_season_id = season_id

    if row is None:
        cur.execute("""
            SELECT avg_fantasy_score, stddev_fantasy_score, tier, rank_in_season
            FROM player_tiers
            WHERE player_id = %s AND season_id = %s
        """, (nba_player_id, fallback_season_id))
        row = cur.fetchone()
        used_season_id = fallback_season_id

    cur.close()
    if row is None:
        return None

    avg, stddev, tier, rank_in_season = row
    avg, stddev = float(avg), float(stddev)
    spike_bar = round(avg + 1.25 * stddev, 2)
    return {
        "avg_fantasy_score": avg,
        "stddev_fantasy_score": stddev,
        "tier": tier,
        "rank_in_season": rank_in_season,
        "spike_bar": spike_bar,
        "profile_season_id": used_season_id,
        "is_fallback_season": used_season_id != season_id,
    }


# =========================
# Report
# =========================

def build_report(conn, league_id: str, season_id: str) -> list[dict]:
    free_agents = get_free_agents(conn, league_id)
    ranked = []
    for p in free_agents:
        if p["nba_player_id"] is None:
            continue  # unmatched crosswalk rows (e.g. 2026-27 rookies) -- no stats to rank by
        profile = get_spike_profile(conn, p["nba_player_id"], season_id)
        if profile is None:
            continue  # never clears the spike bar in season_id or the year before
        ranked.append({**p, **profile})

    ranked.sort(key=lambda r: -r["spike_bar"])
    return ranked


def print_report(report: list[dict], season_id: str, limit: int | None):
    shown = report[:limit] if limit else report
    print(f"\nWaiver-wire spike threats ({len(report)} free agents clear the bar, showing {len(shown)})")
    print(f"{'Player':<24} {'Tier':<8} {'Rank':>5} {'Avg':>7} {'StdDev':>8} {'SpikeBar':>9}  Profile")
    print("-" * 82)
    for r in shown:
        profile_note = f"season {r['profile_season_id']}"
        if r["is_fallback_season"]:
            profile_note += " (fallback)"
        print(f"{r['player_name']:<24} {r['tier']:<8} {r['rank_in_season']:>5} {r['avg_fantasy_score']:>7.2f} "
              f"{r['stddev_fantasy_score']:>8.2f} {r['spike_bar']:>9.2f}  {profile_note}")
    print("\nSpikeBar = avg + 1.25*stddev (players shown already clear the ownable-pool bar of 35).")
    print("This ranks season-long capability, not a prediction for any specific upcoming week.")
    print("'(fallback)' = current season has no games yet; using the player's most recent prior season.")


# =========================
# CLI
# =========================

def main():
    parser = argparse.ArgumentParser(description="Rank unrostered free agents by spike-score threat.")
    parser.add_argument("--season-id", required=True, help="e.g. 22026")
    parser.add_argument("--limit", type=int, default=None, help="Show only the top N (default: all)")
    args = parser.parse_args()

    conn = get_connection()
    league_id = get_current_league_id(conn)
    report = build_report(conn, league_id, args.season_id)
    conn.close()

    print_report(report, args.season_id, args.limit)


if __name__ == "__main__":
    main()
