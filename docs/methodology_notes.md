# Methodology Notes

## Version Summary

**v1.0 — Core pipeline (complete 8/9/26).** Fantasy scoring formula (verified against a real Sleeper score), ownable player pool (ceiling-based eligibility: `mean + 1.25*stddev >= 35`, not raw average), self-relative LOCK/HOLD/PASS decision (`GREATEST(35, mean + 0.5*stddev)`, not a flat threshold), and a tier-based hold-value curve. Calibrated via a proper train/validate backtest (`floor=35`, `ceiling_multiplier=0.5`) — edge over naive +1.71 train / +1.72 validate, confirmed not overfit.

**v1.1 — Two real corrections tested (complete 8/10/26).** Back-to-back fatigue: confirmed real (~1.6% efficiency drop, second night of a B2B), wired into `effective_games_remaining_in_week` as a fractional discount. Injury-return penalty: built, tested, and correctly **rejected** — a targeted check restricted to the exact decisions it could change showed it was wrong more often than right (32.5%/42.5% correct, both below the 50% coinflip bar). That's a real negative result from the process working as intended, not a failed feature.

**v1.2 — Made the tool usable in real time, not just descriptive (complete 8/11/26).** Daily `nba_api` ingestion for box scores, schedules, and scoreboard data — including migrating off two endpoints (`BoxScoreTraditionalV2`, `ScoreboardV2`) that turned out to return no real data at all for the 2025-26 season, not just "deprecated." `gap_reasons` and the `game_fantasy_scores_weekly_effective` sync are chained into every `game_logs` insert path (daily load, both backfill scripts), keeping derived data current without manual tracking. `game_fantasy_scores_weekly_effective` itself was redesigned from a full-rebuild materialized view into an incrementally-synced table. A Python input model (`lock_decision_input.py`) checks the database first and only falls back to a live pull or manual stat entry when a game genuinely isn't loaded yet, with `team_id` auto-resolved from the player's own history. The `ScoreboardV3` home/away derivation was spot-checked at scale (43 games across 8 dates spanning season openers, holiday slates, and end-of-season rest games) with zero mismatches.

**v2.0 — Team-level data (CLOSED 8/12/26, exhaustively negative).** Tested team pace/rating stats (pace, ORtg/DRtg/NetRtg, own+opponent, pooled + confound-controlled + targeted backtest) as a layer on the player-based `lock_bar`. Every candidate signal — opponent def_rating, opponent pace, own off_rating, B2B, home/away — either failed significance or collapsed once confounds (schedule position, team composition) were controlled for. No team-level signal cleared the bar for production. Full 22-test log in `v2_roadmap_section.md` item 9.

**v3.0 — Sleeper league integration (CLOSED 8/15/26).** Full Sleeper API integration: raw ingestion (leagues/rosters/users/matchups/transactions), player ID crosswalk (394 matched), scoring-settings-as-config, roster/ownership tracking, historical matchup standings, full relationship diagram, opponent threat scouting (`opponent_scout.py`), waiver-wire target finder (`waiver_wire_finder.py`). 2025-26 weeks 1-18 hit an unresolved Sleeper API data-reliability issue (root cause never confirmed after 6 tested theories) — resolved practically via manual re-entry, hand-verified against the app's real record; full writeup in `docs/step6_verification_results.md`. See `v3_roadmap_sleeper_integration.md` for the full step-by-step log.

