"""
Pulls one team's schedule/game log from nba_api. season is still required
(LeagueGameFinder needs it), but date_from/date_to (MM/DD/YYYY strings,
nba_api's expected format) optionally narrow it to a date range -- used by
load_daily_team_schedule.py to pull just recent games instead of the whole
season. Omit both for the original full-season backfill behavior.
"""

from nba_api.stats.endpoints import leaguegamefinder

def get_team_schedule(team_id, season, date_from=None, date_to=None):
    gamefinder = leaguegamefinder.LeagueGameFinder(
        team_id_nullable=team_id,
        season_nullable=season,
        season_type_nullable='Regular Season',
        date_from_nullable=date_from,
        date_to_nullable=date_to,
    )
    df = gamefinder.get_data_frames()[0]
    return df
