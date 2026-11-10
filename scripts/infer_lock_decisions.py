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
                stored[r] = (float(pts), st)   # status 'ruling' = a commissioner change; never re-derived (see record_ruling.py)
            elif w < week:
                prior[r] += float(pts)
                prior_weeks.add(w)
    if any(w > week for w in closed_weeks):
        # A week's total is Sleeper's season total minus the weeks before it. Once a LATER week has closed the season total includes
        # that week too, so only the stored value is valid: never re-derive or refresh an older week (a forced refinalize of week 1
        # after week 2 closed turned every team's 326-433 into week 1 + week 2).
        if not stored:
            print(f"  week {week}: a later week has closed and this week's total was never stored; cannot derive it now; skipped")
            return None
        return stored
    if stored and not refresh:
        return stored
    ruled = {r: v for r, v in stored.items() if v[1] == "ruling"}
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
            if r in ruled:
                continue
            cur.execute("""INSERT INTO sleeper_roster_week_totals (league_id, season_id, week_number, roster_id, final_points)
                           VALUES (%s,%s,%s,%s,%s)
                           ON CONFLICT (league_id, season_id, week_number, roster_id)
                           DO UPDATE SET final_points = EXCLUDED.final_points, recorded_at = now()""", (league, season_id, week, r, v))
    return {r: (ruled[r] if r in ruled else (v, None)) for r, v in derived.items()}


