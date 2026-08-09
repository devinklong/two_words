"""
Annotates every row in team_schedule_gaps with a reason, pulled from
nbainjuries' daily injury report — matches the manual validation approach
from 03_explore_team_schedule.ipynb, but fetches each date's report ONCE
and reuses it for every player-gap on that date, instead of one live call
per gap (which would mean 10,000+ redundant requests for the same handful
of dates).

Matching uses BOTH first and last name against the report's 'Player Name'
column, not last name alone — last-name-only risks false matches/misses
whenever two players share a surname on the same day's report (common
enough across 530+ active players to matter at this scale). Ambiguous
matches (still >1 row after both checks) are left unexplained and
printed for manual review rather than guessed at.

Prereqs:
  - team_schedule_gaps view exists (schema/team_schedule_gaps_view.sql)
  - gap_reasons table exists (schema/gap_reasons.sql)

Run from the project root:
    python scripts/build_gap_reasons.py
"""

import os
import time
from datetime import datetime

import pandas as pd
from psycopg2.extras import execute_values

from nbainjuries import injury

from db_connection import get_connection

SLEEP_SECONDS_BETWEEN_REPORT_CALLS = 0.5


def fetch_gaps(conn) -> pd.DataFrame:
    """Pull every gap, joined to players for last_name (used for report matching)."""
    query = """
        SELECT g.player_id, p.first_name, p.last_name, g.team_id, g.game_id, g.game_date
        FROM team_schedule_gaps g
        JOIN players p ON p.player_id = g.player_id
        ORDER BY g.game_date
    """
    return pd.read_sql(query, conn)


def get_report_for_date(game_date, report_cache: dict):
    """Fetch (or reuse cached) injury report for a given date at 5:30 PM."""
    date_key = game_date.strftime("%Y-%m-%d") if hasattr(game_date, "strftime") else str(game_date)

    if date_key in report_cache:
        return report_cache[date_key]

    date_obj = datetime.strptime(date_key, "%Y-%m-%d")
    report_time = datetime(date_obj.year, date_obj.month, date_obj.day, 17, 30)

    try:
        report = injury.get_reportdata(report_time, return_df=True)
    except Exception as e:
        print(f"  Report unavailable for {date_key}: {e}")
        report = None

    report_cache[date_key] = report
    time.sleep(SLEEP_SECONDS_BETWEEN_REPORT_CALLS)
    return report


def annotate_gaps(gaps_df: pd.DataFrame):
    report_cache = {}
    results = []
    ambiguous = []

    unique_dates = gaps_df["game_date"].nunique()
    print(f"Annotating {len(gaps_df)} gaps across {unique_dates} unique dates...")

    for i, row in enumerate(gaps_df.itertuples(index=False), start=1):
        report = get_report_for_date(row.game_date, report_cache)

        status, reason, is_explained = None, None, False

        if report is not None and not report.empty:
            match = report[
                report["Player Name"].str.contains(row.last_name, case=False, na=False)
                & report["Player Name"].str.contains(row.first_name, case=False, na=False)
            ]
            if len(match) > 1:
                # Still ambiguous even with first+last — don't guess, leave unexplained
                # and log it for manual review rather than silently picking a row or
                # relying on terminal scrollback (which gets evicted on long runs).
                ambiguous.append({
                    "player_id": row.player_id,
                    "first_name": row.first_name,
                    "last_name": row.last_name,
                    "game_date": row.game_date,
                    "num_matches": len(match),
                })
            elif not match.empty:
                status = match["Current Status"].values[0]
                reason = match["Reason"].values[0]
                is_explained = True

        results.append({
            "player_id": row.player_id,
            "team_id": row.team_id,
            "game_id": row.game_id,
            "game_date": row.game_date,
            "status": status,
            "reason": reason,
            "is_explained": is_explained,
        })

        if i % 500 == 0:
            print(f"  ...{i}/{len(gaps_df)} processed")

    return pd.DataFrame(results), pd.DataFrame(ambiguous)


def load_gap_reasons(df: pd.DataFrame, conn) -> int:
    if df.empty:
        return 0

    cur = conn.cursor()
    rows = list(df.itertuples(index=False, name=None))

    execute_values(
        cur,
        """
        INSERT INTO gap_reasons
            (player_id, team_id, game_id, game_date, status, reason, is_explained)
        VALUES %s
        ON CONFLICT (player_id, game_id) DO NOTHING
        """,
        rows,
    )
    conn.commit()
    cur.close()
    return len(rows)


def main():
    conn = get_connection()

    gaps_df = fetch_gaps(conn)
    if gaps_df.empty:
        print("No gaps found in team_schedule_gaps — nothing to annotate.")
        return

    annotated, ambiguous = annotate_gaps(gaps_df)

    explained_count = annotated["is_explained"].sum()
    print(f"\n{explained_count}/{len(annotated)} gaps matched an injury report.")
    print(f"{len(annotated) - explained_count} likely coach's decisions (or unmatched).")

    if not ambiguous.empty:
        log_path = "cleaning_logs/ambiguous_gap_matches.csv"
        os.makedirs("cleaning_logs", exist_ok=True)
        ambiguous.to_csv(log_path, index=False)
        print(f"\n{len(ambiguous)} ambiguous match(es) — logged to {log_path} for manual review.")
    else:
        print("\nNo ambiguous matches this run.")

    n = load_gap_reasons(annotated, conn)
    conn.close()

    print(f"\nDone. Inserted {n} gap reason rows.")


if __name__ == "__main__":
    main()
