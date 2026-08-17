"""
Testing tool for the lock/hold decision engine outside of SQL. Checks the
database FIRST -- if the daily pipeline has already loaded this game,
game_lock_signal already has the real, SQL-computed answer, so this just
returns that directly (no Python formula involved, zero risk of drift).
Only falls back to a live nba_api pull (default) or manual stats
(--manual) when the game genuinely isn't in the DB yet -- e.g. checking
right after a game ends, ahead of the daily batch, or testing a
hypothetical stat line that was never real.

team_id is auto-resolved (8/11/26) from the player's most recent game_logs
row, not required as a CLI arg anymore -- game_logs already knows which
team someone plays for, so there's no reason to make you supply it by
hand for every check. Still overridable via --team-id for the edge case
of a very recent trade the DB hasn't caught up to yet, or a
brand-new player with no game_logs history at all (auto-resolution has
nothing to find in that case and will raise a clear error asking for it).

CENTRALIZED 8/15/26 (docs/patch_list.md #1): the fallback path now calls
the shared lock_bar() SQL function (models/lock_bar_function.sql) instead
of reimplementing GREATEST(35, avg + 0.5*stddev) in Python -- same fix
applied to models/game_lock_signal.sql. Removes the "MUST match
game_lock_signal.sql's CASE logic exactly" risk entirely, since there's
now only one place the formula is actually written.

Also fixed 8/15/26: get_player_context() previously queried a `player_tiers`
table that never existed as such -- corrected to query
player_season_fantasy_stats, the real source (this was a real, if rarely-
triggered, bug -- confirmed harmless once traced: the DB-first path
covers almost every real use, so this fallback rarely fires).

Run (checks DB first, live pull as fallback, team_id auto-resolved):
    python scripts/lock_decision_input.py PLAYER_ID --game-id GAME_ID \
        --season-id 22024 --game-date 2025-03-07

Run (manual stat line, skips the DB check and live pull entirely):
    python scripts/lock_decision_input.py PLAYER_ID --manual \
        --pts 31 --oreb 4 --dreb 17 --ast 22 --stl 3 --blk 0 --tov 4 \
        --fgm 13 --fga 22 --ftm 2 --fta 3 --fg3m 3 \
        --season-id 22024 --game-date 2025-03-07

Run (override team_id, e.g. a very recent trade):
    python scripts/lock_decision_input.py PLAYER_ID --game-id GAME_ID \
        --season-id 22024 --game-date 2025-03-07 --team-id 1610612743
"""

import argparse
import sys

from nba_api.stats.endpoints import playergamelog

from db_connection import get_connection

MANUAL_STATS = ["pts", "oreb", "dreb", "ast", "stl", "blk", "tov", "fgm", "fga", "ftm", "fta", "fg3m"]


# =========================
# team_id resolution
# =========================

def resolve_team_id(conn, player_id: int) -> int:
    """
    Looks up the player's most recent known team from their own
    game_logs history -- reuses data that's already there instead of
    requiring --team-id by hand for every check. Trades are rare enough
    that "most recent game they actually played" is a reliable default;
    --team-id still overrides this for the edge case of a trade the DB
    hasn't caught up to yet.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT team_id FROM game_logs
        WHERE player_id = %s
        ORDER BY game_date DESC
        LIMIT 1
    """, (player_id,))
    row = cur.fetchone()
    cur.close()
    if row is None:
        raise ValueError(
            f"player_id={player_id} has no game_logs history to resolve team_id from "
            f"-- pass --team-id explicitly (e.g. for a brand-new player with no games played yet)."
        )
    return row[0]


# =========================
# Database-first lookup
# =========================

