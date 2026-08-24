# Methodology Notes

## Version Summary

**v1.0 — Core pipeline (complete 8/9/26).** Fantasy scoring formula (verified against a real Sleeper score), ownable player pool (ceiling-based eligibility: `mean + 1.25*stddev >= 35`, not raw average), self-relative LOCK/HOLD/PASS decision (`GREATEST(35, mean + 0.5*stddev)`, not a flat threshold), and a tier-based hold-value curve. Calibrated via a proper train/validate backtest (`floor=35`, `ceiling_multiplier=0.5`) — edge over naive +1.71 train / +1.72 validate, confirmed not overfit.

**v1.1 — Two real corrections tested (complete 8/10/26).** Back-to-back fatigue: confirmed real (~1.6% efficiency drop, second night of a B2B), wired into `effective_games_remaining_in_week` as a fractional discount. Injury-return penalty: built, tested, and correctly **rejected** — a targeted check restricted to the exact decisions it could change showed it was wrong more often than right (32.5%/42.5% correct, both below the 50% coinflip bar). That's a real negative result from the process working as intended, not a failed feature.

**v1.2 — Made the tool usable in real time, not just descriptive (complete 8/11/26).** Daily `nba_api` ingestion for box scores, schedules, and scoreboard data — including migrating off two endpoints (`BoxScoreTraditionalV2`, `ScoreboardV2`) that turned out to return no real data at all for the 2025-26 season, not just "deprecated." `gap_reasons` and the `game_fantasy_scores_weekly_effective` sync are chained into every `game_logs` insert path (daily load, both backfill scripts), keeping derived data current without manual tracking. `game_fantasy_scores_weekly_effective` itself was redesigned from a full-rebuild materialized view into an incrementally-synced table. A Python input model (`lock_decision_input.py`) checks the database first and only falls back to a live pull or manual stat entry when a game genuinely isn't loaded yet, with `team_id` auto-resolved from the player's own history. The `ScoreboardV3` home/away derivation was spot-checked at scale (43 games across 8 dates spanning season openers, holiday slates, and end-of-season rest games) with zero mismatches.

**v2.0 — Team-level data (CLOSED 8/12/26, exhaustively negative).** Tested team pace/rating stats (pace, ORtg/DRtg/NetRtg, own+opponent, pooled + confound-controlled + targeted backtest) as a layer on the player-based `lock_bar`. Every candidate signal — opponent def_rating, opponent pace, own off_rating, B2B, home/away — either failed significance or collapsed once confounds (schedule position, team composition) were controlled for. No team-level signal cleared the bar for production. Full 22-test log in `v2_roadmap_section.md` item 9.

**v3.0 — Sleeper league integration (CLOSED 8/15/26).** Full Sleeper API integration: raw ingestion (leagues/rosters/users/matchups/transactions), player ID crosswalk (394 matched), scoring-settings-as-config, roster/ownership tracking, historical matchup standings, full relationship diagram, opponent threat scouting (`opponent_scout.py`), waiver-wire target finder (`waiver_wire_finder.py`). 2025-26 weeks 1-18 hit an unresolved Sleeper API data-reliability issue (root cause never confirmed after 6 tested theories) — resolved practically via manual re-entry, hand-verified against the app's real record; full writeup in `docs/step6_verification_results.md`. See `v3_roadmap_sleeper_integration.md` for the full step-by-step log.

