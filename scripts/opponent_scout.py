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

DURABILITY SIGNAL (added 8/25/26): same real v3.2 finding waiver_wire_
finder.py picked up -- avg_fantasy_score alone can look great off a
shrinking sample size (real examples: Joel Embiid 23% of games played
in his worst season, Anthony Davis 24% in his). Adds each player's
real games_played against that season's real max games by any single
player -- flagged when it drops below DURABILITY_FLAG_THRESHOLD.
CONFIRMED 8/26/26 via `\\d player_tiers`: the view does NOT expose
games_played as a column, despite filtering on it internally. Fixed
by joining player_tiers to a fresh COUNT(DISTINCT game_id) from
game_logs instead -- the original ASSUMPTION was wrong, caught by a
real UndefinedColumn error on first run.

NBA-TEAM STACKING FLAG (added 8/25/26): another real v3.2 finding with
no signal here before. Confirmed (analyze_stacking_roster_construction.py,
within-roster controlled test): a roster with 2+ players sharing a real
NBA team shows real, meaningfully HIGHER week-to-week VARIANCE, but NO
confirmed effect on win rate once team quality is controlled for --
worded carefully below to match that finding exactly, not oversold as
a competitive edge either direction. ASSUMPTION flagged for review:
uses each player's MODE team_id for the season (most games played for
one team), the same simplification used in the original analysis, not
the exact team for a specific week.

LIMITATION: this ranks threat level from each player's season profile.
It is not a live/actual-game prediction -- there's no way to know a
player will spike a specific future week, only how capable they are of
it based on their season-long distribution.

Run:
    python scripts/opponent_scout.py MY_ROSTER_ID --week 5 --season-id 22025
"""

import argparse
from collections import defaultdict

from db_connection import get_connection

DURABILITY_FLAG_THRESHOLD = 0.65  # flag, not a hard cutoff -- not a validated number, see docstring


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


# =========================
# Durability context
# =========================

def get_season_max_games(conn, season_id: str) -> int:
    """Max real games played by any single player that season, from
    game_logs -- the denominator for each player's durability %. Same
    pattern already validated in the v3.2 player-side games-played
    check."""
    cur = conn.cursor()
    cur.execute("""
        SELECT MAX(games_played) FROM (
            SELECT player_id, COUNT(DISTINCT game_id) AS games_played
            FROM game_logs
            WHERE season_id = %s
            GROUP BY player_id
        ) per_player
    """, (season_id,))
    row = cur.fetchone()
    cur.close()
    return row[0] if row and row[0] else None


# =========================
# NBA-team stacking context
# =========================

def get_player_mode_team(conn, nba_player_id: int, season_id: str) -> int | None:
    """Each player's most-played real NBA team that season -- same
    simplification used in analyze_stacking_roster_construction.py, not
    the exact team for a specific week (no game-level team context
    available without a separate score-matching step)."""
    cur = conn.cursor()
    cur.execute("""
        SELECT team_id, COUNT(*) AS n_games
        FROM game_logs
        WHERE player_id = %s AND season_id = %s
        GROUP BY team_id
        ORDER BY n_games DESC
        LIMIT 1
    """, (nba_player_id, season_id))
    row = cur.fetchone()
    cur.close()
    return row[0] if row else None


def get_player_team_with_fallback(conn, nba_player_id: int, season_id: str) -> int | None:
    """Same one-year-back fallback as get_spike_profile -- REAL BUG
    FIXED 8/26/26: an earlier version passed the raw --season-id
    straight through with no fallback, so for a season with no games
    logged yet (e.g. 22026 before it starts), every team lookup
    silently returned None and the stacking check found nothing even
    when a real stack existed (confirmed case: two players both really
    on Golden State, season's real stats coming from the 22025
    fallback, team lookup querying 22026 and finding zero rows)."""
    team = get_player_mode_team(conn, nba_player_id, season_id)
    if team is not None:
        return team
    fallback_season_id = str(int(season_id) - 1)
    return get_player_mode_team(conn, nba_player_id, fallback_season_id)


def find_stacked_groups(report: list[dict], conn, season_id: str) -> dict:
    """Groups report players (those with a real nba_player_id) by real
    NBA team, using the same one-year fallback as spike profiles --
    both ranked and unranked players are checked, since a player who
    never clears the spike bar can still contribute to a real stack."""
    by_team = defaultdict(list)
    for r in report:
        if r.get("nba_player_id") is None:
            continue
        team_id = get_player_team_with_fallback(conn, r["nba_player_id"], season_id)
        if team_id is not None:
            by_team[team_id].append(r["player_name"])
    return {team_id: names for team_id, names in by_team.items() if len(names) >= 2}


# =========================
# Spike profile (identical logic to waiver_wire_finder.py's get_spike_profile)
# =========================

def get_spike_profile(conn, nba_player_id: int, season_id: str, season_max_games: dict) -> dict | None:
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

    ALSO returns a real durability % (games_played / that season's max
    games by any player) -- see module docstring. season_max_games is
    a dict of {season_id: max_games}, passed in rather than queried
    per-player.

    Returns None if the player clears the bar in neither season.
    """
    fallback_season_id = str(int(season_id) - 1)

    cur = conn.cursor()
    cur.execute("""
        SELECT pt.avg_fantasy_score, pt.stddev_fantasy_score, pt.tier, pt.rank_in_season, gl.games_played
        FROM player_tiers pt
        JOIN (
            SELECT player_id, season_id, COUNT(DISTINCT game_id) AS games_played
            FROM game_logs
            GROUP BY player_id, season_id
        ) gl ON gl.player_id = pt.player_id AND gl.season_id = pt.season_id
        WHERE pt.player_id = %s AND pt.season_id = %s
    """, (nba_player_id, season_id))
    row = cur.fetchone()
    used_season_id = season_id

    if row is None:
        cur.execute("""
            SELECT pt.avg_fantasy_score, pt.stddev_fantasy_score, pt.tier, pt.rank_in_season, gl.games_played
            FROM player_tiers pt
            JOIN (
                SELECT player_id, season_id, COUNT(DISTINCT game_id) AS games_played
                FROM game_logs
                GROUP BY player_id, season_id
            ) gl ON gl.player_id = pt.player_id AND gl.season_id = pt.season_id
            WHERE pt.player_id = %s AND pt.season_id = %s
        """, (nba_player_id, fallback_season_id))
        row = cur.fetchone()
        used_season_id = fallback_season_id

    cur.close()
    if row is None:
        return None

    avg, stddev, tier, rank_in_season, games_played = row
    avg, stddev = float(avg), float(stddev)
    spike_bar = round(avg + 1.25 * stddev, 2)

    max_games = season_max_games.get(used_season_id)
    durability_pct = round(games_played / max_games, 3) if max_games else None
    low_durability = durability_pct is not None and durability_pct < DURABILITY_FLAG_THRESHOLD

    return {
        "avg_fantasy_score": avg,
        "stddev_fantasy_score": stddev,
        "tier": tier,
        "rank_in_season": rank_in_season,
        "spike_bar": spike_bar,
        "profile_season_id": used_season_id,
        "is_fallback_season": used_season_id != season_id,
        "games_played": games_played,
        "durability_pct": durability_pct,
        "low_durability": low_durability,
    }