def record(cur, league, roster, season_id, week, slot, pid, nba, name, g, source, overwrite=False, keep=("manual",)):
    """Insert one lock row with the model's view of that game; never overwrites an existing slot.
    Returns False without touching anything when the row is already there and nothing would change. The model lookup below
    reads game_lock_signal (~0.8 s per call), and this runs for every lock every day, which made the step take 214 s. A row
    freezes the model's read at the time it was first written, so skipping an identical row loses nothing."""
    cur.execute("""SELECT game_id, sleeper_player_id, nba_player_id, source, lock_bar, now() - recorded_at > interval '2 days' FROM nba_lock_decisions
                   WHERE league_id = %s AND roster_id = %s AND week_number = %s AND slot = %s""", (league, roster, week, slot))
    have = cur.fetchone()
    if have:
        if not overwrite or have[3] in keep:
            return False   # the insert below would do nothing
        if have[0] == g["id"] and have[1] == pid and have[2] == nba and have[3] == source and (have[4] is not None or have[5]):
            # Same lock already stored. A missing model read (a player outside the ownable pool has no game_lock_signal row, so
            # 55 locks on 11/8/26 never get one) is retried for 2 days after the lock was first recorded, then left alone
            # instead of costing ~0.8 s per row on every daily run.
            return False
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
            source = EXCLUDED.source WHERE nba_lock_decisions.source <> ALL(%s)""" if overwrite else "DO NOTHING"),
        (league, roster, season_id, week, slot, pid, nba, name, g["id"], g["date"], m[0], m[1], m[2], m[3],
         m[4], m[5], m[6], source) + ((list(keep),) if overwrite else ()))
    return True


FIXED_SOURCES = ("manual", "sleeper_flag")   # rows no inference may change


def flag_locks(cur, args, league, week, games, xw):
    """Locks Sleeper itself reports (sleeper_lock_flags, read by sleeper/ingest_lock_flags.py) -> nba_lock_decisions, source
    sleeper_flag. Returns the (roster, slot) pairs covered. A flag whose game has no score row yet waits for the next run."""
    cur.execute("SELECT to_regclass('sleeper_lock_flags'), to_regclass('sleeper_game_map')")
    if not all(cur.fetchone()):
        return set()
    cur.execute("""SELECT f.roster_id, f.slot, f.sleeper_player_id, m.nba_game_id FROM sleeper_lock_flags f
                   JOIN sleeper_game_map m ON m.sleeper_game_id = f.sleeper_game_id
                   WHERE f.league_id = %s AND f.week_number = %s AND m.nba_game_id IS NOT NULL""", (league, week))
    covered = set()
    for roster, slot, sid, gid in cur.fetchall():
        nba, name = xw.get(sid) or (None, sid)
        g = next((x for x in games.get(nba) or [] if x["id"] == gid), None)
        if g is None:
            continue
        covered.add((roster, slot))
        if not args.dry_run:
            record(cur, league, roster, args.season_id, week, slot, sid, nba, name, g, "sleeper_flag", overwrite=True)
    return covered


IDS_DDL = """CREATE TABLE IF NOT EXISTS sleeper_player_ids (
    sleeper_player_id text PRIMARY KEY, nba_player_id integer, player_name text, updated_at timestamptz NOT NULL DEFAULT now())"""


def build_xw(cur, league, dry):
    """{sleeper_player_id: (nba_player_id, name)} for every player who was on a roster at any run, not just today. A week is
    re-checked for days after it closes, and a player dropped in between is no longer in roster_ownership (Reed, roster 5, week 1),
    which left his slot without games. sleeper_player_ids keeps every id the runs have seen; it is filled from roster_ownership
    each run and, once, from the locks already stored."""
    if not dry:
        cur.execute(IDS_DDL)
        cur.execute("""INSERT INTO sleeper_player_ids (sleeper_player_id, nba_player_id, player_name)
                       SELECT DISTINCT ON (sleeper_player_id) sleeper_player_id, nba_player_id, player_name FROM nba_lock_decisions
                       WHERE sleeper_player_id IS NOT NULL AND nba_player_id IS NOT NULL ORDER BY sleeper_player_id, recorded_at DESC
                       ON CONFLICT (sleeper_player_id) DO NOTHING""")
        cur.execute("""INSERT INTO sleeper_player_ids (sleeper_player_id, nba_player_id, player_name)
                       SELECT DISTINCT ON (sleeper_player_id) sleeper_player_id, nba_player_id, player_name FROM roster_ownership
                       WHERE league_id = %s AND nba_player_id IS NOT NULL ORDER BY sleeper_player_id
                       ON CONFLICT (sleeper_player_id) DO UPDATE SET nba_player_id = EXCLUDED.nba_player_id,
                           player_name = EXCLUDED.player_name, updated_at = now()""", (league,))
    xw = {}
    cur.execute("""SELECT DISTINCT ON (sleeper_player_id) sleeper_player_id, nba_player_id, player_name FROM nba_lock_decisions
                   WHERE sleeper_player_id IS NOT NULL AND nba_player_id IS NOT NULL ORDER BY sleeper_player_id, recorded_at DESC""")
    xw.update({s: (n, nm) for s, n, nm in cur.fetchall()})
    cur.execute("SELECT to_regclass('sleeper_player_ids')")
    if cur.fetchone()[0]:
        cur.execute("SELECT sleeper_player_id, nba_player_id, player_name FROM sleeper_player_ids")
        xw.update({s: (n, nm) for s, n, nm in cur.fetchall()})
    cur.execute("SELECT DISTINCT sleeper_player_id, nba_player_id, player_name FROM roster_ownership WHERE league_id = %s", (league,))
    for s_, n, nm in cur.fetchall():
        if n is not None or s_ not in xw:
            xw[s_] = (n, nm)
    return xw


def cur_has_flags(cur):
    cur.execute("SELECT to_regclass('sleeper_lock_flags')")
    return cur.fetchone()[0] is not None


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


def unmatched_values(states, final_sid, week_end, wk, original, nba):
    """[(value, times_seen)] Sleeper reported for this slot after the week ended that equal no game of the player's, neither at its
    current score nor at its pre-correction score. Used to say WHY a team does not add up."""
    seen = defaultdict(int)
    for ts, sid, _nba, pts in states:
        if sid == final_sid and pts and ts.astimezone().date() > week_end:
            seen[round(float(pts), 2)] += 1
    out = []
    for v, n in sorted(seen.items()):
        if any(abs(float(g["score"]) - v) <= OBS_TOL for g in wk):
            continue
        if any((g["id"], nba) in original and abs(original[(g["id"], nba)] / 100 - v) <= OBS_TOL for g in wk):
            continue
        out.append((v, n))
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
    # A slot that was never locked takes the game of the player's TEAM that ended the week. If he sat that game out, Sleeper
    # scores the slot 0 (week 3, roster 4: Sabonis played 11/2, 11/4 and 11/6, missed 11/8, and the team total only adds up
    # with his slot at 0). So each unlocked slot also gets that missed game as a 0.00 candidate; the total decides.
    cur.execute("SELECT week_start_date FROM fantasy_weeks WHERE season_id = %s AND week_number = %s", (args.season_id, week))
    week_start = cur.fetchone()[0]
    cur.execute("""SELECT DISTINCT ON (team_id) team_id, game_id, game_date FROM game_logs
                   WHERE season_id = %s AND game_date BETWEEN %s AND %s ORDER BY team_id, game_date DESC, game_id DESC""",
                (args.season_id, week_start, week_end))
    team_last = {t: (gid, gd) for t, gid, gd in cur.fetchall()}
    cur.execute("""SELECT DISTINCT ON (player_id) player_id, team_id FROM game_logs
                   WHERE season_id = %s AND game_date <= %s ORDER BY player_id, game_date DESC""", (args.season_id, week_end))
    player_team = dict(cur.fetchall())
    for roster in sorted(last_starters):
        if roster not in totals:
            continue
        target_pts, status = totals[roster]
        if status == "ruling":
            continue   # the commissioner changed this total by hand; the locks stay NBA truth and the gap is recorded as explained_diff
        if status and not (args.recheck or args.refinalize):
            continue   # already settled in an earlier run
        starters = last_starters[roster]
        cands, picks, priors, names = [], [], [], []
        for i, slot in enumerate(SLOT_ORDER):
            pid = starters[i]
            nba = (xw.get(pid) or (None, None))[0]
            wk = sorted(games.get(nba) or [], key=lambda x: x["date"])
            have = {} if args.recheck else rows[roster]
            if have.get(slot, (None, None))[1] in FIXED_SOURCES:
                wk = [x for x in wk if x["id"] == have[slot][0]] or wk   # your own entry, or a lock Sleeper itself reports, is fixed
            else:
                missed = team_last.get(player_team.get(nba))
                if missed and (not wk or missed[1] > wk[-1]["date"]):
                    wk = wk + [{"id": missed[0], "date": missed[1], "score": 0, "dnp": True}]
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
            best = sum(cands[i][min(priors[i], len(cands[i]) - 1)] for i in range(len(SLOT_ORDER)))
            print(f"      why: Sleeper's total is {target_pts:.2f}; the best picks add to {best / 100:.2f} ({(target - best) / 100:+.2f})")
            for i, slot in enumerate(SLOT_ORDER):
                odd = unmatched_values(per_slot.get((roster, slot), []), starters[i], week_end, picks[i], original, names[i][1])
                if odd:
                    his_games = ", ".join(f"{float(x['score']):.2f} on {x['date']}" for x in sorted(picks[i], key=lambda x: x["date"])) or "no games"
                    print(f"      {slot:<5} {names[i][2]}: Sleeper reported {', '.join(f'{v:.2f} (x{n})' for v, n in odd)}, which is none of his games ({his_games})")
        else:
            source = "checksum" if st in ("unique", "offset") else "checksum_" + st
        for i, slot in enumerate(SLOT_ORDER):
            wk = picks[i]
            if not wk:
                continue
            g = wk[sol[i]]
            pid, nba, name = names[i]
            print(f"      {slot:<5} {name}  {float(g['score']):.2f} on {g['date']}{' (did not play; unlocked slot scored 0)' if g.get('dnp') else ''}  [{source}]")
            written += 1
            if not args.dry_run:
                record(cur, league, roster, args.season_id, week, slot, pid, nba, name, g, source, overwrite=True, keep=FIXED_SOURCES)
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
    xw = build_xw(cur, league, args.dry_run)
    if args.recheck and not args.dry_run:
        raise SystemExit("--recheck only works with --dry-run (it would overwrite nothing, but it would print locks already recorded)")
    now = datetime.now(timezone.utc)
    total = 0
    for week, week_end in weeks.items():
        cur.execute("""SELECT player_id, game_id, game_date, fantasy_score FROM game_fantasy_scores_weekly_effective
                       WHERE season_id = %s AND week_number = %s""", (args.season_id, week))
        games = defaultdict(list)
        for pid, gid, gd, sc in cur.fetchall():
            games[pid].append({"id": gid, "date": gd, "score": sc})
        flagged = flag_locks(cur, args, league, week, games, xw)
        if flagged:
            print(f"  week {week}: {len(flagged)} lock(s) read straight from Sleeper's lock flags")
            total += len(flagged)
        cur.execute("""SELECT roster_id, slot FROM nba_lock_decisions WHERE league_id = %s AND week_number = %s""", (league, week))
        done = {(r, sl) for r, sl in cur.fetchall()} | flagged
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
        # Sleeper's own flags list every explicit lock, so a slot with no flag is not locked yet. The older guess from frozen
        # points (which once called Queen locked) is used only where the flag tables do not exist, or under --recheck.
        flags_on = cur_has_flags(cur) and not args.recheck
        if flags_on and not args.dry_run:
            cur.execute("""DELETE FROM nba_lock_decisions d WHERE d.league_id = %s AND d.week_number = %s AND d.source = 'sleeper_frozen'
                           AND NOT EXISTS (SELECT 1 FROM sleeper_lock_flags f WHERE f.league_id = d.league_id AND f.week_number = d.week_number
                                           AND f.roster_id = d.roster_id AND f.slot = d.slot)""", (league, week))
            if cur.rowcount:
                print(f"  week {week}: removed {cur.rowcount} guessed lock(s) that Sleeper's flags do not show as locked")
        for (roster, slot), states in ([] if flags_on else per_slot.items()):
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