**v3.1 — Full per-row verification pass + 3 real bugs found and fixed (complete 8/23/26).** Closed the last known gap in the scoring formula: technical/flagrant foul penalties, previously an accepted omission (see formula section below, now updated), are now correctly applied — backfilled from `PlayByPlayV3`, every subtype hand-verified against real Sleeper scores or the NBA rulebook rather than assumed from label text. Also ran the first true per-row (not aggregate) audit of both `player_scores` and `team_scores` against the manually-verified xlsx from Step 6 — surfaced and fixed 15 xlsx transcription typos, a 4-player crosswalk suffix-matching bug (see `architecture_risks.md` #11), and 23 rows where `team_scores` had gone stale relative to the real app for a completed season (`architecture_risks.md` #10). Along the way, caught and fixed a real, unrelated regression in `ownable_player_pool` that had silently dropped every historical season from `game_lock_signal` (`architecture_risks.md` #9) — found by `rebuild_lock_pipeline.py`'s own regression check doing exactly what it was built to do. Final state: 0 mismatches on both `player_scores` and `team_scores`, full pipeline rebuild clean against an updated known-good baseline.

**v3.2 — Position-based scoring analysis (complete 8/24/26).** First real investigation of the v2 parking-lot item. Two real bugs found before any analysis: `sleeper_player_crosswalk.sleeper_position` was sourced from Sleeper's singular `position` field, not the true `fantasy_positions` array — 68% of crosswalked players are actually multi-eligible, and 4 players' stored position didn't even appear in their own eligibility array (confirmed against the real Sleeper app). Fixed via a new `sleeper_player_fantasy_positions` child table (1NF, one row per eligible position, current-state-only by design) and integrated into the crosswalk build. Full statistical investigation (`scripts/analysis/analyze_position_scoring_distributions.py`, all 5 backfilled seasons, ~146k player-game rows) then tested whether position predicts scoring — deliberately framed as descriptive (distribution comparison), not predictive, since the position table has no historical tracking. See "Position-Based Scoring Findings" below for the full result.

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

## Position-Based Scoring Findings — v3.2, complete 8/24/26

**Framing (deliberate):** the question tested is "do position-eligible groups show different score distributions in this league's actual data," not "does position predict future scoring" — the position table is current-state-only (Sleeper reassigns eligibility mid-season, never historically tracked), so applying today's labels retroactively to past seasons can't support a genuine predictive claim. All tests below are descriptive.

**Method:** Kruskal-Wallis (not ANOVA — fantasy scores are right-skewed) per season and per season+tier, plus epsilon-squared effect size, Levene's test for variance, Dunn's post-hoc pairwise comparisons, and Mann-Whitney U for 2-group splits. All 5 backfilled seasons, ~146k player-game-position rows. Full test suite lives in `scripts/analysis/analyze_position_scoring_distributions.py` (also supports `--player <name>` for a single-player outlier check against tier+position peers).

**Headline: position explains very little of the variance overall.** Nearly every test is statistically significant (large sample sizes detect even tiny differences), but epsilon-squared is "negligible" almost everywhere. Position genuinely does not predict scoring for most players.

**The one real, consistent exception: elite tier.** Centers showed a real premium over non-Centers, significant nearly every season — but an outlier-robustness check (re-running the test with the top-scoring Centers excluded per season) showed this premium is carried almost entirely by 2-3 recurring players, not a broad positional effect. Center vs. everyone flips to a slight *negative* once the true outliers are removed.

**Those outliers, identified by nba_player_id and cross-checked:** Nikola Jokić (`203999`, single-position C) is the dominant single driver — excluding him alone collapses the elite-Center signal to noise in 4 of 5 seasons. Joel Embiid (`203954`, single-position C) and Anthony Davis (`203076`, C/PF) contribute the rest of the effect in the seasons they were healthy. Victor Wembanyama (`1641705`, single-position C) joined the outlier group starting 2024-25 — his own single-player check shows him as a real, significant outlier (rank-biserial +0.26 to +0.29 vs. tier peers) in both his healthy seasons, well above the group-level Center effect even including Jokić.

**Durability matters as much as scoring here, and this tool's own mechanics make it matter more.** Real games-played pulled directly from `game_logs`: Embiid's participation collapsed to 23% of possible games in 2024-25, Anthony Davis's to 24% in 2025-26 — both severe, recent, and not reflected in their per-game averages. Jokić, by contrast, has stayed at 79-94% every season. Since Lock-In mode is hold-until-spike (a bad game gets held/skipped, not banked), missed games are a double penalty — fewer real chances to catch a spike, not just a smaller season total. Practical read: Jokić and (recently) Wembanyama are legitimately safe premium assets; Embiid and AD's box-score averages no longer reflect their real recent-season value.

**A second, independent finding: at the elite tier only, single-position-eligible players significantly outscore multi-eligible ones — and this survives excluding every known outlier.** Re-running the eligibility-count test at the elite tier with all 6 flagged outlier players removed still shows single-position players beating both 2-position and 3+-position groups (significant in 4 of 5 seasons, pooled post-hoc p<0.02 for every pairwise comparison). This means flexibility is **not** a scoring-neutral tiebreaker at the top of the talent pool the way it appears to be everywhere else — a real, if modestly-sized, cost to preferring a multi-eligible elite player over a single-position one, all else equal. Outside the elite tier this pattern doesn't hold (mixed/non-significant at mid, lower, and waiver tiers).

**Practical guidance, superseding any earlier "position-based" heuristic:**
- **Trade/keeper valuation:** don't pay a position premium for "elite Center" as a category — pay for specific, provably-outlier players (checkable via `--player`), and treat Embiid/AD's recent-season numbers with real skepticism given their durability collapse.
- **Waiver/injury replacement:** actively avoid a single-position Center pickup over a comparable flexible G/F — Centers are the worst-scoring position at replacement level, every season tested.
- **Roster construction generally:** flexibility (multi-position eligibility) is a legitimate, real tiebreaker when position itself is a wash — true for the large majority of players — but not a free lunch at the elite tier specifically, where single-position players have shown a genuine scoring edge independent of the known outliers.
- **Untested, flagged for future work:** whether "single-position elite" correlates with usage/role concentration (a plausible basketball explanation for the finding above) was not checked against any usage-rate data — this is a hypothesis, not a confirmed mechanism.

Not yet built: any of this wired into `waiver_wire_finder.py` / `opponent_scout.py` — findings delivered as analysis only. Team-side analysis (which locked slot-positions score the most) remains unstarted, blocked on confirming whether Sleeper's `starters[]` order reliably matches `roster_positions[]` index-for-index.

## Open Items

1. **Replacement-level value (PASS outcomes)** — still a flat 30 placeholder (see "Ownable Pool Simulation" above), not derived from real waiver-wire data. Sleeper roster/transaction data is live now (v3.0), so this is buildable — just not yet built.
2. **v3.2 position-based scoring — CLOSED 8/24/26, see "Position-Based Scoring Findings" above.** Draft-pick analysis remains genuinely untouched — Sleeper's `/traded_picks` endpoint was scoped in v3.0's roadmap but never actually ingested.
3. **Non-functional backlog, not new scope:** consistency/refactor items (`docs/patch_list.md`) — architecture-risk items are now fully closed (11/11, see `docs/architecture_risks.md`).
4. **`sleeper_matchup_points_snapshots` staleness monitoring** — v3.1 fixed one real instance of a completed season's snapshots drifting stale relative to reality (`architecture_risks.md` #10), but nothing proactively re-checks this for a future completed season. Worth a periodic/scheduled re-verification once a season wraps, rather than relying on someone happening to run a full audit again.
5. **v3.2 team-side position analysis** — unstarted, blocked on confirming Sleeper's `starters[]`/`roster_positions[]` index alignment against real league data.
