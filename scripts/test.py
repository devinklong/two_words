from nba_api.stats.endpoints import boxscoretraditionalv3
b = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id='0022400909')
dfs = b.get_data_frames()
for i, df in enumerate(dfs):
    print(f"--- frame {i} ---")
    print(df.columns.tolist())
    print(df.head(3))