"""
scripts/analysis/export_diagnostics_to_csv.py

Runs every result-set query from univariate_diagnostics.sql,
bivariate_diagnostics.sql, and team_vs_opponent_rolling_stats.sql and
writes each to its own CSV under ./exports/ (sanity-check COUNT(*)
queries are skipped). Queries are embedded directly here rather than
parsed from the .sql files, so mirror any edits made there.

Usage: python scripts/analysis/export_diagnostics_to_csv.py
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # project root (scripts/analysis/ is two levels down)
from db_connection import get_connection

import pandas as pd

OUTPUT_DIR = Path(__file__).resolve().parent / "exports"

QUERIES = {

    # ---------- univariate_diagnostics.sql ----------

    "univariate_distribution_stats": """
        SELECT granularity, 'pace' AS metric, COUNT(*) AS n,
               ROUND(AVG(pace)::NUMERIC, 2) AS mean,
               ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pace)::NUMERIC, 2) AS median,
               ROUND(STDDEV(pace)::NUMERIC, 2) AS stddev,
               ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY pace)::NUMERIC, 2) AS p25,
               ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY pace)::NUMERIC, 2) AS p75,
               ROUND((PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY pace)
                    - PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY pace))::NUMERIC, 2) AS iqr
        FROM team_stats_all_granularities WHERE pace IS NOT NULL GROUP BY granularity
        UNION ALL
        SELECT granularity, 'off_rating', COUNT(*),
               ROUND(AVG(off_rating)::NUMERIC, 2),
               ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY off_rating)::NUMERIC, 2),
               ROUND(STDDEV(off_rating)::NUMERIC, 2),
               ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY off_rating)::NUMERIC, 2),
               ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY off_rating)::NUMERIC, 2),
               ROUND((PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY off_rating)
                    - PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY off_rating))::NUMERIC, 2)
        FROM team_stats_all_granularities WHERE off_rating IS NOT NULL GROUP BY granularity
        UNION ALL
        SELECT granularity, 'def_rating', COUNT(*),
               ROUND(AVG(def_rating)::NUMERIC, 2),
               ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY def_rating)::NUMERIC, 2),
               ROUND(STDDEV(def_rating)::NUMERIC, 2),
               ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY def_rating)::NUMERIC, 2),
               ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY def_rating)::NUMERIC, 2),
               ROUND((PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY def_rating)
                    - PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY def_rating))::NUMERIC, 2)
        FROM team_stats_all_granularities WHERE def_rating IS NOT NULL GROUP BY granularity
        ORDER BY metric, granularity;
    """,

    "univariate_outliers": """
        WITH stats AS (
            SELECT granularity,
                   AVG(pace) AS mean_pace, STDDEV(pace) AS sd_pace,
                   AVG(off_rating) AS mean_off, STDDEV(off_rating) AS sd_off,
                   AVG(def_rating) AS mean_def, STDDEV(def_rating) AS sd_def
            FROM team_stats_all_granularities GROUP BY granularity
        )
        SELECT g.game_id, g.team_id, g.granularity, g.game_date,
               g.pace, g.off_rating, g.def_rating,
               ROUND((ABS(g.pace - s.mean_pace) / NULLIF(s.sd_pace, 0))::NUMERIC, 2) AS pace_z,
               ROUND((ABS(g.off_rating - s.mean_off) / NULLIF(s.sd_off, 0))::NUMERIC, 2) AS off_z,
               ROUND((ABS(g.def_rating - s.mean_def) / NULLIF(s.sd_def, 0))::NUMERIC, 2) AS def_z
        FROM team_stats_all_granularities g
        JOIN stats s ON s.granularity = g.granularity
        WHERE ABS(g.pace - s.mean_pace) / NULLIF(s.sd_pace, 0) > 3
           OR ABS(g.off_rating - s.mean_off) / NULLIF(s.sd_off, 0) > 3
           OR ABS(g.def_rating - s.mean_def) / NULLIF(s.sd_def, 0) > 3
        ORDER BY g.granularity, pace_z DESC NULLS LAST;
    """,

    "univariate_league_trend_by_month": """
        SELECT season_id, DATE_TRUNC('month', game_date)::DATE AS month,
               ROUND(AVG(pace)::NUMERIC, 2) AS league_avg_pace,
               ROUND(AVG(off_rating)::NUMERIC, 2) AS league_avg_off_rating,
               COUNT(*) AS n_team_games
        FROM team_stats_all_granularities
        WHERE granularity = 'single_game'
        GROUP BY season_id, DATE_TRUNC('month', game_date)
        ORDER BY season_id, month;
    """,

    "univariate_team_percentile_ranking": """
        WITH latest_per_team_season AS (
            SELECT DISTINCT ON (team_id, season_id)
                team_id, season_id, pace, off_rating, def_rating
            FROM team_rolling_season_to_date_stats
            WHERE games_included > 0
            ORDER BY team_id, season_id, game_date DESC
        )
        SELECT season_id, team_id, pace, off_rating, def_rating,
               ROUND((100 * PERCENT_RANK() OVER (PARTITION BY season_id ORDER BY pace))::NUMERIC, 1) AS pace_pctile,
               ROUND((100 * PERCENT_RANK() OVER (PARTITION BY season_id ORDER BY off_rating))::NUMERIC, 1) AS off_rating_pctile,
               ROUND((100 * PERCENT_RANK() OVER (PARTITION BY season_id ORDER BY def_rating DESC))::NUMERIC, 1) AS def_rating_pctile
        FROM latest_per_team_season
        ORDER BY season_id, off_rating_pctile DESC;
    """,

    "univariate_season_over_season_stability": """
        WITH season_final AS (
            SELECT DISTINCT ON (team_id, season_id)
                team_id, season_id, pace, off_rating, def_rating
            FROM team_rolling_season_to_date_stats
            WHERE games_included > 0
            ORDER BY team_id, season_id, game_date DESC
        )
        SELECT team_id, COUNT(DISTINCT season_id) AS seasons_present,
               ROUND(AVG(pace)::NUMERIC, 2) AS avg_pace_across_seasons,
               ROUND(STDDEV(pace)::NUMERIC, 2) AS stddev_pace_across_seasons,
               ROUND(AVG(off_rating)::NUMERIC, 2) AS avg_off_rating_across_seasons,
               ROUND(STDDEV(off_rating)::NUMERIC, 2) AS stddev_off_rating_across_seasons
        FROM season_final GROUP BY team_id
        ORDER BY stddev_off_rating_across_seasons DESC NULLS LAST;
    """,

    "univariate_volatility_within_season": """
        SELECT team_id, season_id,
               ROUND(STDDEV(pace)::NUMERIC, 2) AS pace_volatility,
               ROUND(STDDEV(off_rating)::NUMERIC, 2) AS off_rating_volatility,
               ROUND(STDDEV(def_rating)::NUMERIC, 2) AS def_rating_volatility,
               COUNT(*) AS games_with_full_window
        FROM team_rolling_trailing10_advanced_stats
        WHERE games_included = 10
        GROUP BY team_id, season_id
        ORDER BY off_rating_volatility DESC NULLS LAST;
    """,

    "univariate_std_vs_trailing10_divergence": """
        SELECT rss.team_id, rss.season_id, rss.game_date,
               rss.games_included AS std_games_included, rss.pace AS std_pace,
               t10.games_included AS t10_games_included, t10.pace AS t10_pace,
               ROUND((t10.pace - rss.pace)::NUMERIC, 2) AS pace_gap,
               ROUND((t10.off_rating - rss.off_rating)::NUMERIC, 2) AS off_rating_gap,
               ROUND((t10.def_rating - rss.def_rating)::NUMERIC, 2) AS def_rating_gap
        FROM team_rolling_season_to_date_stats rss
        JOIN team_rolling_trailing10_advanced_stats t10
            ON t10.game_id = rss.game_id AND t10.team_id = rss.team_id
        WHERE rss.games_included >= 10 AND t10.games_included = 10
        ORDER BY ABS(t10.off_rating - rss.off_rating) DESC;
    """,

    # ---------- bivariate_diagnostics.sql ----------

    "bivariate_pace_vs_ratings_corr": """
        SELECT granularity,
               ROUND(CORR(pace, off_rating)::NUMERIC, 3) AS corr_pace_off_rating,
               ROUND(CORR(pace, def_rating)::NUMERIC, 3) AS corr_pace_def_rating,
               COUNT(*) AS n
        FROM team_stats_all_granularities
        WHERE pace IS NOT NULL AND off_rating IS NOT NULL AND def_rating IS NOT NULL
        GROUP BY granularity ORDER BY granularity;
    """,

    "bivariate_off_vs_def_corr": """
        SELECT granularity, ROUND(CORR(off_rating, def_rating)::NUMERIC, 3) AS corr_off_def, COUNT(*) AS n
        FROM team_stats_all_granularities
        WHERE off_rating IS NOT NULL AND def_rating IS NOT NULL
        GROUP BY granularity ORDER BY granularity;
    """,

    "bivariate_off_def_quadrants": """
        WITH latest AS (
            SELECT DISTINCT ON (team_id, season_id)
                team_id, season_id, off_rating, def_rating
            FROM team_rolling_season_to_date_stats
            WHERE games_included > 0
            ORDER BY team_id, season_id, game_date DESC
        ),
        medians AS (
            SELECT season_id,
                   PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY off_rating) AS med_off,
                   PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY def_rating) AS med_def
            FROM latest GROUP BY season_id
        )
        SELECT l.season_id, l.team_id, l.off_rating, l.def_rating,
               CASE WHEN l.off_rating >= m.med_off THEN 'good_off' ELSE 'bad_off' END AS off_bucket,
               CASE WHEN l.def_rating <= m.med_def THEN 'good_def' ELSE 'bad_def' END AS def_bucket
        FROM latest l JOIN medians m ON m.season_id = l.season_id
        ORDER BY l.season_id, off_bucket, def_bucket;
    """,

    "bivariate_stat_vs_sample_size": """
        WITH bucketed AS (
            SELECT granularity,
                   CASE WHEN games_included IS NULL THEN 'n/a (single_game)'
                        WHEN games_included < 5 THEN '0-4 games'
                        WHEN games_included < 10 THEN '5-9 games'
                        WHEN games_included < 20 THEN '10-19 games'
                        ELSE '20+ games' END AS sample_bucket,
                   off_rating
            FROM team_stats_all_granularities
            WHERE granularity IN ('season_to_date', 'trailing_10') AND games_included IS NOT NULL
        )
        SELECT granularity, sample_bucket,
               ROUND(AVG(off_rating)::NUMERIC, 2) AS avg_off_rating,
               ROUND(STDDEV(off_rating)::NUMERIC, 2) AS stddev_off_rating,
               COUNT(*) AS n
        FROM bucketed
        GROUP BY granularity, sample_bucket
        ORDER BY granularity,
            CASE sample_bucket WHEN '0-4 games' THEN 1 WHEN '5-9 games' THEN 2
                 WHEN '10-19 games' THEN 3 ELSE 4 END;
    """,

    "bivariate_stat_vs_home_away": """
        SELECT granularity, is_home,
               ROUND(AVG(pace)::NUMERIC, 2) AS avg_pace,
               ROUND(AVG(off_rating)::NUMERIC, 2) AS avg_off_rating,
               ROUND(AVG(def_rating)::NUMERIC, 2) AS avg_def_rating,
               COUNT(*) AS n
        FROM team_stats_all_granularities
        GROUP BY granularity, is_home ORDER BY granularity, is_home DESC;
    """,

    "bivariate_stat_vs_own_b2b": """
        SELECT g.granularity, b2b.is_second_night_of_b2b,
               ROUND(AVG(g.pace)::NUMERIC, 2) AS avg_pace,
               ROUND(AVG(g.off_rating)::NUMERIC, 2) AS avg_off_rating,
               ROUND(AVG(g.def_rating)::NUMERIC, 2) AS avg_def_rating,
               COUNT(*) AS n
        FROM team_stats_all_granularities g
        JOIN team_schedule_b2b_flags b2b
            ON b2b.team_id = g.team_id AND b2b.season_id = g.season_id AND b2b.game_date = g.game_date
        GROUP BY g.granularity, b2b.is_second_night_of_b2b
        ORDER BY g.granularity, b2b.is_second_night_of_b2b;
    """,

    "bivariate_stat_vs_opponent_b2b": """
        SELECT opp_b2b.is_second_night_of_b2b AS opponent_is_second_night_of_b2b,
               ROUND(AVG(tvo.own_pace)::NUMERIC, 2) AS avg_own_pace,
               ROUND(AVG(tvo.own_off_rating)::NUMERIC, 2) AS avg_own_off_rating,
               ROUND(AVG(tvo.own_def_rating)::NUMERIC, 2) AS avg_own_def_rating,
               COUNT(*) AS n
        FROM team_vs_opponent_trailing10 tvo
        JOIN team_schedule_b2b_flags opp_b2b
            ON opp_b2b.team_id = tvo.opponent_team_id
            AND opp_b2b.season_id = tvo.season_id AND opp_b2b.game_date = tvo.game_date
        GROUP BY opp_b2b.is_second_night_of_b2b
        ORDER BY opp_b2b.is_second_night_of_b2b;
    """,

    # ---------- team_vs_opponent_rolling_stats.sql ----------

    "team_vs_opponent_hypothesis_corr_trailing10": """
        SELECT
            ROUND(CORR(own_pace, own_off_rating)::NUMERIC, 3) AS corr_own_pace_own_off_rating,
            ROUND(CORR(opp_pace, own_off_rating)::NUMERIC, 3) AS corr_opp_pace_own_off_rating,
            ROUND(CORR(opp_def_rating, own_off_rating)::NUMERIC, 3) AS corr_opp_def_own_off_rating,
            ROUND(CORR(opp_off_rating, own_def_rating)::NUMERIC, 3) AS corr_opp_off_own_def_rating,
            ROUND(CORR(own_pace - opp_pace, own_off_rating)::NUMERIC, 3) AS corr_pace_gap_own_off_rating,
            COUNT(*) AS n
        FROM team_vs_opponent_trailing10;
    """,

    "team_vs_opponent_hypothesis_corr_season_to_date": """
        SELECT
            ROUND(CORR(own_pace, own_off_rating)::NUMERIC, 3) AS corr_own_pace_own_off_rating,
            ROUND(CORR(opp_pace, own_off_rating)::NUMERIC, 3) AS corr_opp_pace_own_off_rating,
            ROUND(CORR(opp_def_rating, own_off_rating)::NUMERIC, 3) AS corr_opp_def_own_off_rating,
            ROUND(CORR(opp_off_rating, own_def_rating)::NUMERIC, 3) AS corr_opp_off_own_def_rating,
            ROUND(CORR(own_pace - opp_pace, own_off_rating)::NUMERIC, 3) AS corr_pace_gap_own_off_rating,
            COUNT(*) AS n
        FROM team_vs_opponent_season_to_date;
    """,

    "team_vs_opponent_trailing10_full": """
        SELECT * FROM team_vs_opponent_trailing10;
    """,

}


def run():
    conn = get_connection()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print(f"Exporting {len(QUERIES)} query results to {OUTPUT_DIR}")
    print("=" * 70)

    failures = []
    for name, query in QUERIES.items():
        try:
            df = pd.read_sql(query, conn)
            out_path = OUTPUT_DIR / f"{name}.csv"
            df.to_csv(out_path, index=False)
            print(f"  OK    {name}.csv  ({len(df)} rows)")
        except Exception as e:
            failures.append((name, str(e)))
            print(f"  FAIL  {name}: {e}")
            conn.rollback()  # clear the failed transaction so the next query can run

    conn.close()

    print()
    print("=" * 70)
    if failures:
        print(f"{len(failures)} of {len(QUERIES)} queries failed -- see FAIL lines above. "
              f"Likely a missing view/table (e.g. team_vs_opponent_trailing10 not yet "
              f"created) rather than a query bug.")
    else:
        print(f"All {len(QUERIES)} queries exported successfully to {OUTPUT_DIR}")
    print("=" * 70)


if __name__ == "__main__":
    run()
