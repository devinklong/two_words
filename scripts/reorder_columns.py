"""
Moves a list of DataFrame columns to sit immediately after a specified
anchor column, without manually rebuilding the full column list.
"""

def reorder_columns(df, columns_to_move, after_column):
    cols = df.columns.tolist()

    for col in columns_to_move:
        cols.remove(col)

    insert_at = cols.index(after_column) + 1
    cols[insert_at:insert_at] = columns_to_move

    return df[cols]
