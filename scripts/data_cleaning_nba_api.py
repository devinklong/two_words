"""
Cleaning function for raw nba_api PlayerGameLog data.
Converts raw game log DataFrame into a 3NF-compliant structure:
- Splits MATCHUP into atomic opponent_team_id and is_home columns
- Drops derived/redundant columns (percentages, REB total, video flag)
- Reorders columns for readability
"""

def clean_gamelog(df, team_lookup):
    # Split MATCHUP into atomic columns
    df['is_home'] = ~df['MATCHUP'].str.contains('@')
    df['opponent'] = df['MATCHUP'].str.split().str[-1]
    df['opponent_team_id'] = df['opponent'].map(team_lookup)

    # Drop derived/redundant columns
    df = df.drop(columns=['FG_PCT', 'FG3_PCT', 'FT_PCT', 'REB', 'VIDEO_AVAILABLE', 'MATCHUP', 'opponent'])

    # Reorder columns
    cols = df.columns.tolist()
    cols.remove('is_home')
    cols.remove('opponent_team_id')
    insert_at = cols.index('GAME_DATE') + 1
    cols[insert_at:insert_at] = ['opponent_team_id', 'is_home']
    df = df[cols]

    return df
