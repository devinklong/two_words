# Methodology Notes

## Lock/Hold Decision Logic — Self-Relative vs. League-Relative Thresholds

**Initial framing (README v1):** Compare a completed game's score against a
player's own rolling 5-game/10-game average and standard deviation to flag it
as LOCK / PASS / EVALUATE.

**Problem identified:** This only answers "is this a good game for this
specific player," not the actual question that matters — "is this score good
enough for my roster slot, given what's competitively available across the
league." A player with a low mean and high variance could trigger a LOCK
despite being below what's actually competitive; a stable high-mean star
sitting at their own average might be worth holding, since they're likely to
beat it later in the week.

**Decision: two-layer approach, league-relative first.**

1. **League-relative benchmark** — an absolute scoring bar (independent of
   the specific player) representing "good enough to stop worrying about it."
2. **Player-relative probability layer** — only applied *if* a score falls
   below the league-relative bar: use the player's own rolling mean/stdev to
   estimate the likelihood of a better score later in the week (hold-value).

The tool's real value is concentrated in players who don't average 40+
fantasy points/game — high-average stars clear the league-relative bar
automatically most nights.

## Fantasy Scoring Formula — CONFIRMED

Implemented as `game_fantasy_scores` (per-game score for every row in
`game_logs`, all 5 seasons). Verified against a real Sleeper result: Jokić
vs. Phoenix, 3/7/25 — computed 113.1 against a confirmed actual score of
113.10. This also empirically resolved that double-double (+3) and
triple-double (+5) bonuses **stack** rather than one replacing the other.

`player_season_fantasy_stats` aggregates to per-player, per-season
averages/stddev (2,598 player-seasons across 5 years). Confirmed only ~4.8%
(125) average 40+ fantasy points/game, validating the target-population
assumption.

Known accepted limitation: no technical/flagrant foul penalty (`game_logs`
has no columns distinguishing these from ordinary personal fouls). Accepted
as-is — a technical/flagrant is usually accompanied by other negative
signals in the same game, so it's unlikely to be invisible in practice.

## Ownable Player Pool — Eligibility Definition

**Rejected approach:** selecting the pool by raw season average. This
systematically misses waiver-wire/role-change players who produce lockable
spike games without a high season average (the "Tobias Harris problem" —
consistent players can rank highly by average while rarely producing an
actual lockable spike, and season-average-of-averages flattens away the
exact spikes that make a score lockable).

**Decision:** eligibility uses `mean_fantasy_score + k*stddev >= threshold`
(ceiling-based, not average-based) — this correctly captures players capable
of a lockable spike, including streamers/waiver adds once they've shown that
capability, rather than requiring a good season average to even qualify.

**Pool size:** ~150 players (not 200) — 30 taxi-squad spots are usually
1st/2nd-year players presumed not to produce lockable scores when activated;
~160 remaining minus ~10 for in-season dilution (stashes, injury churn).
Should be a tunable parameter, not hardcoded.

## Threshold Calibration — Phase 1 (Sensitivity Grid) Complete, Phase 2 (Backtest) Pending

**Phase 1 grid results (8/4/26):** tested k (0.75, 1.0, 1.25) x threshold
(35-48). Pool size grows with k (more inclusive of high-variance players),
but clear_rate *drops* as k increases at the same threshold (t=35: 49.2% at
k=0.75 → 42.5% at k=1.0 → 37.3% at k=1.25) — empirically confirms that a
more inclusive, upside-chasing pool costs average consistency. Only k=0.75
approaches a 50% clear rate in the tested range.

**Important limitation:** clear rate alone can't finalize the decision — a
lower clear rate isn't necessarily worse if those clears represent bigger
point swings. Needs an actual outcome-based metric, not just hit frequency.

**Phase 2 (not yet built):** a real weekly-outcome backtest — simulate the
lock/hold policy against real historical weeks, compare against a
perfect-hindsight oracle and a naive baseline, grid-search (k, threshold)
pairs for what actually maximizes results. Requires a train/test split
(calibrate on 2021-24, validate on 2024-26) to guard against overfitting the
threshold to this specific 5-season sample.

