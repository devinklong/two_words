"""
Cleans BoxScoreTraditionalV3's team-stats frame (index 2 of
get_data_frames()) into team_game_stats' 3NF shape -- raw counting stats
only, no percentages (fieldGoalsPercentage/threePointersPercentage/
freeThrowsPercentage/reboundsTotal all dropped as derivable/redundant,
same convention as data_cleaning_nba_api.py's player-level cleaning).

Takes home_team_id/visitor_team_id as params, same as clean_boxscore() --
this frame has no home/away indicator of its own, so that still has to
come from get_scoreboard_games.py's leaderType-based derivation.
"""

def clean_team_boxscore(team_stats_df, game_id, game_date, season_id, home_team_id, visitor_team_id):
    df = team_stats_df.copy()

    df["team_id"] = df["teamId"]
    df["opponent_team_id"] = df["teamId"].apply(
        lambda tid: visitor_team_id if tid == home_team_id else home_team_id
    )
    df["is_home"] = df["teamId"] == home_team_id
    df["game_id"] = game_id
    df["game_date"] = game_date
    df["season_id"] = season_id

    df = df.rename(columns={
        "fieldGoalsMade": "fgm", "fieldGoalsAttempted": "fga",
        "threePointersMade": "fg3m", "threePointersAttempted": "fg3a",
        "freeThrowsMade": "ftm", "freeThrowsAttempted": "fta",
        "reboundsOffensive": "oreb", "reboundsDefensive": "dreb",
        "assists": "ast", "steals": "stl", "blocks": "blk", "turnovers": "tov",
        "foulsPersonal": "pf", "points": "pts", "plusMinusPoints": "plus_minus",
    })

    return df[[
        "game_id", "team_id", "opponent_team_id", "season_id", "game_date",
        "is_home", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
        "oreb", "dreb", "ast", "stl", "blk", "tov", "pf", "pts", "plus_minus",
    ]]