def get_from_db(conn, player_id: int, game_id: str, season_id: str) -> dict | None:
    """
    Returns the real, already-computed decision if this game has been
    loaded and processed by the pipeline, else None. This is the fast
    path and should cover almost every real use once daily loading is
    running -- the Python scoring path below is only a fallback.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT fantasy_score, lock_bar, lock_signal, percentage_to_lock,
               tier, games_remaining_in_week, effective_games_remaining_in_week
        FROM game_lock_signal
        WHERE player_id = %s AND game_id = %s AND season_id = %s
    """, (player_id, game_id, season_id))
    row = cur.fetchone()
    cur.close()
    if row is None:
        return None
    fantasy_score, lock_bar, lock_signal, percentage_to_lock, tier, grw, egrw = row
    return {
        "source": "database",
        "fantasy_score": float(fantasy_score),
        "lock_bar": float(lock_bar),
        "lock_signal": lock_signal,
        "percentage_to_lock": float(percentage_to_lock) if percentage_to_lock is not None else None,
        "tier": tier,
        "games_remaining_in_week": grw,
        "effective_games_remaining_in_week": float(egrw),
    }


# =========================
# Scoring formula (Sleeper league settings) -- MUST match game_fantasy_scores
# view's SQL exactly. Only reached when the DB lookup above comes back
# empty. Confirmed against a real Sleeper score (Jokić vs PHX 3/7/25,
# 113.10) that double-double (+3) and triple-double (+5) STACK, not one
# replacing the other. Technical/flagrant penalties (-2 each) are NOT
# computed -- game_logs has no technical/flagrant columns, only generic
# `pf`; accepted gap, see methodology_notes.md.
# =========================

def compute_fantasy_score(stats: dict) -> float:
    pts, oreb, dreb, ast, stl, blk, tov = (
        stats["pts"], stats["oreb"], stats["dreb"], stats["ast"],
        stats["stl"], stats["blk"], stats["tov"],
    )
    fgm, fga, ftm, fta, fg3m = (
        stats["fgm"], stats["fga"], stats["ftm"], stats["fta"], stats["fg3m"],
    )
    reb = oreb + dreb

    score = 0.0
    score += 0.5 * pts
    score += 1.5 * reb + 0.5 * oreb  # extra OREB bonus stacks on top of the 1.5/reb rate
    score += 2 * ast
    score += 3 * stl
    score += 3 * blk
    score -= 1 * tov
    score += 1 * fgm
    score -= 0.45 * fga
    score += 1 * ftm
    score -= 0.5 * fta
    score += 0.5 * fg3m

    categories_10plus = sum(1 for v in (pts, reb, ast, stl, blk) if v >= 10)
    if categories_10plus >= 2:
        score += 3
    if categories_10plus >= 3:
        score += 5

    if pts >= 40:
        score += 2
    if pts >= 50:
        score += 2  # stacks with the 40+ bonus
    if ast >= 15:
        score += 1
    if reb >= 20:
        score += 1

    return round(score, 2)


# =========================
# Live data pull (fallback only)
# =========================

def _season_id_to_nba_format(season_id: str) -> str:
    """'22024' -> '2024-25'"""
    start_year = int(season_id[1:])
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def fetch_live_stats(player_id: int, game_id: str, season_id: str) -> dict:
    """
    Pulls the player's full season log and filters to game_id. Reuses
    PlayerGameLog (same endpoint load_game_logs.py already uses) -- fine
    for one-off testing, but NOT the shape the eventual daily pipeline
    should use (that needs a date-scoped box-score pull, not a per-player
    season call every time -- see project notes on daily data flow).
    """
    season = _season_id_to_nba_format(season_id)
    raw = playergamelog.PlayerGameLog(player_id=player_id, season=season).get_data_frames()[0]
    match = raw[raw["Game_ID"] == game_id]
    if match.empty:
        raise ValueError(f"No game found for player_id={player_id}, game_id={game_id}, season={season}")
    row = match.iloc[0]
    return {
        "pts": row["PTS"], "oreb": row["OREB"], "dreb": row["DREB"],
        "ast": row["AST"], "stl": row["STL"], "blk": row["BLK"], "tov": row["TOV"],
        "fgm": row["FGM"], "fga": row["FGA"], "ftm": row["FTM"], "fta": row["FTA"],
        "fg3m": row["FG3M"],
    }