**v3.1 — Full per-row verification pass + 3 real bugs found and fixed (complete 8/23/26).** Closed the last known gap in the scoring formula: technical/flagrant foul penalties, previously an accepted omission (see formula section below, now updated), are now correctly applied — backfilled from `PlayByPlayV3`, every subtype hand-verified against real Sleeper scores or the NBA rulebook rather than assumed from label text. Also ran the first true per-row (not aggregate) audit of both `player_scores` and `team_scores` against the manually-verified xlsx from Step 6 — surfaced and fixed 15 xlsx transcription typos, a 4-player crosswalk suffix-matching bug (see `architecture_risks.md` #11), and 23 rows where `team_scores` had gone stale relative to the real app for a completed season (`architecture_risks.md` #10). Along the way, caught and fixed a real, unrelated regression in `ownable_player_pool` that had silently dropped every historical season from `game_lock_signal` (`architecture_risks.md` #9) — found by `rebuild_lock_pipeline.py`'s own regression check doing exactly what it was built to do. Final state: 0 mismatches on both `player_scores` and `team_scores`, full pipeline rebuild clean against an updated known-good baseline.

**v3.2 — Position-based scoring analysis (complete 8/25/26).** First real investigation of the v2 parking-lot item, spanning both player-side (5 seasons) and team-side (2 seasons, real locked decisions) data. See "Position-Based Scoring Findings" below for the summary, or `v3_2_position_scoring_investigation.md` for the complete test-by-test log — every hypothesis, correction, and number in full.

---

## Lock/Hold Decision Logic — Design History

- **8/3/26:** Rejected self-relative-only design (README v1: lock based on player's own rolling avg/stdev). Decided on two-layer approach: league-relative absolute bar first, player-relative hold-value layer second.
- **8/9/26:** Redesigned again — absolute bar alone let high-average stars auto-LOCK on ordinary games. Fixed to a fully self-relative ceiling requirement: `LOCK` if `fantasy_score >= GREATEST(35, player's own mean + 0.5*stddev)`. Below that: `PASS` if no games remain that week, else `HOLD`. This is the current, deployed logic (`models/game_lock_signal.sql`).
- **8/10/26:** An injury-return penalty was added to this same formula, tested, and reverted after failing a targeted backtest. See v1.1 summary above; full writeup in `tests/injuries/`.

## Fantasy Scoring Formula — Confirmed 8/3/26, technical/flagrant gap closed 8/23/26

Verified exactly against a real Sleeper score (Jokić vs. PHX, 3/7/25, 113.10) — confirmed double-double/triple-double bonuses stack.

**Technical/flagrant foul penalty — CLOSED 8/23/26 (previously an accepted gap).** This league's real Sleeper scoring docks -2.0 per technical foul and -2.0 per flagrant foul; neither was previously implemented, since `game_logs` has no column distinguishing them from an ordinary personal foul. The original assumption ("a tech is usually accompanied by other red flags anyway") undersold the real impact — a full audit found 168 individual game-scores affected by this gap in the 2024-25/2025-26 seasons alone. Backfilled via `scripts/backfill_technical_flagrant_fouls.py` from `PlayByPlayV3`, with every foul subtype (`Technical`, `Flagrant Type 1/2`, `Double Technical`, `Hanging Technical`, and several confirmed-no-penalty categories like `Defense 3 Second` and `Flopping`) individually hand-verified against real Sleeper scores or the NBA's own rulebook — not assumed from label text, which was proven repeatedly unsafe (e.g. `Defense 3 Second`'s description literally contains "T.Foul" but carries no real penalty). See `architecture_risks.md`'s new items for the two real bugs this work surfaced along the way.

## Ownable Player Pool — Defined 8/4/26

Eligibility: `mean + k*stddev >= threshold` (ceiling-based, not raw average — average-based selection misses streamer-type spike players). Calibrated **k=1.25, threshold=35** via Phase 1 sensitivity grid (stricter option preferred over higher clear-rate alternative). Target pool size ~150-210/season.

**8/22/26:** Added a rolling-last-20-games bootstrap fallback for players without 20+ games logged in the current season yet (season-start cold-start problem). **8/23/26:** that same fix had a real bug — see `architecture_risks.md` #9 — fixed and reverified.

## Player Tiers — Added 8/9/26

Season-relative rank by `avg_fantasy_score` within the pool: top 25 = elite, 26-75 = mid, 76+ = lower (`models/player_tiers.sql`). Used for hold-value curve fitting. Fixed a real bug: `ROW_NUMBER()` needs a `player_id` tiebreaker or ties can flip rank/tier assignment between separate query runs.

## Hold-Value Curve (percentage_to_lock) — Built 8/8/26, Refit 8/9/26

Empirical relationship between games-remaining and "does a later game beat this one" is a **saturating exponential**, `y = a*(1-(1-b)^k)` — confirmed NOT linear (diminishing returns per extra game). Progression: single pooled curve → 2 variance-based buckets → **3 tier-based curves (current)**. Refit 8/9/26 after finding the old curve systematically under-predicted real hold value by 10-16pp — it was fit on the full unconditioned population instead of the actual below-ceiling decision population.

## Ownable Pool Simulation & Threshold Calibration (Phase 2) — Complete 8/9/26

Weekly-outcome simulator: walks each player's real games in order, banks the first `LOCK`, compares to a perfect-hindsight oracle and a naive baseline (flat median score, 30.3). Grid-searched `floor`/`ceiling_multiplier` on train seasons (2021-24), validated on 2024-26. **Winner: floor=35, ceiling_multiplier=0.5.**

**Replacement-level assumption:** PASS outcomes use a flat **30** points — not derived from real waiver-wire data, an explicit placeholder pending Sleeper API integration.

## Sliding Threshold / Games-Remaining Context — Built 8/4-8/6/26, B2B wired 8/10/26

`fantasy_weeks` (24 weeks/season, Mon-Sun) + `games_remaining_in_week`. B2B fatigue confirmed real and wired into `effective_games_remaining_in_week` (fractional discount on a second-night-of-B2B future game) — see v1.1 summary.

## Daily Data Flow — Built 8/10-8/11/26

`load_daily_game_logs.py` / `load_daily_team_schedule.py` pull one date at a time via `nba_api`, chaining `build_gap_reasons()` (scoped to exactly that date) and the `game_fantasy_scores_weekly_effective` sync (deliberately NOT date-scoped — catches up any missing rows regardless of source, so a 2-way/bench player backfilled through any path gets picked up automatically). See `get_scoreboard_games.py` for the `ScoreboardV3` home/away derivation and its at-scale validation.

## Position-Based Scoring Findings — v3.2, complete 8/25/26

Full test-by-test log (every hypothesis, correction, and number): `v3_2_position_scoring_investigation.md`. Summary of conclusions only:

- **Position barely predicts scoring overall** — most tests significant only because of large sample sizes, effect sizes mostly negligible.
- **The one apparent elite-Center premium is carried by 2-3 recurring outlier players** (Jokić dominant, Embiid/AD supporting), not a broad positional effect — confirmed by outlier-exclusion retesting. Embiid and AD's recent-season averages no longer reflect real value given severe games-missed collapses (Embiid 23% of games played 2024-25, AD 24% in 2025-26); Jokić stayed 79-94% every season.
- **Single-position eligibility carries a real elite-tier scoring edge**, independent of the known outliers — flexibility is not a free tiebreaker at the top of the talent pool the way it is everywhere else.
- **Team-side (real locked decisions, not raw per-game data) tells a different, resolved story:** the Center SLOT is the highest-scoring slot at every tier except elite, opposite the player-side replacement-tier finding. Root cause confirmed mathematically: Centers have a genuinely fatter right-tail distribution (not owner selectivity — locking behavior is statistically identical across positions) — exactly what `lock_bar`'s ceiling-chasing design is built to exploit. Replacement-level Centers combine the worst average outcome with the best spike-relative-to-floor upside of any position.
- **`lock_bar`'s core premise is directly validated against real outcomes:** clearing your own spike bar correlates with winning at every slot, real and significant (largest at Center).
- **Fantasy-slot scores show real but modest week-to-week co-movement, once a real between-team-skill confound is corrected for** (pooling all rosters together had roughly doubled the apparent effect). The residual is a genuine mix of shared NBA schedule density (bigger piece) and real same-team player stacking (smaller but confirmed) — not managers going through hot/cold streaks. Stacking same-team players increases weekly volatility but shows no real evidence of helping or hurting win rate once team quality is properly controlled for.
- **Practical guidance:** value specific outlier players in trades, not the Center position generally; be skeptical of Embiid/AD's box-score averages; prefer flexible G/F pickups over Centers at replacement level on average-scoring grounds, but don't discount a replacement Center as a speculative spike-chasing hold; treat same-team stacking as a risk-management lever, not a win-rate lever. None of this is wired into `waiver_wire_finder.py`/`opponent_scout.py` yet — analysis only.

## Open Items

1. **Replacement-level value (PASS outcomes)** — still a flat 30 placeholder (see "Ownable Pool Simulation" above), not derived from real waiver-wire data. Sleeper roster/transaction data is live now (v3.0), so this is buildable — just not yet built.
2. **v3.2 position-based scoring — CLOSED 8/25/26, see "Position-Based Scoring Findings" above.** Draft-pick analysis remains genuinely untouched — Sleeper's `/traded_picks` endpoint was scoped in v3.0's roadmap but never actually ingested.
3. **Non-functional backlog, not new scope:** consistency/refactor items (`docs/patch_list.md`) — architecture-risk items are now fully closed (11/11, see `docs/architecture_risks.md`).
4. **`sleeper_matchup_points_snapshots` staleness monitoring** — v3.1 fixed one real instance of a completed season's snapshots drifting stale relative to reality (`architecture_risks.md` #10), but nothing proactively re-checks this for a future completed season. Worth a periodic/scheduled re-verification once a season wraps, rather than relying on someone happening to run a full audit again.
5. **v3.2 team-side position analysis — CLOSED 8/25/26**, see "Position-Based Scoring Findings" above and `v3_2_position_scoring_investigation.md` Part 3 for the full slot-value + selectivity test results.
