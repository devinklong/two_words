"""
scripts/analysis/correlation_matrix_and_clustering.py

Two multivariate diagnostics: a full pace/off_rating/def_rating
correlation matrix per granularity, and KMeans "team style" clustering
on season-to-date team-season profiles. net_rating is excluded from
both (it's off_rating minus def_rating by definition).

Requires: pandas, scikit-learn (pip install pandas scikit-learn --break-system-packages)
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # project root (scripts/analysis/ is two levels down)
from db_connection import get_connection

import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

FEATURES = ["pace", "off_rating", "def_rating"]
N_CLUSTERS = 4  # starting guess -- see inertia printout below before trusting this number


def load_all_granularities(conn):
    query = """
        SELECT granularity, game_id, team_id, season_id, game_date,
               pace, off_rating, def_rating
        FROM team_stats_all_granularities
        WHERE pace IS NOT NULL AND off_rating IS NOT NULL AND def_rating IS NOT NULL;
    """
    return pd.read_sql(query, conn)


def load_team_season_profiles(conn):
    """Most recent season_to_date snapshot per team per season, for clustering."""
    query = """
        SELECT DISTINCT ON (team_id, season_id)
            team_id, season_id, pace, off_rating, def_rating
        FROM team_rolling_season_to_date_stats
        WHERE games_included > 0
        ORDER BY team_id, season_id, game_date DESC;
    """
    return pd.read_sql(query, conn)


def print_correlation_matrices(df):
    print("=" * 70)
    print("CORRELATION MATRICES (Pearson) by granularity")
    print("=" * 70)
    for gran, sub in df.groupby("granularity"):
        print(f"\n  {gran}  (n={len(sub)})")
        corr = sub[FEATURES].corr().round(3)
        print(corr.to_string())


def run_clustering(df):
    print()
    print("=" * 70)
    print("TEAM STYLE CLUSTERING (season-to-date team-season profiles)")
    print("=" * 70)
    print(f"  n team-seasons: {len(df)}")

    X = df[FEATURES].values
    X_scaled = StandardScaler().fit_transform(X)

    # Inertia by k lets you eyeball the elbow before trusting N_CLUSTERS.
    print("\n  Inertia by k (look for the elbow before trusting N_CLUSTERS):")
    for k in range(2, 7):
        km = KMeans(n_clusters=k, n_init=10, random_state=42).fit(X_scaled)
        print(f"    k={k}: inertia={km.inertia_:.1f}")

    km = KMeans(n_clusters=N_CLUSTERS, n_init=10, random_state=42).fit(X_scaled)
    df = df.copy()
    df["cluster"] = km.labels_

    print(f"\n  Cluster profiles (k={N_CLUSTERS}, unscaled feature means):")
    profile = df.groupby("cluster")[FEATURES].mean().round(2)
    profile["n_team_seasons"] = df.groupby("cluster").size()
    print(profile.to_string())

    print("\n  Sample team-seasons per cluster (first 5 shown):")
    for c in sorted(df["cluster"].unique()):
        sample = df[df["cluster"] == c][["team_id", "season_id"] + FEATURES].head(5)
        print(f"\n    Cluster {c}:")
        print(sample.to_string(index=False))

    return df


def run():
    conn = get_connection()

    all_gran = load_all_granularities(conn)
    print_correlation_matrices(all_gran)

    profiles = load_team_season_profiles(conn)
    run_clustering(profiles)

    conn.close()

    print()
    print("=" * 70)
    print("Done. Cross-check cluster groupings against teams you already know before trusting them.")
    print("=" * 70)


if __name__ == "__main__":
    run()
