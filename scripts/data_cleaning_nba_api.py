"""
Cleans a raw nba_api PlayerGameLog DataFrame into 3NF: splits MATCHUP into
atomic team_id/opponent_team_id/is_home, drops derived/redundant columns
(percentages, REB total, video flag), and reorders for readability.
"""

from reorder_columns import reorder_columns

def clean_gamelog(df, team_lookup):
    df['is_home'] = ~df['MATCHUP'].str.contains('@')
    df['team'] = df['MATCHUP'].str.split().str[0]
    df['team_id'] = df['team'].map(team_lookup)
    df['opponent'] = df['MATCHUP'].str.split().str[-1]
    df['opponent_team_id'] = df['opponent'].map(team_lookup)

    df = df.drop(columns=['FG_PCT', 'FG3_PCT', 'FT_PCT', 'REB', 'VIDEO_AVAILABLE', 'MATCHUP', 'team', 'opponent'])
    df = reorder_columns(df, ['team_id', 'opponent_team_id', 'is_home'], after_column='GAME_DATE')

    return df
