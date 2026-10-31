"""Infer which player every team locked into which slot each week and store it in nba_lock_decisions.

Sleeper does not flag a lock, and its matchup points DRIFT (an unlocked slot follows the player's latest game, can lag,
and even a finished week was seen changing). Two steps, both automatic:
  1. sleeper_frozen (any day of the week): the slot's points equal exactly one game of the slot's player, held 2+ days AFTER
     a later game, are his best game so far, and he never leaves the slot (locks are permanent). Recorded once, then skipped.
  2. checksum (once the week is over): Sleeper's season total (fpts) is final and does not drift. A team's week total is
     the sum of its nine locked games, so the combination of each slot's games that adds up to the cent is the real one.
     Written over step 1 / any guess (never over a row you entered by hand); the match is stored in
     sleeper_roster_week_totals (status unique / multi / ambiguous / offset / no_match). If no combination matches, the
     rows are written as 'unverified' and printed loudly, never silently.
Hand-entered rows (record_lock.py) are fixed constraints. The fpts total is read after the week closes; if Sleeper has not
posted it yet the week simply waits for the next daily run.

Usage (from ~/projects/two_words):  python scripts/infer_lock_decisions.py [--season-id 22026] [--week N] [--dry-run] [--recheck] [--refinalize]
"""
import argparse
import itertools
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

SLOT_ORDER = ["PG", "SG", "G", "SF", "PF", "F", "C", "UTIL1", "UTIL2"]
TOL = 0.01
BUFFER_DAYS = 2
REFRESH_TOTALS_DAYS = 1   # Sleeper's final total is re-read only until the day after the week ends, then kept as recorded
REFINALIZE_DAYS = 7   # a closed week is re-derived for this many days: the NBA corrects box scores and Sleeper's totals move


def match_game(points, games):
    """The game whose fantasy score equals the slot's points (None if none or ambiguous)."""
    hits = [g for g in games if abs(float(g["score"]) - float(points)) <= TOL]
    return hits[0] if len(hits) == 1 else None


def infer_slot(states, games_by_player, checked_through):
    """states: time-ordered [(ts, sleeper_pid, nba_pid, points)] for ONE slot. Merges consecutive equal states into runs
    and returns (run, game) for the FIRST run that is confirmed frozen, else None. A run is frozen when its points equal
    exactly one of the player's games (played on/before the run began) and the run lasted until at least BUFFER_DAYS
    after a LATER game of that player."""
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
        if any(x["pid"] != r["pid"] for x in runs[i + 1:]):
            continue   # a locked player stays in his slot, so someone later swapped out of it was never locked (Queen)
        games = games_by_player.get(r["nba"], [])
        g = match_game(r["pts"], [x for x in games if x["date"] <= r["ts"].astimezone().date()])
        if g is None:
            continue
        cutoff = r["until"].astimezone().date() - timedelta(days=BUFFER_DAYS)
        played = [x for x in games if x["date"] <= cutoff]
        later = [x for x in played if x["date"] > g["date"]]
        # lock = held past a later game AND it is his best game so far; a lower early score that "holds" is API lag
        # (George 31.10 / LeBron 40.15 in week 1), not a lock -- a manager keeps the better score
        if later and float(g["score"]) >= max(float(x["score"]) for x in played):
            return r, g
    return None


TOTALS_DDL = """CREATE TABLE IF NOT EXISTS sleeper_roster_week_totals (
    league_id text NOT NULL, season_id text NOT NULL, week_number integer NOT NULL, roster_id integer NOT NULL,
    final_points numeric NOT NULL, status text, note text, explained_diff numeric NOT NULL DEFAULT 0,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (league_id, season_id, week_number, roster_id))"""
OFFSETS = (0, 200, 400, 600)   # our box scores may miss a technical foul (-2.00 each); tried only after an exact match fails


def solve_week(cands, target):
    """cands: one list of candidate scores (integer cents) per slot. Every choice of one candidate per slot whose total
    equals target (cents), as tuples of indexes. Meet in the middle: fast for 9 slots x a few games."""
    n = len(cands)
    mid = n // 2
    right = defaultdict(list)
    for combo in itertools.product(*[range(len(c)) for c in cands[mid:]]):
        right[sum(cands[mid + k][j] for k, j in enumerate(combo))].append(combo)
    out = []
    for combo in itertools.product(*[range(len(c)) for c in cands[:mid]]):
        part = sum(cands[k][j] for k, j in enumerate(combo))
        for rc in right.get(target - part, []):
            out.append(combo + rc)
            if len(out) >= 500:
                return out
    return out


