"""
Cleans nba_api's BoxScoreTraditionalV3 output into the same 3NF shape
game_logs already expects. V3 uses camelCase column names (confirmed
8/10/26 against a real pull, game_id 0022400909) -- DIFFERENT from V2's
UPPER_SNAKE_CASE, which the original version of this file was written
against before V2 turned out to return no data for the 2025-26 season.

Takes BOTH the player-level dataframe (index 0 of get_data_frames()) and
the team-level dataframe (index 2) -- team totals give a real WL result
directly, instead of re-summing player rows to guess at it.

Box scores still don't carry a home/visitor indicator anywhere in any of
the 3 returned frames -- opponent_team_id/is_home still have to come from
a separate scoreboard call, passed in the same way as before.
"""

def clean_boxscore(player_stats_df, team_stats_df, game_id, game_date, season_id,
                    home_team_id, visitor_team_id):
    df = player_stats_df.copy()

    # DNP/inactive players -- comment is non-empty text (e.g. "DNP - COACH'S
    # DECISION") for anyone who didn't play. Filtering on comment rather
    # than minutes, since minutes' exact empty-value representation for a
    # DNP row (NaN vs "" vs "0:00") hasn't been directly confirmed yet --
    # comment is the more reliable signal either way.
    df = df[df["comment"].isna() | (df["comment"].str.strip() == "")]

    df["team_id"] = df["teamId"]
    df["opponent_team_id"] = df["teamId"].apply(
        lambda tid: visitor_team_id if tid == home_team_id else home_team_id
    )
    df["is_home"] = df["teamId"] == home_team_id
    df["game_id"] = game_id
    df["game_date"] = game_date
    df["season_id"] = season_id

    # Real team point totals from the team-level frame -- more reliable
    # than summing player rows (rounding/late-scratch edge cases avoided).
    team_points = team_stats_df.set_index("teamId")["points"].to_dict()
    team_ids = list(team_points.keys())
    if len(team_ids) == 2:
        opponent_of = {team_ids[0]: team_ids[1], team_ids[1]: team_ids[0]}
        df["wl"] = df["teamId"].apply(
            lambda tid: "W" if team_points[tid] > team_points[opponent_of[tid]] else "L"
        )
    else:
        df["wl"] = None  # unexpected shape -- flag rather than guess

    df = df.rename(columns={
        "personId": "player_id",
        "minutes": "minutes",  # V3 already uses this exact name, no-op rename kept for clarity
        "fieldGoalsMade": "fgm", "fieldGoalsAttempted": "fga",
        "threePointersMade": "fg3m", "threePointersAttempted": "fg3a",
        "freeThrowsMade": "ftm", "freeThrowsAttempted": "fta",
        "reboundsOffensive": "oreb", "reboundsDefensive": "dreb",
        "assists": "ast", "steals": "stl", "blocks": "blk", "turnovers": "tov",
        "foulsPersonal": "pf", "points": "pts", "plusMinusPoints": "plus_minus",
    })

    return df[[
        "game_id", "player_id", "team_id", "opponent_team_id", "season_id",
        "game_date", "is_home", "wl", "minutes", "fgm", "fga", "fg3m", "fg3a",
        "ftm", "fta", "oreb", "dreb", "ast", "stl", "blk", "tov", "pf", "pts",
        "plus_minus",
    ]]