# =========================
# Player context (tier, lock_bar inputs) from the DB -- fallback only.
# CORRECTED 8/15/26: was querying a `player_tiers` table that never
# existed under this name/shape for this purpose -- player_season_
# fantasy_stats is the real source (confirmed against the live schema).
# `tier` isn't a real stored column here either, so it's dropped.
# =========================

def get_player_context(conn, player_id: int, season_id: str) -> dict:
    cur = conn.cursor()
    cur.execute("""
        SELECT avg_fantasy_score, stddev_fantasy_score
        FROM player_season_fantasy_stats
        WHERE player_id = %s AND season_id = %s
    """, (player_id, season_id))
    row = cur.fetchone()
    cur.close()
    if row is None:
        raise ValueError(f"player_id={player_id} season_id={season_id} has no player_season_fantasy_stats row")
    avg, stddev = row
    return {"avg_fantasy_score": float(avg), "stddev_fantasy_score": float(stddev)}


def get_lock_bar(conn, avg_fantasy_score: float, stddev_fantasy_score: float) -> float:
    """
    Calls the shared lock_bar() SQL function directly -- no args beyond
    avg/stddev, so this gets the same validated defaults (floor=35,
    ceiling_multiplier=0.5) as models/game_lock_signal.sql, with zero
    risk of drift between the two.
    """
    cur = conn.cursor()
    cur.execute("SELECT lock_bar(%s, %s)", (avg_fantasy_score, stddev_fantasy_score))
    result = cur.fetchone()[0]
    cur.close()
    return float(result)


def get_games_remaining(conn, team_id: int, game_date: str, season_id: str) -> dict:
    """
    Effective (B2B-discounted) and raw games remaining in the player's
    fantasy week AFTER this game -- mirrors
    rebuild_materialized_views.sql's effective_games_remaining_in_week
    subquery, but computed standalone so it works for a game whose stats
    haven't been loaded yet. team_schedule already has the full season's
    dates loaded in advance, so this also works for a hypothetical/future
    game date, not just a past one.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT
            COUNT(*) AS games_remaining,
            COALESCE(ROUND(SUM(
                CASE WHEN b2b.is_second_night_of_b2b THEN 0.9805 ELSE 1.0 END
            ), 3), 0) AS effective_games_remaining
        FROM fantasy_weeks fw
        JOIN team_schedule_b2b_flags b2b
            ON b2b.season_id = fw.season_id AND b2b.team_id = %(team_id)s
        WHERE fw.season_id = %(season_id)s
          AND %(game_date)s BETWEEN fw.week_start_date AND fw.week_end_date
          AND b2b.game_date > %(game_date)s
          AND b2b.game_date BETWEEN fw.week_start_date AND fw.week_end_date
    """, {"team_id": team_id, "season_id": season_id, "game_date": game_date})
    games_remaining, effective_games_remaining = cur.fetchone()
    cur.close()
    return {
        "games_remaining_in_week": games_remaining,
        "effective_games_remaining_in_week": float(effective_games_remaining),
    }


def get_hold_probability(conn, tier: str, effective_games_remaining: float) -> float:
    cur = conn.cursor()
    cur.execute("SELECT hold_win_probability_by_tier(%s, %s)", (effective_games_remaining, tier))
    result = cur.fetchone()[0]
    cur.close()
    return float(result)


def get_tier(conn, player_id: int, season_id: str) -> str | None:
    """
    Separate lookup, only needed for the HOLD path's percentage_to_lock
    calc (which is keyed by tier, via player_tiers -- the REAL view of
    that name). Kept separate from get_player_context() since a player
    can have avg/stddev (player_season_fantasy_stats, full NBA universe)
    without clearing the ownable-pool bar that player_tiers requires --
    in that case tier is genuinely None, and percentage_to_lock can't be
    computed (a HOLD decision on a non-pool player is an edge case this
    fallback doesn't fully support; PASS/LOCK still work fine).
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT tier FROM player_tiers
        WHERE player_id = %s AND season_id = %s
    """, (player_id, season_id))
    row = cur.fetchone()
    cur.close()
    return row[0] if row else None


# =========================
# Decision -- MUST match game_lock_signal.sql's CASE logic exactly.
# Only reached on the fallback path (DB lookup came back empty).
# =========================

