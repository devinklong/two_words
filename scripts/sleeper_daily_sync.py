"""
scripts/sleeper_daily_sync.py

Core sync logic for keeping Sleeper data current during a live season --
NOT a full backfill. Determines which week a given date falls in (reusing
the already-validated week_start_date/week_end_date boundaries from
game_fantasy_scores_weekly_effective) and syncs only that week's matchups
and recent transactions, plus a full roster refresh (rosters are small
and change any day, so always refreshing them in full is cheap and safe).

No runner/scheduler decided yet -- this is importable logic, callable
manually, from a script, or wired to cron later without changing how it
works. Can't be end-to-end validated against real daily activity until
a season is actually live; get_current_week() IS testable now against
historical 2024/2025 dates, since those boundaries are already proven.
"""

import sys
from pathlib import Path
from datetime import date

sys.path.append(str(Path(__file__).resolve().parent))
from db_connection import get_connection
from backfill_sleeper_league import upsert_rosters, upsert_matchups, upsert_transactions


def get_current_week(cur, season_id, as_of_date=None):
    """Returns the week_number whose [week_start_date, week_end_date] range
    contains as_of_date (defaults to today). Returns None if as_of_date falls
    outside every known week for that season (offseason, or season not yet
    backfilled) -- callers must handle that, not assume a week always exists."""
    if as_of_date is None:
        as_of_date = date.today()

    cur.execute("""
        SELECT week_number FROM game_fantasy_scores_weekly_effective
        WHERE season_id = %s AND %s BETWEEN week_start_date AND week_end_date
        LIMIT 1;
    """, (season_id, as_of_date))
    row = cur.fetchone()
    return row[0] if row else None


def sync_current_week(cur, league_id, season_id, as_of_date=None):
    """Syncs rosters in full, plus matchups/transactions for the current week
    and the prior week (to catch late-settling waivers/corrections)."""
    week = get_current_week(cur, season_id, as_of_date)
    if week is None:
        print(f"  No active week for season_id={season_id} on {as_of_date or date.today()} "
              f"-- offseason, or week boundaries not yet backfilled for this date. Skipping.")
        return

    weeks_to_sync = [w for w in (week - 1, week) if w >= 1]
    print(f"  Current week: {week}. Syncing weeks {weeks_to_sync}.")

    n_rosters = upsert_rosters(cur, league_id)
    print(f"  {n_rosters} rosters synced (full refresh)")

    n_matchups = upsert_matchups(cur, league_id, weeks=weeks_to_sync)
    print(f"  {n_matchups} matchup rows synced")

    n_transactions = upsert_transactions(cur, league_id, rounds=weeks_to_sync)
    print(f"  {n_transactions} transactions synced")


def run(league_id, season_id, as_of_date=None):
    conn = get_connection()
    cur = conn.cursor()

    sync_current_week(cur, league_id, season_id, as_of_date)
    conn.commit()

    cur.close()
    conn.close()


if __name__ == "__main__":
    # Placeholder call -- swap league_id/season_id for whichever league is
    # actually live once a season starts. No CURRENT_LEAGUE_ID constant here
    # on purpose: which league is "current" needs a real decision (query
    # sleeper_leagues for status='in_season'?) once this gets wired up for
    # real use, not a hardcoded guess.
    print("This module's functions are meant to be imported and called with an explicit "
          "league_id/season_id -- see get_current_week() and sync_current_week().")