def choose_solution(cands, target, priors, offsets=OFFSETS):
    """-> (choice tuple or None, status, offset_cents). Exact total first; then the technical-foul offsets, each only
    accepted when it gives ONE closest-to-priors answer. Among several exact answers, the one closest to the priors wins."""
    for off in offsets:
        sols = solve_week(cands, target + off)
        if not sols:
            continue
        gap = lambda sol: sum(a != b for a, b in zip(sol, priors))
        best = min(gap(x) for x in sols)
        top = [x for x in sols if gap(x) == best]
        if off:
            if len(sols) == 1:
                return sols[0], "offset", off
            continue
        if len(sols) == 1:
            return sols[0], "unique", 0
        return top[0], ("multi" if len(top) == 1 else "ambiguous"), 0
    return None, "no_match", 0


def fetch_fpts(league):
    import json
    from urllib.request import urlopen
    with urlopen(f"https://api.sleeper.app/v1/league/{league}/rosters", timeout=30) as resp:
        data = json.load(resp)
    return {r["roster_id"]: float(r["settings"].get("fpts") or 0) + float(r["settings"].get("fpts_decimal") or 0) / 100
            for r in data}


def week_totals(cur, league, season_id, week, closed_weeks, dry, refresh=False):
    """{roster_id: (final_points, status)} for a CLOSED week, or None if not available yet. A week's total is Sleeper's
    season total minus the stored totals of the weeks before it; stored once so later weeks can use it."""
    cur.execute("SELECT to_regclass('sleeper_roster_week_totals')")
    have = cur.fetchone()[0] is not None
    if not have and not dry:
        cur.execute(TOTALS_DDL)
        have = True
    if have and not dry:
        cur.execute("ALTER TABLE sleeper_roster_week_totals ADD COLUMN IF NOT EXISTS explained_diff numeric NOT NULL DEFAULT 0")
    stored = {}
    prior = defaultdict(float)
    prior_weeks = set()
    if have:
        cur.execute("SELECT week_number, roster_id, final_points, status FROM sleeper_roster_week_totals WHERE league_id = %s AND season_id = %s",
                    (league, season_id))
        for w, r, pts, st in cur.fetchall():
            if w == week:
                stored[r] = (float(pts), st)
            elif w < week:
                prior[r] += float(pts)
                prior_weeks.add(w)
    if stored and not refresh:
        return stored
    if any(w not in prior_weeks for w in closed_weeks if w < week):
        print(f"  week {week}: earlier week totals are missing, cannot derive this week's total yet; skipped")
        return None
    try:
        fpts = fetch_fpts(league)
    except Exception as e:
        print(f"  week {week}: could not read Sleeper season totals ({e}); will retry next run")
        return stored or None
    derived = {r: round(v - prior[r], 2) for r, v in fpts.items()}
    if not derived or any(v <= 0 for v in derived.values()):
        print(f"  week {week}: Sleeper has not posted the final totals yet; will retry next run")
        return None
    if not dry:
        for r, v in derived.items():
            cur.execute("""INSERT INTO sleeper_roster_week_totals (league_id, season_id, week_number, roster_id, final_points)
                           VALUES (%s,%s,%s,%s,%s)
                           ON CONFLICT (league_id, season_id, week_number, roster_id)
                           DO UPDATE SET final_points = EXCLUDED.final_points, recorded_at = now()""", (league, season_id, week, r, v))
    return {r: (v, None) for r, v in derived.items()}


def record(cur, league, roster, season_id, week, slot, pid, nba, name, g, source, overwrite=False):
    """Insert one lock row with the model's view of that game; never overwrites an existing slot."""
    cur.execute("""SELECT g.fantasy_score, s.lock_bar, s.lock_signal, s.percentage_to_lock, s.tier, s.player_stats_source,
                          e.effective_games_remaining_in_week
                   FROM game_fantasy_scores_weekly_effective g
                   JOIN game_fantasy_scores_weekly_effective e ON e.game_id = g.game_id AND e.player_id = g.player_id
                   LEFT JOIN game_lock_signal s ON s.game_id = g.game_id AND s.player_id = g.player_id
                   WHERE g.game_id = %s AND g.player_id = %s""", (g["id"], nba))
    m = cur.fetchone() or (g["score"], None, None, None, None, None, None)
    cur.execute("""
        INSERT INTO nba_lock_decisions (league_id, roster_id, season_id, week_number, slot, sleeper_player_id,
            nba_player_id, player_name, game_id, game_date, fantasy_score, lock_bar, model_signal,
            model_percentage_to_lock, tier, stats_source, games_left_effective, source)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (league_id, roster_id, week_number, slot) """ + ("""DO UPDATE SET
            sleeper_player_id = EXCLUDED.sleeper_player_id, nba_player_id = EXCLUDED.nba_player_id,
            player_name = EXCLUDED.player_name, game_id = EXCLUDED.game_id, game_date = EXCLUDED.game_date,
            fantasy_score = EXCLUDED.fantasy_score, lock_bar = EXCLUDED.lock_bar, model_signal = EXCLUDED.model_signal,
            model_percentage_to_lock = EXCLUDED.model_percentage_to_lock, tier = EXCLUDED.tier,
            stats_source = EXCLUDED.stats_source, games_left_effective = EXCLUDED.games_left_effective,
            source = EXCLUDED.source WHERE nba_lock_decisions.source <> 'manual'""" if overwrite else "DO NOTHING"),
        (league, roster, season_id, week, slot, pid, nba, name, g["id"], g["date"], m[0], m[1], m[2], m[3],
         m[4], m[5], m[6], source))