def decide(fantasy_score: float, context: dict, games_remaining: dict, conn) -> dict:
    lock_bar = get_lock_bar(conn, context["avg_fantasy_score"], context["stddev_fantasy_score"])

    if fantasy_score >= lock_bar:
        signal = "LOCK"
        percentage_to_lock = None
    elif games_remaining["games_remaining_in_week"] == 0:
        signal = "PASS"
        percentage_to_lock = None
    else:
        signal = "HOLD"
        tier = get_tier(conn, context["player_id"], context["season_id"])
        if tier is None:
            percentage_to_lock = None
        else:
            hold_prob = get_hold_probability(conn, tier, games_remaining["effective_games_remaining_in_week"])
            percentage_to_lock = round(1 - hold_prob, 4)

    return {
        "source": "python_fallback",
        "lock_signal": signal,
        "lock_bar": round(lock_bar, 2),
        "fantasy_score": fantasy_score,
        "percentage_to_lock": percentage_to_lock,
        "games_remaining_in_week": games_remaining["games_remaining_in_week"],
        "effective_games_remaining_in_week": games_remaining["effective_games_remaining_in_week"],
    }


# =========================
# Output
# =========================

def print_result(result: dict):
    tag = "[from database]" if result["source"] == "database" else "[computed in Python -- game not yet in DB]"
    print(f"\n{tag}")
    print(f"Fantasy score: {result['fantasy_score']}")
    print(f"Lock bar: {result['lock_bar']}")
    print(f"Games remaining this week: {result['games_remaining_in_week']} "
          f"(effective: {result['effective_games_remaining_in_week']})")
    print(f"\n>>> {result['lock_signal']} <<<")
    if result["percentage_to_lock"] is not None:
        print(f"(percentage_to_lock: {result['percentage_to_lock']})")


# =========================
# CLI
# =========================

def main():
    parser = argparse.ArgumentParser(description="Evaluate a completed game against the lock/hold decision engine.")
    parser.add_argument("player_id", type=int)
    parser.add_argument("--season-id", required=True, help="e.g. 22024")
    parser.add_argument("--game-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--team-id", type=int, default=None,
                         help="Overrides auto-resolution from the player's most recent game_logs row")

    parser.add_argument("--game-id", help="nba_api Game_ID -- required unless --manual is used")
    parser.add_argument("--manual", action="store_true",
                         help="Skip the DB check and live pull; use manually-entered stats instead")

    for stat in MANUAL_STATS:
        parser.add_argument(f"--{stat}", type=float)

    args = parser.parse_args()
    conn = get_connection()

    if args.manual:
        missing = [s for s in MANUAL_STATS if getattr(args, s) is None]
        if missing:
            print(f"--manual requires all of: {', '.join('--' + m for m in missing)}")
            sys.exit(1)
        stats = {s: getattr(args, s) for s in MANUAL_STATS}
        fantasy_score = compute_fantasy_score(stats)
        context = get_player_context(conn, args.player_id, args.season_id)
        context["player_id"], context["season_id"] = args.player_id, args.season_id
        team_id = args.team_id or resolve_team_id(conn, args.player_id)
        games_remaining = get_games_remaining(conn, team_id, args.game_date, args.season_id)
        result = decide(fantasy_score, context, games_remaining, conn)

    else:
        if not args.game_id:
            print("--game-id is required unless --manual is used")
            sys.exit(1)

        result = get_from_db(conn, args.player_id, args.game_id, args.season_id)

        if result is None:
            stats = fetch_live_stats(args.player_id, args.game_id, args.season_id)
            fantasy_score = compute_fantasy_score(stats)
            context = get_player_context(conn, args.player_id, args.season_id)
            context["player_id"], context["season_id"] = args.player_id, args.season_id
            team_id = args.team_id or resolve_team_id(conn, args.player_id)
            games_remaining = get_games_remaining(conn, team_id, args.game_date, args.season_id)
            result = decide(fantasy_score, context, games_remaining, conn)

    conn.close()
    print_result(result)


if __name__ == "__main__":
    main()
