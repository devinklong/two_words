# Patch List — Pipeline & Repo Consistency

Non-functional refactor/consistency items identified during the 8/15/26
schema reconciliation session. All items below are DONE — this file is
now a closed checklist, kept for the historical record rather than as
an active plan. Original problem write-ups and verification detail
live in git history if ever needed.

1. **Centralize `lock_bar` into a real Postgres function — DONE 8/16/26.** One real function (`models/lock_bar_function.sql`), everything else calls it instead of hand-copying the formula. Verified: `weekly_outcome_simulation.sql` edge unchanged (+1.71/+1.72 train/validate).
2. **Rename `game_fantasy_scores_weekly_lock_signal` → `_percentage_to_lock` — DONE 8/17/26.** Repo-wide grep-and-rename; the old name overlapped with `game_lock_signal` (a different, downstream object) and caused a real diagramming error.
3. **Deduplicate pool/tier CTEs — DONE 8/17/26.** Files that re-derived `ranked_pool`/`tiered_pool` inline now join `player_tiers` directly instead.
4. **Deployment-tracking registry — DONE 8/17/26.** `schema_migrations` table, backfilled with the 46 already-deployed objects.
5. **Subfolder `scripts/` one level deep — DONE 8/17/26.** Split into `scripts/ingestion/`, `scripts/sleeper/`, `scripts/analysis/` — Sleeper-interdependent files kept together per their bare-import chains. Both import-fix categories (existing `.parent` → `.parents[1]`, and new `sys.path.append` blocks for files that had none) applied across all affected files.
6. **Floating tests — all DONE:**
   - **(a)** `sleeper_matchup_points_latest` existence confirmed live (`architecture_risks.md` #5).
   - **(b)** `sleeper_transactions` diff-test built and run clean, both seasons (`architecture_risks.md` #2/#6b).
   - **(c)** Crosswalk/spike-bar test suite built, 11/11 passed.