# =========================
# Report
# =========================

def build_report(conn, league_id: str, roster_id: int, season_id: str) -> list[dict]:
    players = get_opponent_players(conn, league_id, roster_id)
    fallback_season_id = str(int(season_id) - 1)
    season_max_games = {
        season_id: get_season_max_games(conn, season_id),
        fallback_season_id: get_season_max_games(conn, fallback_season_id),
    }

    report = []
    for p in players:
        if p["nba_player_id"] is None:
            report.append({**p, "spike_bar": None, "note": "no crosswalk match -- unmatched Sleeper player"})
            continue
        profile = get_spike_profile(conn, p["nba_player_id"], season_id, season_max_games)
        if profile is None:
            report.append({**p, "spike_bar": None, "note": "never clears the spike bar in season_id or the year before"})
            continue
        report.append({**p, **profile, "note": None})

    # Rank threats (spike_bar known) first, descending; unranked players after.
    ranked = sorted([r for r in report if r["spike_bar"] is not None], key=lambda r: -r["spike_bar"])
    unranked = [r for r in report if r["spike_bar"] is None]
    return ranked + unranked


def print_report(report: list[dict], my_roster_id: int, opp_roster_id: int, week: int, stacked_groups: dict):
    opp_owner = next((r["owner_name"] for r in report if r["owner_name"]), "Unknown")
    print(f"\nWeek {week}: roster_id {my_roster_id} vs roster_id {opp_roster_id} ({opp_owner})")
    print(f"{'Player':<24} {'Tier':<8} {'Rank':>5} {'Avg':>7} {'StdDev':>8} {'SpikeBar':>9} {'Durability':>11}  Profile")
    print("-" * 100)
    for r in report:
        if r["spike_bar"] is None:
            print(f"{r['player_name'] or r['sleeper_player_id']:<24} -- {r['note']}")
            continue
        profile_note = f"season {r['profile_season_id']}"
        if r["is_fallback_season"]:
            profile_note += " (fallback)"
        durability_str = f"{r['durability_pct']*100:.0f}%" if r["durability_pct"] is not None else "n/a"
        if r["low_durability"]:
            durability_str += " \u26a0"
        print(f"{r['player_name']:<24} {r['tier']:<8} {r['rank_in_season']:>5} {r['avg_fantasy_score']:>7.2f} "
              f"{r['stddev_fantasy_score']:>8.2f} {r['spike_bar']:>9.2f} {durability_str:>11}  {profile_note}")
    print("\nSpikeBar = avg + 1.25*stddev (players shown already clear the ownable-pool bar of 35).")
    print("This ranks season-long capability, not a prediction for this specific week.")
    print("'(fallback)' = current season has no games yet; using the player's most recent prior season.")
    print("A player missing from this list either never clears the spike bar, or has <20 games that season.")
    print(f"Durability = games_played / that season's real max games by any player. "
          f"'\u26a0' = below {DURABILITY_FLAG_THRESHOLD*100:.0f}% -- a real recent games-missed pattern this "
          f"player's avg_fantasy_score doesn't reflect on its own (see v3.2 Embiid/AD finding).")

    if stacked_groups:
        print("\nNBA-team stacking on this roster:")
        for team_id, names in stacked_groups.items():
            print(f"  {', '.join(names)} (real NBA team_id {team_id})")
        print("Confirmed real effect: increases this roster's week-to-week VARIANCE (bigger swings both ways),")
        print("but no confirmed effect on their actual win rate once team quality is controlled for --")
        print("informational, not a strength or weakness signal on its own.")


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
    stacked_groups = find_stacked_groups(report, conn, args.season_id)
    conn.close()

    print_report(report, args.roster_id, opp_roster_id, args.week, stacked_groups)


if __name__ == "__main__":
    main()