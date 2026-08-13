## v2.0 Roadmap — Team-Level Data (planned 8/11-12/26)

Expected to be far more analysis/discovery work than data engineering — most of the raw ingestion either already exists or is a small extension of it; the real open question is whether any of this signal is real and where it belongs, not how to get the data.

1. **Backfill raw team box-score components** (FGM/FGA/FTA/OREB/DREB/TOV/PTS) for all 5 historical seasons via `BoxScoreTraditionalV3`'s team-stats frame, looping the `game_id`s already in `game_logs` rather than rediscovering games by date; daily loads already fetch this same frame and can start persisting it rather than discarding it.
2. **Build one shared per-game view** computing pace, ORtg, DRtg, and NetRtg from those raw components via the standard formulas — 3NF-compliant, no stored percentages, same pattern as `game_fantasy_scores`.
3. **Build a rolling/season-to-date aggregation** on top of that view (summing raw totals across games, not averaging per-game ratios) — the version that's actually predictive of a team's *upcoming* games, which is what this needs.
4. **Wire it onto `percentage_to_lock`/the HOLD decision specifically, not `lock_bar`** — a completed game's fantasy score can't be retroactively changed by team context, but whether a *future* game this week beats it is exactly the kind of forward-looking question team context is meant to inform.
5. **Test own-team and opponent-team effects separately** for each metric (8 hypotheses, not 4) — a player's own team's pace and their opponent's defense are different mechanisms that could point in different directions.
6. **Run a cheap bucketed diagnostic first** (tercile buckets per metric, compare fantasy-score delta vs. each player's own baseline) — same style as `b2b_analysis.sql` — before building anything heavier.
7. **Escalate to real multivariate/control-variable regression only if the diagnostic shows something real but tangled** between correlated metrics (e.g. pace and net rating moving together).
8. **Any signal that clears that bar still needs a targeted backtest** — restricted to the specific decisions it would actually change, same method that validated B2B and correctly killed the injury-return penalty — before it's trusted anywhere near the real decision logic.
9. Full testing log (team-level exploration, 8/12/26)

Every hypothesis tested this pass, in order. "Tested against" is percentage_to_lock (the real LOCK/HOLD decision output) and/or fantasy_score (the underlying production metric) — team-output-only tests (e.g. own_off_rating) are marked as such and were screening steps toward the real targets, not conclusions on their own.
## 9. Full testing log (team-level exploration, 8/12/26)

Every hypothesis tested this pass, in order. "Tested against" is percentage_to_lock (the real LOCK/HOLD decision output) and/or fantasy_score (the underlying production metric) — team-output-only tests (e.g. own_off_rating) are marked as such and were screening steps toward the real targets, not conclusions on their own.

| # | Variable | Type | Tested against | Method | Threshold | Result | Verdict |
|---|---|---|---|---|---|---|---|
| 1 | pace vs off_rating | bivariate (predictor-predictor) | N/A — confound check | Pearson CORR() | screening only | \|r\| ≤ 0.08, all granularities | Not a confound; independent |
| 2 | off_rating vs def_rating | bivariate (predictor-predictor) | N/A — confound check | Pearson CORR() | screening only | r = 0.08–0.12 | Weak; no two-way-good/bad pattern |
| 3 | home/away | bivariate | team off/def_rating only | Group-mean comparison | none | +1.9pt gap single-game; vanishes in rolling windows | Informational — superseded by #14 |
| 4 | own B2B | bivariate | team off/def_rating only | Group-mean comparison | none | Real dip at single-game level | 2nd confirmation of v1.1 effect |
| 5 | opponent B2B | bivariate | own team output only | Group-mean comparison | none | ~flat, no gap | No exploitable edge |
| 6 | opp_def_rating vs own_off_rating | bivariate | team output only | Pearson CORR() | none | r = 0.178 (t10), 0.269 (std) | Standout at this level — tested further at #16, #20, #22 |
| 7 | own_pace vs own_off_rating | bivariate | team output only | Pearson CORR() | none | r = -0.083 / -0.049 | Weak |
| 8 | opp_pace vs own_off_rating | bivariate | team output only | Pearson CORR() | none | r = -0.016 / 0.034 | ~None |
| 9 | pace gap vs own_off_rating | bivariate | team output only | Pearson CORR() | none | r = -0.049 / -0.058 | Weak |
| 10 | opp_def_rating | bivariate | **percentage_to_lock** | CORR + 5-bucket quantile swing | meaningful swing on 0–1 scale | corr 0.0025/-0.0042; swing -0.0071/-0.0034 | **DEAD** |
| 11 | own B2B (2nd night) | bivariate | **percentage_to_lock** | Group delta + stratified (games_remaining_in_week) control | controlled delta ≈ naive | naive +0.0636 → controlled -0.0002 | **CONFOUND (schedule timing)** |
| 12 | own B2B (2nd night) | bivariate | **fantasy_score** | Group delta by tier + stratified control | controlled delta ≈ naive | naive -0.439 → controlled -0.444 (holds) | Real, but already captured by existing 0.9805 multiplier's mechanism |
| 13 | home/away | bivariate | **fantasy_score** | Group delta + stratified control | controlled ≈ naive; swing vs stddev (~14.5) | naive +0.806 ≈ controlled +0.807 | Real, ~0.06 SD — too small to act on |
| 14 | own_off_rating | bivariate | **fantasy_score** (pooled) | CORR + 5-bucket quantile swing | swing > home/away's confirmed +0.806 | corr +0.0793, swing +3.29, monotonic | Passed pooled screen → #17 |
| 15 | opp_def_rating | bivariate | **fantasy_score** (pooled) | CORR + 5-bucket quantile swing | same | corr +0.0624, swing +2.48, monotonic | Passed pooled screen → #18 |
| 16 | opp_pace | bivariate | **fantasy_score** (pooled) | CORR + 5-bucket quantile swing | same | corr +0.0510, swing +1.92, monotonic | Passed pooled screen → #19 |
| 17 | own_def_rating / own_pace / opp_off_rating | bivariate | **fantasy_score** (pooled) | CORR + 5-bucket quantile swing | same | swings -1.00/-0.86/-0.83, non-monotonic or weak | Noise — not pursued |
| 18 | own_off_rating | bivariate | **fantasy_score deviation** (within-player) | CORR + 5-bucket quantile swing | swing holds near pooled +3.29 | swing collapsed to -0.257, flipped sign, non-monotonic | **CONFOUND (team composition)** |
| 19 | opp_def_rating | bivariate | **fantasy_score deviation** (within-player) | CORR + 5-bucket quantile swing | swing holds near pooled +2.48 | swing +2.19 (~88% retained), monotonic | Survived → targeted backtest #22 |
| 20 | opp_pace | bivariate | **fantasy_score deviation** (within-player) | CORR + 5-bucket quantile swing | swing holds near pooled +1.92 | swing +1.83 (~95% retained), monotonic | Survived → targeted backtest #23 |
| 21 | opp_def_rating | bivariate | **percentage_to_lock** (close-call decisions, ±3pts of lock bar) | Targeted backtest: favorable-matchup binary classifier vs 50% coinflip, n=5,518 | accuracy distinguishably > 50% (~2 SE ≈ 51.3%) | accuracy 50.4% (~0.6 SE) | **NOT SIGNIFICANT** |
| 22 | opp_pace | bivariate | **percentage_to_lock** (close-call decisions, ±3pts of lock bar) | Same targeted backtest | same | accuracy 51.0% (~1.5 SE) | **NOT SIGNIFICANT** |

**Net result:** two metrics (opp_def_rating, opp_pace) survived every confound check thrown at them — schedule position, team composition — and still failed to produce a statistically distinguishable edge at the level of the actual close-call decisions. Nothing from this pass clears the bar for wiring into `percentage_to_lock` or `lock_calculator`. Team-level side of v2.0 considered exhaustively closed as of 8/12/26.
