"""
Phase 2 validation step: reruns the exact same weekly-outcome simulation
as scripts/grid_search_lock_threshold.py, but against the VALIDATE split
(2024-26) only, for ONE specific (k, threshold) combo you choose after
reviewing the grid search's training-split results.

Deliberately a separate script, run manually, rather than something the
grid search calls automatically -- the whole point of a held-out
validation split is that you commit to a choice from training data
alone, THEN check it once. Auto-validating inside the search would let
the "best" combo get picked partly based on validation performance,
defeating the purpose.

CENTRALIZED 8/22/26 (docs/architecture_risks.md #8): VALIDATE_SEASONS and
REPLACEMENT_LEVEL now imported from scripts/constants.py instead of
redefined here -- no behavior change, same literal values as before.

Run from the project root:
    python scripts/validate_lock_threshold.py K THRESHOLD

Example:
    python scripts/validate_lock_threshold.py 0.75 42
"""

import sys

import pandas as pd

from db_connection import get_connection
from constants import VALIDATE_SEASONS, REPLACEMENT_LEVEL

SIMULATION_QUERY = """
WITH pool AS (
    SELECT player_id, season_id
    FROM player_season_fantasy_stats
    WHERE games_played >= 20
      AND avg_fantasy_score + %(k)s * stddev_fantasy_score >= %(t)s
),
pool_games AS (
    SELECT gfswe.*
    FROM game_fantasy_scores_weekly_effective gfswe
    JOIN pool p ON p.player_id = gfswe.player_id AND p.season_id = gfswe.season_id
    WHERE gfswe.season_id IN %(validate_seasons)s
),
first_lock AS (
    SELECT DISTINCT ON (player_id, season_id, week_number)
        player_id, season_id, week_number, fantasy_score AS locked_score
    FROM pool_games
    WHERE fantasy_score >= %(t)s
    ORDER BY player_id, season_id, week_number, game_date ASC
),
last_game AS (
    SELECT DISTINCT ON (player_id, season_id, week_number)
        player_id, season_id, week_number, fantasy_score AS final_score
    FROM pool_games
    WHERE games_remaining_in_week = 0
    ORDER BY player_id, season_id, week_number, game_date DESC
),
oracle AS (
    SELECT player_id, season_id, week_number, MAX(fantasy_score) AS oracle_score
    FROM pool_games
    GROUP BY player_id, season_id, week_number
),
naive_first_lock AS (
    SELECT DISTINCT ON (player_id, season_id, week_number)
        player_id, season_id, week_number, fantasy_score AS naive_locked_score
    FROM pool_games
    WHERE fantasy_score >= 30.3
    ORDER BY player_id, season_id, week_number, game_date ASC
),
player_weeks AS (
    SELECT DISTINCT player_id, season_id, week_number FROM pool_games
),
banked AS (
    SELECT
        pw.player_id, pw.season_id, pw.week_number,
        COALESCE(fl.locked_score, GREATEST(lg.final_score, %(replacement)s)) AS policy_banked_score,
        COALESCE(nfl.naive_locked_score, GREATEST(lg.final_score, %(replacement)s)) AS naive_banked_score,
        o.oracle_score
    FROM player_weeks pw
    JOIN oracle o USING (player_id, season_id, week_number)
    JOIN last_game lg USING (player_id, season_id, week_number)
    LEFT JOIN first_lock fl USING (player_id, season_id, week_number)
    LEFT JOIN naive_first_lock nfl USING (player_id, season_id, week_number)
)
SELECT
    COUNT(DISTINCT player_id || '-' || season_id) AS pool_player_seasons,
    COUNT(*) AS player_weeks_simulated,
    ROUND(AVG(policy_banked_score)::NUMERIC, 3) AS avg_policy_banked,
    ROUND(AVG(naive_banked_score)::NUMERIC, 3) AS avg_naive_banked,
    ROUND(AVG(oracle_score)::NUMERIC, 3) AS avg_oracle,
    ROUND((AVG(policy_banked_score) - AVG(naive_banked_score))::NUMERIC, 3) AS policy_edge_over_naive
FROM banked;
"""


def main():
    if len(sys.argv) != 3:
        print("Usage: python scripts/validate_lock_threshold.py K THRESHOLD")
        print("Example: python scripts/validate_lock_threshold.py 0.75 42")
        sys.exit(1)

    k = float(sys.argv[1])
    t = float(sys.argv[2])

    conn = get_connection()
    df = pd.read_sql(
        SIMULATION_QUERY,
        conn,
        params={"k": k, "t": t, "validate_seasons": VALIDATE_SEASONS, "replacement": REPLACEMENT_LEVEL},
    )
    conn.close()

    row = df.iloc[0]
    print(f"VALIDATE split (2024-26) results for k={k}, threshold={t}:")
    print(f"  pool size:             {int(row['pool_player_seasons'])} player-seasons")
    print(f"  player-weeks:          {int(row['player_weeks_simulated'])}")
    print(f"  avg policy banked:     {row['avg_policy_banked']}")
    print(f"  avg naive banked:      {row['avg_naive_banked']}")
    print(f"  avg oracle:            {row['avg_oracle']}")
    print(f"  edge over naive:       {row['policy_edge_over_naive']:+.3f}")
    print("\nCompare this edge to what the same (k, threshold) showed on the TRAIN split.")
    print("A similar or larger edge here is a good sign; a much smaller or negative edge")
    print("means the combo may have been overfit to 2021-24 and needs reconsidering.")


if __name__ == "__main__":
    main()
