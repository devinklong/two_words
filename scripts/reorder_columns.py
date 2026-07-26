"""
Reusable utility to reorder DataFrame columns.
Moves a list of columns to sit immediately after a specified anchor column,
without needing to manually rebuild the full column list each time.
"""

def reorder_columns(df, columns_to_move, after_column):
    cols = df.columns.tolist()

    for col in columns_to_move:
        cols.remove(col)

    insert_at = cols.index(after_column) + 1
    cols[insert_at:insert_at] = columns_to_move

    return df[cols]
