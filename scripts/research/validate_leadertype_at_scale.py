"""
Spot-checks get_completed_games_with_home_away()'s core assumption --
that ScoreboardV3's game-leaders frame always has exactly one 'home' and
one 'away' leaderType row per completed game -- across a spread of real
dates, not just the single 2025-03-07 batch it was originally confirmed
against. Picks dates likely to stress different scheduling shapes: a
season opener, a light holiday slate, ordinary mid-season nights, and the
final week of a regular season (where rest/tanking can produce unusual
box scores).

Two independent checks per date, same cross-validation approach used in
the original discovery (8/10/26):
  1. Every Final-status game has exactly one home + one away leader row
     (the core assumption load_daily_game_logs.py depends on).
  2. The derived home/away team_ids agree with gameCode's own
     away-then-home tricode convention ("20250307/CLECHA" -> away=CLE,
     home=CHA) -- an independent source within the same frame set, not
     just re-deriving the same value twice.

Run: python scripts/validate_leadertype_at_scale.py [YYYY-MM-DD ...]
     (defaults to a built-in spread of dates if none given)
"""

import sys

from nba_api.stats.endpoints import scoreboardv3
from nba_api.stats.static import teams as nba_teams

DEFAULT_DATES = [
    "2021-10-19",  # 2021-22 season opener -- unusual slate size, first data of the backfill
    "2022-12-25",  # Christmas Day -- known light, high-profile slate (~5 games)
    "2023-02-14",  # ordinary mid-season night
    "2024-01-01",  # New Year's Day
    "2025-03-07",  # already validated -- kept as a regression anchor
    "2025-04-11",  # final week of 2024-25 regular season -- rest/tanking edge cases
]


def build_tricode_lookup() -> dict:
    return {t["abbreviation"]: t["id"] for t in nba_teams.get_teams()}


def check_date(game_date: str, tricode_lookup: dict) -> dict:
    sb = scoreboardv3.ScoreboardV3(game_date=game_date)
    dfs = sb.get_data_frames()
    game_header, leaders = dfs[1], dfs[3]

    final_games = game_header[game_header["gameStatusText"].str.startswith("Final")]

    results = {"date": game_date, "total_games": len(final_games), "missing_leader_row": [],
               "gamecode_mismatch": [], "ok": 0}

    for _, game_row in final_games.iterrows():
        game_id = game_row["gameId"]
        game_code = game_row["gameCode"]

        rows = leaders[leaders["gameId"] == game_id]
        home_rows = rows[rows["leaderType"] == "home"]
        away_rows = rows[rows["leaderType"] == "away"]

        if len(home_rows) != 1 or len(away_rows) != 1:
            results["missing_leader_row"].append(
                f"{game_id}: {len(home_rows)} home row(s), {len(away_rows)} away row(s)"
            )
            continue

        derived_home_id = home_rows.iloc[0]["teamId"]
        derived_away_id = away_rows.iloc[0]["teamId"]

        # gameCode format: "YYYYMMDD/AWAYHOME" -- 3-letter tricodes concatenated
        code_part = game_code.split("/")[-1]
        away_tricode, home_tricode = code_part[:3], code_part[3:]
        code_away_id = tricode_lookup.get(away_tricode)
        code_home_id = tricode_lookup.get(home_tricode)

        if code_away_id != derived_away_id or code_home_id != derived_home_id:
            results["gamecode_mismatch"].append(
                f"{game_id}: leaderType says home={derived_home_id}/away={derived_away_id}, "
                f"gameCode ({game_code}) implies home={code_home_id}/away={code_away_id}"
            )
            continue

        results["ok"] += 1

    return results


def main():
    dates = sys.argv[1:] if len(sys.argv) > 1 else DEFAULT_DATES
    tricode_lookup = build_tricode_lookup()

    all_results = []
    for d in dates:
        print(f"Checking {d}...")
        try:
            r = check_date(d, tricode_lookup)
            all_results.append(r)
            print(f"  {r['total_games']} completed game(s), {r['ok']} clean, "
                  f"{len(r['missing_leader_row'])} missing-row issue(s), "
                  f"{len(r['gamecode_mismatch'])} gameCode mismatch(es)")
        except Exception as e:
            print(f"  FAILED to check {d}: {e}")
            all_results.append({"date": d, "total_games": 0, "missing_leader_row": ["FETCH FAILED"],
                                 "gamecode_mismatch": [], "ok": 0})

    print("\n" + "=" * 60)
    total_games = sum(r["total_games"] for r in all_results)
    total_ok = sum(r["ok"] for r in all_results)
    total_missing = sum(len(r["missing_leader_row"]) for r in all_results)
    total_mismatch = sum(len(r["gamecode_mismatch"]) for r in all_results)

    print(f"TOTAL: {total_games} games across {len(dates)} dates -- "
          f"{total_ok} clean, {total_missing} missing-row issues, {total_mismatch} gameCode mismatches")

    if total_missing or total_mismatch:
        print("\nDetails:")
        for r in all_results:
            for issue in r["missing_leader_row"]:
                print(f"  [{r['date']}] MISSING ROW: {issue}")
            for issue in r["gamecode_mismatch"]:
                print(f"  [{r['date']}] MISMATCH: {issue}")
        print("\nAny issues above mean get_scoreboard_games.py's core assumption doesn't hold")
        print("universally -- worth adding a fallback (e.g. parsing gameCode directly) before")
        print("trusting the daily pipeline fully unattended.")
    else:
        print("\nAll clean across every date checked. The leaderType-based derivation in")
        print("get_scoreboard_games.py held up consistently across season openers, holiday")
        print("slates, ordinary nights, and end-of-season rest games.")


if __name__ == "__main__":
    main()