**On curve shape (linear vs. exponential) for a sliding threshold:** don't
assume a shape. Compute the empirical relationship first as a step
function/lookup table with no assumed form; only fit a parametric curve if
extrapolation/generalization is needed, and compare candidate shapes via the
same held-out validation split — not by picking one from intuition.

## Sliding Threshold Design — Time-Remaining-in-Week Dependency

**Key insight (8/4/26):** the lock threshold likely isn't a single static
number — it should slide based on how many games the player has remaining
in the current fantasy week. Early in the week with games left, the bar
should be higher (more chances to beat it later); on the last remaining
game, the bar should be lower (no more chances — use it or lose it).

This requires "games remaining this week for this player" as a model input,
which required mapping `game_date` to real league week boundaries first.

**Built (8/6/26):**
- `fantasy_weeks` — 24 weeks/season (21 regular + 3 playoff), Monday-Sunday,
  derived from each season's actual first game date. Playoff byes affect
  which teams play in week 22, not the date boundaries themselves.
- `game_fantasy_scores_weekly` — joins `fantasy_weeks` onto every game.
  122,569 of 128,434 total games fall inside the league's 24-week window;
  the ~5,865-game gap is expected (fantasy season ends before the NBA
  regular season does).
- `game_fantasy_scores_weekly_context` / `_full` — adds
  `games_before_in_week`, `games_remaining_in_week`,
  `total_team_games_this_week`, `is_last_game_of_week`, computed from
  `team_schedule` (not `game_logs`), so counts reflect the team's actual
  schedule regardless of whether the player personally plays every game.
  Validated: 0 internal-consistency mismatches across all rows.

## Back-to-Back Effect — Confirmed Real (8/6/26)

For high-usage players (season avg 30+ fantasy pts): avg_fantasy_score
drops from 38.44 (normal rest) → 38.33 (first night of B2B) → 37.69 (second
night of B2B), while avg_minutes stays essentially flat (32.78 vs. 32.72).
Flat minutes rules out a rest-management confound — this is a genuine
per-minute efficiency decline from fatigue, not reduced playing time, which
means any correction needs to target efficiency, not discount by minutes.
Effect is smaller (~1.6%) but present in the full population too.

**Status:** confirmed as a real signal, not yet incorporated into a formal
"effective games remaining" correction — that's a planned refinement to the
sliding-threshold model, not yet built.

## Hold-Value Step Function — Empirical Lookup Table (8/8/26)

Built `hold_value_step_function.sql`: for each `games_remaining_in_week`
level, what % of the time did a later game that week actually score higher
(`hold_wins_pct`), and by how much on average (`avg_score_delta_if_hold`).
This is the step-function/lookup table called for above — no curve shape
assumed yet.

| games_remaining | hold_wins_pct | avg_score_delta_if_hold |
|---|---|---|
| 1 | 42.0% | -0.22 |
| 2 | 58.6% | +4.36 |
| 3 | 66.6% | +6.73 |
| 4 | 70.2% (n=171, small sample) | +8.55 |

Monotonic increase in both columns as games remaining grows — consistent
with the sliding-threshold hypothesis. grw=1 sitting just under 50% is a
reasonable anchor for the "last game of week" end of the curve.

**Curve fit + bucketing (8/8/26):** confirmed the shape is NOT linear —
diminishing marginal returns per additional game (grw 1→2: +16.6pp, grw
2→3: +8.0pp, grw 3→4: +3.6pp), consistent with correlated rather than
independent future performance within a week (tested and rejected a naive
independence formula, `1-(1-p)^k`, which overshoots badly). Fit a
saturating curve `y = a*(1-(1-b)^k)` via `scripts/fit_hold_value_curve.py`
(`scipy.curve_fit`), then upgraded from one pooled curve to **two curves
by player variance tier** (`player_variance_buckets`, NTILE(2) split on
`stddev_fantasy_score` per season) — a single pooled curve treats a
streaky player and a steady player identically, which defeats the point
of a player-relative layer. Confirmed real signal, not noise: bucket gap
widens with games remaining (1.8pp at grw=1 → 10.4pp at grw=4), and the
original pooled curve sits almost exactly between the two bucketed
curves, as expected.

