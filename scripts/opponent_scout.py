"""
scripts/opponent_scout.py

Step 8 -- Opponent threat scouting. Given your roster_id and a week,
finds your opponent via sleeper_matchups and ranks their ENTIRE current
roster (per project decision -- no starter/bench distinction needed;
see v3 roadmap notes) by spike-score threat.

"Spike threat" uses the ownable-pool threshold from v1.0
(mean + 1.25*stddev >= 35), NOT the lock_bar (mean + 0.5*stddev) that
game_lock_signal uses for the actual lock/hold decision. Those are two
different questions:
    - lock_bar (0.5*stddev): "would this score, once it happens,
      clear the bar to lock?"
    - spike_bar (1.25*stddev): "how capable is this player of a big
      spike score at all?" -- the same lens applied to a player you're
      sizing up on an opponent's bench, not one you're deciding to
      lock/hold on your own roster.

Roster-only, structurally independent of Step 6 (sleeper_matchups is
roster/week STRUCTURE, never flagged unreliable -- only Sleeper's own
points were). Uses this project's real player_tiers view (models/player_tiers.sql),
which already encodes the ownable-pool spike threshold
(games_played >= 20 AND avg + 1.25*stddev >= 35) -- a player appearing
in the report at all means they clear that bar.

NOTE: player_tiers must actually be applied to the live DB before this
will run (psql -d postgres -f models/player_tiers.sql) -- the .sql file
existing in the repo doesn't mean the view exists in the database.

Fallback is capped to exactly ONE year back, derived automatically from
season_id's format (e.g. '22026' -> '22025') -- see get_spike_profile's
docstring.

LIMITATION: this ranks threat level from each player's season profile.
It is not a live/actual-game prediction -- there's no way to know a
player will spike a specific future week, only how capable they are of
it based on their season-long distribution.

Run:
    python scripts/opponent_scout.py MY_ROSTER_ID --week 5 --season-id 22025
"""

import argparse

from db_connection import get_connection


# =========================
# Matchup resolution
# =========================

def get_current_league_id(conn) -> str:
    cur = conn.cursor()
    cur.execute("SELECT league_id FROM sleeper_current_league")
    row = cur.fetchone()
    cur.close()
    if row is None:
        raise ValueError("sleeper_current_league returned no rows -- check the previous_league_id chain.")
    return row[0]


def get_opponent_roster_id(conn, league_id: str, week: int, roster_id: int) -> int:
    """
    Finds the roster sharing my matchup_id for this week. Playoff-
    eliminated teams have matchup_id = NULL and have no opponent --
    raises a clear error rather than silently returning nothing.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT matchup_id FROM sleeper_matchups
        WHERE league_id = %s AND week = %s AND roster_id = %s
    """, (league_id, week, roster_id))
    row = cur.fetchone()
    if row is None:
        cur.close()
        raise ValueError(f"roster_id={roster_id} has no sleeper_matchups row for week={week}.")
    matchup_id = row[0]
    if matchup_id is None:
        cur.close()
        raise ValueError(f"roster_id={roster_id} has no opponent in week={week} (bracket-eliminated / bye).")

    cur.execute("""
        SELECT roster_id FROM sleeper_matchups
        WHERE league_id = %s AND week = %s AND matchup_id = %s AND roster_id != %s
    """, (league_id, week, matchup_id, roster_id))
    opp_row = cur.fetchone()
    cur.close()
    if opp_row is None:
        raise ValueError(f"No opponent roster found for matchup_id={matchup_id}, week={week}.")
    return opp_row[0]


# =========================
# Opponent roster + threat calc
# =========================

def get_opponent_players(conn, league_id: str, roster_id: int) -> list[dict]:
    cur = conn.cursor()
    cur.execute("""
        SELECT sleeper_player_id, nba_player_id, player_name, owner_name
        FROM roster_ownership
        WHERE league_id = %s AND roster_id = %s
        ORDER BY player_name
    """, (league_id, roster_id))
    rows = cur.fetchall()
    cur.close()
    return [
        {"sleeper_player_id": r[0], "nba_player_id": r[1], "player_name": r[2], "owner_name": r[3]}
        for r in rows
    ]


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
    a player's most recent qualifying season, which risked surfacing a
    player as a "threat" off many-year-old data (see
    waiver_wire_finder.py's Lonzo Ball case, same underlying issue).

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

def build_report(conn, league_id: str, roster_id: int, season_id: str) -> list[dict]:
    players = get_opponent_players(conn, league_id, roster_id)
    report = []
    for p in players:
        if p["nba_player_id"] is None:
            report.append({**p, "spike_bar": None, "note": "no crosswalk match -- unmatched Sleeper player"})
            continue
        profile = get_spike_profile(conn, p["nba_player_id"], season_id)
        if profile is None:
            report.append({**p, "spike_bar": None, "note": "never clears the spike bar in season_id or the year before"})
            continue
        report.append({**p, **profile, "note": None})

    # Rank threats (spike_bar known) first, descending; unranked players after.
    ranked = sorted([r for r in report if r["spike_bar"] is not None], key=lambda r: -r["spike_bar"])
    unranked = [r for r in report if r["spike_bar"] is None]
    return ranked + unranked


def print_report(report: list[dict], my_roster_id: int, opp_roster_id: int, week: int):
    opp_owner = next((r["owner_name"] for r in report if r["owner_name"]), "Unknown")
    print(f"\nWeek {week}: roster_id {my_roster_id} vs roster_id {opp_roster_id} ({opp_owner})")
    print(f"{'Player':<24} {'Tier':<8} {'Rank':>5} {'Avg':>7} {'StdDev':>8} {'SpikeBar':>9}  Profile")
    print("-" * 82)
    for r in report:
        if r["spike_bar"] is None:
            print(f"{r['player_name'] or r['sleeper_player_id']:<24} -- {r['note']}")
            continue
        profile_note = f"season {r['profile_season_id']}"
        if r["is_fallback_season"]:
            profile_note += " (fallback)"
        print(f"{r['player_name']:<24} {r['tier']:<8} {r['rank_in_season']:>5} {r['avg_fantasy_score']:>7.2f} "
              f"{r['stddev_fantasy_score']:>8.2f} {r['spike_bar']:>9.2f}  {profile_note}")
    print("\nSpikeBar = avg + 1.25*stddev (players shown already clear the ownable-pool bar of 35).")
    print("This ranks season-long capability, not a prediction for this specific week.")
    print("'(fallback)' = current season has no games yet; using the player's most recent prior season.")
    print("A player missing from this list either never clears the spike bar, or has <20 games that season.")


# =========================
# CLI
# =========================

def main():
    parser = argparse.ArgumentParser(description="Rank an opponent's roster by spike-score threat for a given week.")
    parser.add_argument("roster_id", type=int, help="Your own roster_id")
    parser.add_argument("--week", type=int, required=True)
    parser.add_argument("--season-id", required=True, help="e.g. 22025")
    args = parser.parse_args()

    conn = get_connection()
    league_id = get_current_league_id(conn)
    opp_roster_id = get_opponent_roster_id(conn, league_id, args.week, args.roster_id)
    report = build_report(conn, league_id, opp_roster_id, args.season_id)
    conn.close()

    print_report(report, args.roster_id, opp_roster_id, args.week)


if __name__ == "__main__":
    main()
