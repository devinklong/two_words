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