| games_remaining | bucket 1 (steadier) | bucket 2 (streakier) |
|---|---|---|
| 1 | 40.6% | 42.4% |
| 2 | 56.2% | 61.9% |
| 3 | 62.2% | 70.8% |
| 4 | 64.5% | 74.9% |

Implemented as `percentage_to_lock` (`schema/percentage_to_lock.sql`) =
`1 - hold_win_probability(games_remaining, bucket)` — the complement,
since higher should mean more reason to lock. Fixed at `1.0` when
`games_remaining_in_week = 0` (no future games, no real decision).

## v1.0 Complete — `game_lock_signal` (8/8/26)

**Ownable pool calibrated:** `schema/ownable_player_pool.sql` implements
`mean_fantasy_score + k*stddev >= threshold` with **k=1.25, threshold=35**
— the stricter of the two Phase 1 grid options (37.3% clear rate),
matching the earlier stated preference for fewer, higher-confidence locks
over frequency. `games_played >= 20` floor for sample-size consistency
with `player_variance_buckets`. Both constants named/commented as
first-pass, pending Phase 2.

**`game_lock_signal` decision logic** (`schema/game_lock_signal.sql`),
restricted to the ownable pool:
- `fantasy_score >= 35` → **LOCK**
- Below 35, `games_remaining_in_week = 0` → **PASS** (never cleared the
  bar, no more chances this week — look elsewhere, e.g. waivers)
- Below 35, games remaining → **HOLD** (`percentage_to_lock` exposed as
  the confidence figure on the row, not pre-baked into more categories)

**Design correction during build:** the first version added a 4th branch
— `percentage_to_lock` above some cutoff (tried 0.55, then 0.80) → LOCK
anyway even below the bar. This was wrong, not just miscalibrated: it let
genuinely bad scores (e.g. 10.85, nowhere near 35) get labeled LOCK
purely because `games_remaining=0` fixes `percentage_to_lock` at 1.0 —
backwards logic, since "no more chances to improve" is a reason to PASS
on a bad score, not call it good. Removed the branch entirely rather than
re-tuning the cutoff; the absolute bar now gates LOCK unconditionally,
hold-value only ever chooses between HOLD/PASS below it. Also removed two
never-validated cutoff constants from the design in the process.

**Validation (8/8/26):** 15 players × 5 seasons (ranks 1, 11, 21, ..., 141
by `avg_fantasy_score`, `tests/lock_signal_validation.sql`), 75
player-seasons, 4,613 game rows. All 4 structural invariants held with
zero violations (no LOCK below 35, no PASS with games remaining, no HOLD
at games_remaining=0, no score≥35 without LOCK). LOCK% by rank is cleanly
monotonic and consistent across all 5 seasons independently — ~98-100% at
rank 1 down to ~15-20% by rank 141 — confirming the tool's actual
recommendations track real player quality, not just passing structural
checks.

## Open Items

1. Phase 2 weekly-outcome backtest — the real validation step. Simulate
   the lock/hold policy against historical weeks, compare to a
   perfect-hindsight oracle and a naive baseline, grid-search/re-derive
   k=1.25, threshold=35 (and the hold-value curve shape) rather than
   treating today's first-pass values as final. Train on 2021-24,
   validate on 2024-26.
2. Incorporate the confirmed B2B effect into an "effective games remaining"
   correction rather than treating all remaining games as equal value.
3. Sleeper API integration — needed to replace the ~150-player proxy pool
   with the league's actual real-time ownership data.
4. Parked for v2: team pace/advanced stats (`nba_api`'s
   `leaguedashteamstats`), position-based lock scoring, draft pick profile
   analysis.
5. Injury-return uncertainty, not yet incorporated (8/9/26): unlike a B2B
   (consistent small tax, same role/matchup), a return from injury is
   inconsistent in direction — minutes restriction, rust, or a normal role
   immediately are all plausible, so remaining-game decision points near an
   injury return likely overstate true hold-value in the pooled table.
   `gap_reasons` already has per-game injury-report status to test this —
   worth re-running the hold-value step function split by recent
   injury-report status to see if the curve shifts. Parked as a v1.1
   refinement, same tier as the B2B correction (item 2), not a v1 blocker.