OBS_TOL = 0.011


def observed_options(states, final_sid, week_end, wk, original, nba):
    """The values Sleeper ITSELF reported for this slot after the week ended, each tied to one of the player's games.
    -> [(cents, game, shift_cents, times_seen)]; shift is (pre-correction minus current) when the value is the score the
    game had before a later NBA correction. A value that matches no game exactly once is dropped."""
    seen = defaultdict(int)
    for ts, sid, _nba, pts in states:
        if sid == final_sid and pts and ts.astimezone().date() > week_end:
            seen[round(float(pts), 2)] += 1
    out = []
    for v, n in seen.items():
        hit = [g for g in wk if abs(float(g["score"]) - v) <= OBS_TOL]
        if len(hit) == 1:
            out.append((int(round(v * 100)), hit[0], 0, n))
            continue
        hit = [g for g in wk if (g["id"], nba) in original and abs(original[(g["id"], nba)] / 100 - v) <= OBS_TOL]
        if len(hit) == 1:
            g = hit[0]
            out.append((int(round(v * 100)), g, original[(g["id"], nba)] - int(round(float(g["score"]) * 100)), n))
    return out


def solve_from_observed(obs, target):
    """obs: per slot, the options from observed_options (a slot with no games gets [(0, None, 0, 1)]).
    -> (choice, status, explained_cents) for the combination of REPORTED values that adds up to the final, else None."""
    sols = solve_week([[o[0] for o in slot] for slot in obs], target)
    if not sols:
        return None
    weight = lambda sol: sum(obs[i][j][3] for i, j in enumerate(sol))   # how often Sleeper showed each value
    best = max(weight(x) for x in sols)
    top = [x for x in sols if weight(x) == best]
    st = "unique" if len(sols) == 1 else ("multi" if len(top) == 1 else "ambiguous")
    return top[0], st, sum(obs[i][j][2] for i, j in enumerate(top[0]))


def with_pre_correction_scores(cands, picks, priors, nba_ids, original):
    """Per slot, add a second candidate for every game the NBA corrected later: the score it had before the correction.
    -> (cands, picks, priors, shifts); shifts[i][k] is (pre-correction minus current) in cents, or None for a current score."""
    out_c, out_p, out_pri, out_shift = [], [], [], []
    for i in range(len(cands)):
        c_i, p_i, s_i, pri_i = [], [], [], priors[i]
        for k, g in enumerate(picks[i]):
            if k == priors[i]:
                pri_i = len(c_i)
            c_i.append(cands[i][k]); p_i.append(g); s_i.append(None)
            old = original.get((g["id"], nba_ids[i]))
            if old is not None and old != cands[i][k]:
                c_i.append(old); p_i.append(g); s_i.append(old - cands[i][k])
        out_c.append(c_i or cands[i]); out_p.append(p_i); out_shift.append(s_i); out_pri.append(pri_i)
    return out_c, out_p, out_pri, out_shift


