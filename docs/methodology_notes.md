# Methodology Notes

## Lock/Hold Decision Logic — Design History

- **8/3/26:** Rejected self-relative-only design (README v1: lock based on player's own rolling avg/stdev). Decided on two-layer approach: league-relative absolute bar first, player-relative hold-value layer second.
- **8/9/26:** Redesigned again — absolute bar alone let high-average stars auto-LOCK on ordinary games. Fixed to a fully self-relative ceiling requirement: `LOCK` if `fantasy_score >= GREATEST(35, player's own mean + 0.5*stddev)`. Below that: `PASS` if no games remain that week, else `HOLD`. This is the current, deployed logic (`game_lock_signal.sql`).

## Fantasy Scoring Formula — Confirmed 8/3/26

Verified exactly against a real Sleeper score (Jokić vs. PHX, 3/7/25, 113.10) — confirmed double-double/triple-double bonuses stack. No technical/flagrant foul data available; accepted as a known gap (a tech is usually accompanied by other red flags anyway).

## Ownable Player Pool — Defined 8/4/26

Eligibility: `mean + k*stddev >= threshold` (ceiling-based, not raw average — average-based selection misses streamer-type spike players). Calibrated **k=1.25, threshold=35** via Phase 1 sensitivity grid (stricter option preferred over higher clear-rate alternative). Target pool size ~150-210/season.

## Player Tiers — Added 8/9/26

Season-relative rank by `avg_fantasy_score` within the pool: top 25 = elite, 26-75 = mid, 76+ = lower (`player_tiers.sql`). Used for hold-value curve fitting. Fixed a real bug: `ROW_NUMBER()` needs a `player_id` tiebreaker or ties can flip rank/tier assignment between separate query runs — fixed across all 5 affected files.

## Hold-Value Curve (percentage_to_lock) — Built 8/8/26, Refit 8/9/26

Empirical relationship between games-remaining and "does a later game beat this one" is a **saturating exponential**, `y = a*(1-(1-b)^k)` — confirmed NOT linear (diminishing returns per extra game). Progression: single pooled curve → 2 variance-based buckets → **3 tier-based curves (current)**. Refit 8/9/26 after finding the old curve systematically under-predicted real hold value by 10-16pp — it was fit on the full unconditioned population instead of the actual below-ceiling decision population. Refit uses sample-size-weighted least squares so tiny-sample games-remaining=4 points don't distort the fit.

## Ownable Pool Simulation & Threshold Calibration (Phase 2) — Complete 8/9/26

Built a weekly-outcome simulator: walks each player's real games in order, banks the first `LOCK`, compares to a perfect-hindsight oracle and a naive baseline (flat median score, 30.3). Grid-searched the ceiling design's two constants (`floor`, `ceiling_multiplier`) on train seasons (2021-24), validated on 2024-26. **Winner: floor=35, ceiling_multiplier=0.5** — edge over naive +1.712 train / +1.720 validate (near-identical, not overfit). Deployed.

Checked and ruled out: elite players are NOT disproportionately penalized by the uniform multiplier (tested via oracle-capture by tier — no meaningful difference).

**Replacement-level assumption:** PASS outcomes use a flat **30** points (not derived — real waiver-wire replacement value can't be estimated from box-score data alone; needs real Sleeper transaction history, not yet integrated). Documented as an explicit placeholder, not a measured value.

## Sliding Threshold / Games-Remaining Context — Built 8/4-8/6/26

`fantasy_weeks` (24 weeks/season, Mon-Sun) + `games_remaining_in_week` (from `team_schedule`, not `game_logs`, so it reflects the team's real schedule regardless of DNPs). B2B fatigue confirmed real (8/6/26): ~1.6% efficiency drop on 2nd night of back-to-back, not a minutes effect — not yet wired into the model (v1.1 item).

## Open Items

1. B2B correction into "effective games remaining" — confirmed real, not yet incorporated into the decision logic.
2. Injury-return uncertainty — `gap_reasons` has the data to test whether hold-value differs near a return from injury; not yet tested.
3. Sleeper API integration — needed for real roster/ownership data and real replacement-level values.
4. v2 parking lot: team pace/advanced stats, position-based scoring, draft pick analysis.
