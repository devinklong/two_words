"""
Finds completed games for a date via ScoreboardV3 -- confirmed 8/10/26
against a real pull (game_date=2025-03-07, ISO format, worked directly).

V3 has NO homeTeamId/awayTeamId column anywhere in its 6 returned frames
-- unlike V2's GameHeader. Home/away has to be derived from the "game
leaders" frame (index 3), which has gameId/teamId/leaderType with
leaderType values 'home'/'away' directly. Cross-checked against frame 1's
gameCode field ("20250307/CLECHA" -- away-then-home tricode convention,
CLE away / CHA home) and both agree.

CAVEAT: only confirmed against 8 games in one batch, all with exactly one
'home' and one 'away' leader row. If a future date's leaders frame is
ever missing a row for some game (unclear if that can happen), that
game will silently be excluded here rather than crash -- worth an
occasional spot check against a real games-found count, not just trusting
this blindly forever.
"""

from nba_api.stats.endpoints import scoreboardv3


def get_completed_games_with_home_away(game_date: str) -> list[dict]:
    """
    game_date: 'YYYY-MM-DD' (ISO -- confirmed working format for V3).
    Returns [{'game_id', 'home_team_id', 'visitor_team_id'}, ...] for
    games with gameStatusText == 'Final' only -- in-progress/future games
    on this date are skipped, since there's no real box score for them yet.
    """
    sb = scoreboardv3.ScoreboardV3(game_date=game_date)
    dfs = sb.get_data_frames()
    game_header, leaders = dfs[1], dfs[3]

    # startswith, not ==: overtime games report "Final/OT", "Final/2OT",
    # etc., not a plain "Final" -- an exact match silently excluded every
    # OT game (confirmed 8/10/26: game_id 0022400909, a real OT game,
    # was missing under the old exact-match filter).
    final_game_ids = set(game_header[game_header["gameStatusText"].str.startswith("Final")]["gameId"])

    games = []
    for game_id in final_game_ids:
        rows = leaders[leaders["gameId"] == game_id]
        home_rows = rows[rows["leaderType"] == "home"]
        away_rows = rows[rows["leaderType"] == "away"]
        if home_rows.empty or away_rows.empty:
            print(f"WARNING: game_id={game_id} missing a home or away leader row -- skipping")
            continue
        games.append({
            "game_id": game_id,
            "home_team_id": home_rows.iloc[0]["teamId"],
            "visitor_team_id": away_rows.iloc[0]["teamId"],
        })

    return games


def find_home_visitor_for_game(game_id: str, game_date: str) -> tuple:
    """Single-game lookup, for verify_boxscore_columns.py's use case."""
    games = get_completed_games_with_home_away(game_date)
    match = [g for g in games if g["game_id"] == game_id]
    if not match:
        raise ValueError(f"game_id={game_id} not found as a completed game on {game_date}")
    return match[0]["home_team_id"], match[0]["visitor_team_id"]
