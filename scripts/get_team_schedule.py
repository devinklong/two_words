"""
Pulls one team's regular-season schedule/game log from nba_api for a given
season, excluding preseason/playoffs.
"""

from nba_api.stats.endpoints import leaguegamefinder

def get_team_schedule(team_id, season):
    gamefinder = leaguegamefinder.LeagueGameFinder(
        team_id_nullable=team_id,
        season_nullable=season,
        season_type_nullable='Regular Season'
    )
    df = gamefinder.get_data_frames()[0]
    return df
