"""Infer which player every team locked into which slot each week, from Sleeper's points snapshots, and store it
in nba_lock_decisions (source 'sleeper_frozen' / 'sleeper_final'; rows you entered by hand with record_lock.py are kept).

Sleeper does not flag a lock, but it gives, per roster, the player in each slot (starters, in slot order) and the slot's
points. We log those on every daily sync (sleeper_matchup_points_snapshots, written by sleeper_daily_sync.py). A slot is
treated as LOCKED on a game when:
  frozen: the slot's player and points matched exactly one of his games, and stayed unchanged although he played a later
          game that week (a live slot would have moved); or
  final : the week is over and that was the slot's last state (Sunday auto-lock / anything left in the lineup).
Limits: needs a later day's snapshot to confirm a mid-week lock (a lock on the player's last game of the week is
confirmed at week end); a lock is dated by the GAME it froze, not by the minute you clicked.

Usage (from ~/projects/two_words):  python scripts/infer_lock_decisions.py [--season-id 22026] [--week N] [--dry-run]
"""
import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta

SLOT_ORDER = ["PG", "SG", "G", "SF", "PF", "F", "C", "UTIL1", "UTIL2"]
TOL = 0.06


def match_game(points, games):
    """The game whose fantasy score equals the slot's points (None if none or ambiguous)."""
    hits = [g for g in games if abs(float(g["score"]) - float(points)) <= TOL]
    return hits[0] if len(hits) == 1 else None


def infer_slot(states, games_by_player, week_end, checked_through):
    """states: time-ordered [(ts, sleeper_pid, nba_pid, points)] for ONE slot (consecutive duplicates already merged
    into runs by this function). Returns (state, game, reason) for the first confirmed lock, else None."""
    runs = []
    for ts, pid, nba, pts in states:
        if runs and runs[-1]["pid"] == pid and runs[-1]["pts"] == pts:
            continue
        runs.append({"ts": ts, "pid": pid, "nba": nba, "pts": pts, "until": None})
    for i, r in enumerate(runs):
        r["until"] = runs[i + 1]["ts"] if i + 1 < len(runs) else checked_through
    for i, r in enumerate(runs):
        if not r["pid"] or r["nba"] is None or not r["pts"]:
            continue
        games = games_by_player.get(r["nba"], [])
        g = match_game(r["pts"], games)
        if g is None:
            continue
        # only the slot's CURRENT state can be confirmed frozen; a state that later changed was live (or swapped out)
        later = [x for x in games if x["date"] > g["date"] and x["date"] <= r["until"].date() - timedelta(days=2)] if i == len(runs) - 1 else []
        if later:
            return r, g, "sleeper_frozen"
        if i == len(runs) - 1 and checked_through.date() > week_end:
            return r, g, "sleeper_final"
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season-id", default="22026")
    ap.add_argument("--week", type=int)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT to_regclass('nba_lock_decisions')")
    if cur.fetchone()[0] is None:
        raise SystemExit("Table nba_lock_decisions is missing: psql -d postgres -f schema/tables/nba_lock_decisions.sql")
    cur.execute("SELECT league_id FROM sleeper_current_league")
    league = cur.fetchone()[0]
    cur.execute("SELECT week_number, week_end_date FROM fantasy_weeks WHERE season_id = %s AND week_start_date <= %s ORDER BY week_number",
                (args.season_id, date.today()))
    weeks = {w: e for w, e in cur.fetchall()}
    if args.week:
        weeks = {w: e for w, e in weeks.items() if w == args.week}
    cur.execute("SELECT DISTINCT sleeper_player_id, nba_player_id, player_name FROM roster_ownership WHERE league_id = %s", (league,))
    xw = {s: (n, nm) for s, n, nm in cur.fetchall()}
    now = datetime.now()
    total = 0
    for week, week_end in weeks.items():
        cur.execute("""SELECT player_id, game_id, game_date, fantasy_score FROM game_fantasy_scores_weekly_effective
                       WHERE season_id = %s AND week_number = %s""", (args.season_id, week))
        games = defaultdict(list)
        for pid, gid, gd, sc in cur.fetchall():
            games[pid].append({"id": gid, "date": gd, "score": sc})
        cur.execute("""SELECT roster_id, synced_at, starters, starters_points FROM sleeper_matchup_points_snapshots
                       WHERE league_id = %s AND week = %s AND starters IS NOT NULL ORDER BY roster_id, synced_at""", (league, week))
        per_slot = defaultdict(list)
        for roster, ts, starters, pts in cur.fetchall():
            if len(starters) != len(SLOT_ORDER) or len(pts or []) != len(SLOT_ORDER):
                print(f"  week {week} roster {roster}: {len(starters)} starters, expected {len(SLOT_ORDER)}; skipped")
                continue
            for i, slot in enumerate(SLOT_ORDER):
                sid = starters[i]
                per_slot[(roster, slot)].append((ts, sid, (xw.get(sid) or (None, None))[0], pts[i]))
        for (roster, slot), states in per_slot.items():
            res = infer_slot(states, games, week_end, now)
            if not res:
                continue
            r, g, reason = res
            cur.execute("""SELECT g.fantasy_score, s.lock_bar, s.lock_signal, s.percentage_to_lock, s.tier, s.player_stats_source,
                                  e.effective_games_remaining_in_week
                           FROM game_fantasy_scores_weekly_effective g
                           JOIN game_fantasy_scores_weekly_effective e ON e.game_id = g.game_id AND e.player_id = g.player_id
                           LEFT JOIN game_lock_signal s ON s.game_id = g.game_id AND s.player_id = g.player_id
                           WHERE g.game_id = %s AND g.player_id = %s""", (g["id"], r["nba"]))
            m = cur.fetchone() or (g["score"], None, None, None, None, None, None)
            name = (xw.get(r["pid"]) or (None, r["pid"]))[1]
            print(f"  week {week} roster {roster:>2} {slot:<5} {name}  {float(g['score']):.1f} on {g['date']}  [{reason}]")
            total += 1
            if args.dry_run:
                continue
            cur.execute("""
                INSERT INTO nba_lock_decisions (league_id, roster_id, season_id, week_number, slot, sleeper_player_id,
                    nba_player_id, player_name, game_id, game_date, fantasy_score, lock_bar, model_signal,
                    model_percentage_to_lock, tier, stats_source, games_left_effective, source)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (league_id, roster_id, week_number, slot) DO UPDATE SET
                    sleeper_player_id = EXCLUDED.sleeper_player_id, nba_player_id = EXCLUDED.nba_player_id,
                    player_name = EXCLUDED.player_name, game_id = EXCLUDED.game_id, game_date = EXCLUDED.game_date,
                    fantasy_score = EXCLUDED.fantasy_score, lock_bar = EXCLUDED.lock_bar, model_signal = EXCLUDED.model_signal,
                    model_percentage_to_lock = EXCLUDED.model_percentage_to_lock, tier = EXCLUDED.tier,
                    stats_source = EXCLUDED.stats_source, games_left_effective = EXCLUDED.games_left_effective,
                    source = EXCLUDED.source
                WHERE nba_lock_decisions.source <> 'manual'""",
                (league, roster, args.season_id, week, slot, r["pid"], r["nba"], name, g["id"], g["date"], m[0], m[1], m[2], m[3],
                 m[4], m[5], m[6], reason))
    if not args.dry_run:
        conn.commit()
    print(f"{total} lock(s) inferred" + (" (dry run, nothing written)" if args.dry_run else ""))
    cur.close(); conn.close()


if __name__ == "__main__":
    main()
