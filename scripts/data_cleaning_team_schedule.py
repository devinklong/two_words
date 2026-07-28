"""
Cleaning function for raw nba_api LeagueGameFinder team schedule data.
Converts raw team schedule DataFrame into a 3NF-compliant structure:
- Splits MATCHUP into atomic opponent_team_id and is_home columns
- Drops derived/redundant columns (percentages, REB total)
- Reorders columns for readability using reorder_columns()
"""

from scripts.reorder_columns import reorder_columns

def clean_team_schedule(df, team_lookup):
    # Split MATCHUP into atomic columns
    df['is_home'] = ~df['MATCHUP'].str.contains('@')
    df['opponent'] = df['MATCHUP'].str.split().str[-1]
    df['opponent_team_id'] = df['opponent'].map(team_lookup)

    # Drop derived/redundant columns
    df = df.drop(columns=['FG_PCT', 'FG3_PCT', 'FT_PCT', 'REB', 'MATCHUP', 'opponent'])

    # Reorder columns
    df = reorder_columns(df, ['opponent_team_id', 'is_home'], after_column='GAME_DATE')

    return df
