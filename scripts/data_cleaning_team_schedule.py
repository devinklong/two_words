"""
Cleans a raw nba_api LeagueGameFinder team-schedule DataFrame into 3NF:
splits MATCHUP into atomic opponent_team_id/is_home, drops derived/redundant
columns (percentages, REB total), and reorders for readability.
"""

from reorder_columns import reorder_columns

def clean_team_schedule(df, team_lookup):
    df['is_home'] = ~df['MATCHUP'].str.contains('@')
    df['opponent'] = df['MATCHUP'].str.split().str[-1]
    df['opponent_team_id'] = df['opponent'].map(team_lookup)

    df = df.drop(columns=['FG_PCT', 'FG3_PCT', 'FT_PCT', 'REB', 'MATCHUP', 'opponent'])
    df = reorder_columns(df, ['opponent_team_id', 'is_home'], after_column='GAME_DATE')

    return df
