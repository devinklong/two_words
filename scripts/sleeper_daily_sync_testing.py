"""
scripts/sleeper_daily_sync_testing.py

Tests get_current_week() against known dates from the already-verified
2024/2025 seasons (0 mismatches confirmed in verify_sleeper_join_integrity.py).
Does NOT test sync_current_week()'s actual API-fetching behavior -- that
needs a live season to validate for real, not simulated dates.
"""

import sys
from pathlib import Path
from datetime import date

sys.path.append(str(Path(__file__).resolve().parent))
from db_connection import get_connection
from sleeper_daily_sync import get_current_week

KNOWN_CASES = [
    # (season_id, date, expected_week)
    ("22025", date(2025, 10, 23), 1),   # Nembhard's verified game
    ("22025", date(2025, 10, 20), 1),   # week 1 start boundary
    ("22025", date(2025, 10, 26), 1),   # week 1 end boundary
    ("22025", date(2025, 10, 27), 2),   # week 2 start boundary
    ("22024", date(2024, 10, 22), 1),   # real season opener
    ("22024", date(2025, 4, 6), 24),    # championship week
]

OUT_OF_RANGE_CASES = [
    ("22025", date(2025, 7, 1)),   # offseason
    ("22024", date(2024, 6, 1)),   # offseason
]


def run():
    conn = get_connection()
    cur = conn.cursor()

    passed, failed = 0, 0

    for season_id, as_of_date, expected_week in KNOWN_CASES:
        actual = get_current_week(cur, season_id, as_of_date)
        status = "PASS" if actual == expected_week else "FAIL"
        if status == "PASS":
            passed += 1
        else:
            failed += 1
        print(f"  [{status}] season={season_id} date={as_of_date} expected={expected_week} actual={actual}")

    for season_id, as_of_date in OUT_OF_RANGE_CASES:
        actual = get_current_week(cur, season_id, as_of_date)
        status = "PASS" if actual is None else "FAIL"
        if status == "PASS":
            passed += 1
        else:
            failed += 1
        print(f"  [{status}] season={season_id} date={as_of_date} expected=None (offseason) actual={actual}")

    cur.close()
    conn.close()

    print(f"\n{passed} passed, {failed} failed.")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    run()
