"""
Annotates team_schedule_gaps with a reason from nbainjuries' daily report,
fetching each date's report ONCE and reusing it for every gap on that date
(not one call per gap). Matches on first+last name together, since last
name alone risks collisions across 530+ active players; still-ambiguous
matches are left unexplained and logged for manual review, not guessed at.
Prereqs: team_schedule_gaps view + gap_reasons table exist.

DATE-SCOPED (8/10/26): now accepts an optional date_from, so the daily
pipeline (load_daily_game_logs.py) can chain this in scoped to just the
date it loaded, instead of re-processing the full historical backfill
(and re-fetching thousands of already-resolved injury reports) every
single night. Full-backfill behavior (no date filter) still works
unchanged when run standalone with no argument.

Run (full backfill, unchanged): python scripts/build_gap_reasons.py
Run (date-scoped):              python scripts/build_gap_reasons.py YYYY-MM-DD
"""

import os
import sys
import time
from datetime import datetime

import pandas as pd
from psycopg2.extras import execute_values

from nbainjuries import injury

from db_connection import get_connection

SLEEP_SECONDS_BETWEEN_REPORT_CALLS = 0.5


def fetch_gaps(conn, date_from=None) -> pd.DataFrame:
    """date_from (optional, 'YYYY-MM-DD'): restricts to gaps on or after
    this date -- used for the daily-scoped chained call. None = full
    backfill (original, unscoped behavior)."""
    query = """
        SELECT g.player_id, p.first_name, p.last_name, g.team_id, g.game_id, g.game_date
        FROM team_schedule_gaps g
        JOIN players p ON p.player_id = g.player_id
    """
    params = None
    if date_from:
        query += " WHERE g.game_date >= %s"
        params = (date_from,)
    query += " ORDER BY g.game_date"
    return pd.read_sql(query, conn, params=params)


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
                # still ambiguous even with first+last -- don't guess, log for manual review
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


def build_gap_reasons(conn, date_from=None) -> int:
    """
    Callable entry point -- used both by main() below and by
    load_daily_game_logs.py's chained call. Returns the number of gap
    reason rows inserted. Reuses the caller's connection rather than
    opening/closing its own, so a chained call doesn't need a second
    round trip to the DB.
    """
    gaps_df = fetch_gaps(conn, date_from=date_from)
    if gaps_df.empty:
        print("No gaps found -- nothing to annotate.")
        return 0

    annotated, ambiguous = annotate_gaps(gaps_df)

    explained_count = annotated["is_explained"].sum()
    print(f"\n{explained_count}/{len(annotated)} gaps matched an injury report.")
    print(f"{len(annotated) - explained_count} likely coach's decisions (or unmatched).")

    if not ambiguous.empty:
        log_path = "cleaning_logs/ambiguous_gap_matches.csv"
        os.makedirs("cleaning_logs", exist_ok=True)
        # Append, not overwrite -- a daily-scoped run's ambiguous matches
        # shouldn't erase ones logged by a previous run (full backfill or
        # an earlier day). header only written if the file is new.
        write_header = not os.path.exists(log_path)
        ambiguous.to_csv(log_path, mode="a", header=write_header, index=False)
        print(f"\n{len(ambiguous)} ambiguous match(es) — appended to {log_path} for manual review.")
    else:
        print("\nNo ambiguous matches this run.")

    n = load_gap_reasons(annotated, conn)
    print(f"\nDone. Inserted {n} gap reason rows.")
    return n


def main():
    date_from = sys.argv[1] if len(sys.argv) > 1 else None
    conn = get_connection()
    build_gap_reasons(conn, date_from=date_from)
    conn.close()


if __name__ == "__main__":
    main()