def finalize_week(cur, args, league, week, games, last_starters, xw, closed_weeks, found, per_slot, week_end):
    """After the week is over: pick, per team, the combination of locked games that adds up to Sleeper's final total."""
    totals = week_totals(cur, league, args.season_id, week, closed_weeks, args.dry_run, refresh=args.refresh_totals)
    if not totals:
        return 0
    cur.execute("""SELECT roster_id, slot, game_id, source FROM nba_lock_decisions
                   WHERE league_id = %s AND week_number = %s""", (league, week))
    rows = defaultdict(dict)
    for roster, slot, gid, src in cur.fetchall():
        rows[roster][slot] = (gid, src)
    original = {}   # (game_id, nba_player_id) -> the score before the NBA's later correction, in cents
    cur.execute("SELECT to_regclass('game_score_corrections')")
    if cur.fetchone()[0]:
        cur.execute("SELECT game_id, player_id, original_score FROM game_score_corrections")
        original = {(g, int(p)): int(round(float(s) * 100)) for g, p, s in cur.fetchall()}
    for (roster, slot), gid in found.items():
        rows[roster].setdefault(slot, (gid, "sleeper_frozen"))
    written = 0
    for roster in sorted(last_starters):
        if roster not in totals:
            continue
        target_pts, status = totals[roster]
        if status and not (args.recheck or args.refinalize):
            continue   # already settled in an earlier run
        starters = last_starters[roster]
        cands, picks, priors, names = [], [], [], []
        for i, slot in enumerate(SLOT_ORDER):
            pid = starters[i]
            nba = (xw.get(pid) or (None, None))[0]
            wk = sorted(games.get(nba) or [], key=lambda x: x["date"])
            have = {} if args.recheck else rows[roster]
            if have.get(slot, (None, None))[1] == "manual":
                wk = [x for x in wk if x["id"] == have[slot][0]] or wk   # your own entry is fixed
            prior = len(wk) - 1
            if slot in have and have[slot][1] != "auto_final":
                prior = next((k for k, x in enumerate(wk) if x["id"] == have[slot][0]), prior)
            cands.append([int(round(float(x["score"]) * 100)) for x in wk] or [0])
            picks.append(wk)
            priors.append(max(prior, 0))
            names.append((pid, nba, (xw.get(pid) or (None, pid))[1]))
        target = int(round(target_pts * 100))
        explained = 0
        note_extra = ""
        method = "totals"
        obs = [observed_options(per_slot.get((roster, slot), []), starters[i], week_end, picks[i], original, names[i][1]) or None
               for i, slot in enumerate(SLOT_ORDER)]
        obs = [o if o else ([(0, None, 0, 1)] if not picks[i] else None) for i, o in enumerate(obs)]
        if all(o is not None for o in obs):
            found_obs = solve_from_observed(obs, target)
            if found_obs:
                sol, st, explained = found_obs
                cands, picks, off, method = [[o[0] for o in slot] for slot in obs], [[o[1] for o in slot if o[1] is not None] for slot in obs], 0, "reported"
                changed = [names[i][2] for i, j in enumerate(sol) if obs[i][j][2]]
                if changed:
                    note_extra = f"; Sleeper credited the pre-correction score for {', '.join(changed)} ({explained / 100:+.2f})"
        if method == "totals":
            sol, st, off = choose_solution(cands, target, priors)
            if st in ("offset", "no_match") and original:
                # Sleeper freezes the week's total, so it may hold the score from BEFORE a later NBA correction. Offer both
                # versions of each corrected game and accept only an exact match.
                ext_c, ext_p, ext_pri, ext_orig = with_pre_correction_scores(cands, picks, priors, [n[1] for n in names], original)
                sol2, st2, _ = choose_solution(ext_c, target, ext_pri, offsets=(0,))
                if sol2 is not None:
                    cands, picks, sol, st, off = ext_c, ext_p, sol2, st2, 0
                    explained = sum(ext_orig[i][j] or 0 for i, j in enumerate(sol2))
                    changed = [names[i][2] for i, j in enumerate(sol2) if ext_orig[i][j]]
                    note_extra = f"; Sleeper credited the pre-correction score for {', '.join(changed)} ({explained / 100:+.2f})"

        label = {"unique": "unique match", "multi": "several exact matches, closest to evidence chosen",
                 "ambiguous": "AMBIGUOUS: several equal matches", "offset": f"matched after a {off/100:.2f} scoring offset",
                 "no_match": "NO combination adds up"}[st]
        label += note_extra
        if method == "reported":
            label = {"unique": "unique match on the values Sleeper itself reported", "multi": "several matches on Sleeper's reported values, most often shown chosen",
                     "ambiguous": "AMBIGUOUS: several equal matches on Sleeper's reported values"}[st] + note_extra
        print(f"  week {week} roster {roster:>2}: final {target_pts:.2f} -> {label}")
        if sol is None:
            sol, source = priors, "unverified"
        else:
            source = "checksum" if st in ("unique", "offset") else "checksum_" + st
        for i, slot in enumerate(SLOT_ORDER):
            wk = picks[i]
            if not wk:
                continue
            g = wk[sol[i]]
            pid, nba, name = names[i]
            print(f"      {slot:<5} {name}  {float(g['score']):.2f} on {g['date']}  [{source}]")
            written += 1
            if not args.dry_run:
                record(cur, league, roster, args.season_id, week, slot, pid, nba, name, g, source, overwrite=True)
        if not args.dry_run:
            cur.execute("""UPDATE sleeper_roster_week_totals SET status = %s, note = %s, explained_diff = %s
                           WHERE league_id = %s AND season_id = %s AND week_number = %s AND roster_id = %s""",
                        (st, label, explained / 100, league, args.season_id, week, roster))
    return written


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season-id", default="22026")
    ap.add_argument("--week", type=int)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--recheck", action="store_true", help="dry-run only: ignore rows already recorded, to test the inference against known locks")
    ap.add_argument("--refinalize", action="store_true", help="re-derive closed weeks even if settled (automatic for weeks that ended within the last 7 days)")
    args = ap.parse_args()
    force_refinalize = args.refinalize

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
    closed_weeks = [w for w, e in weeks.items() if date.today() > e]
    if args.week:
        weeks = {w: e for w, e in weeks.items() if w == args.week}
    cur.execute("SELECT DISTINCT sleeper_player_id, nba_player_id, player_name FROM roster_ownership WHERE league_id = %s", (league,))
    xw = {s: (n, nm) for s, n, nm in cur.fetchall()}
    if args.recheck and not args.dry_run:
        raise SystemExit("--recheck only works with --dry-run (it would overwrite nothing, but it would print locks already recorded)")
    now = datetime.now(timezone.utc)
    total = 0
    for week, week_end in weeks.items():
        cur.execute("""SELECT roster_id, slot FROM nba_lock_decisions WHERE league_id = %s AND week_number = %s""", (league, week))
        done = {(r, sl) for r, sl in cur.fetchall()}
        cur.execute("""SELECT player_id, game_id, game_date, fantasy_score FROM game_fantasy_scores_weekly_effective
                       WHERE season_id = %s AND week_number = %s""", (args.season_id, week))
        games = defaultdict(list)
        for pid, gid, gd, sc in cur.fetchall():
            games[pid].append({"id": gid, "date": gd, "score": sc})
        cur.execute("""SELECT roster_id, synced_at, starters, starters_points FROM sleeper_matchup_points_snapshots
                       WHERE league_id = %s AND week = %s AND starters IS NOT NULL ORDER BY roster_id, synced_at""", (league, week))
        per_slot = defaultdict(list)
        last_starters = {}
        for roster, ts, starters, pts in cur.fetchall():
            if len(starters) != len(SLOT_ORDER) or len(pts or []) != len(SLOT_ORDER):
                print(f"  week {week} roster {roster}: {len(starters)} starters, expected {len(SLOT_ORDER)}; skipped")
                continue
            prev = last_starters.get(roster)
            starters = [(prev[i] if prev and str(sid) in ("0", "", "None") else sid) for i, sid in enumerate(starters)]
            last_starters[roster] = starters   # an emptied slot keeps the last player who held it that week
            for i, slot in enumerate(SLOT_ORDER):
                sid = starters[i]
                per_slot[(roster, slot)].append((ts, sid, (xw.get(sid) or (None, None))[0], pts[i]))
        week_over = now.date() > week_end
        found = {}
        for (roster, slot), states in per_slot.items():
            if (roster, slot) in done and not args.recheck:
                continue
            res = infer_slot(states, games, now)
            if not res:
                continue
            r, g = res
            name = (xw.get(r["pid"]) or (None, r["pid"]))[1]
            print(f"  week {week} roster {roster:>2} {slot:<5} {name}  {float(g['score']):.2f} on {g['date']}  [sleeper_frozen]")
            total += 1
            found[(roster, slot)] = g["id"]
            if not args.dry_run:
                record(cur, league, roster, args.season_id, week, slot, r["pid"], r["nba"], name, g, "sleeper_frozen")
        if week_over:
            days_over = (now.date() - week_end).days
            args.refinalize = force_refinalize or days_over <= REFINALIZE_DAYS
            args.refresh_totals = force_refinalize or days_over <= REFRESH_TOTALS_DAYS
            total += finalize_week(cur, args, league, week, games, last_starters, xw, closed_weeks, found, per_slot, week_end)
    if not args.dry_run:
        conn.commit()
    print(f"{total} lock(s) recorded" + (" (dry run, nothing written)" if args.dry_run else ""))
    cur.close(); conn.close()


if __name__ == "__main__":
    main()
