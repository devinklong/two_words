# Patch List — Pipeline & Repo Consistency (identified 8/15/26)

Non-functional refactor/consistency items surfaced during the full schema
reconciliation session (relationship diagram build, ~14 file batches).
None of these change what the pipeline can *do* — they're about naming,
duplication, deployment tracking, and test coverage. Real functional gaps
(Step 6, `sleeper_matchup_points_latest`'s unconfirmed existence, the
`historical_matchup_results` architecture-rule conflict) are tracked
separately, not here.

No version bump warranted for any of these — the project hasn't used
patch-level version numbers anywhere; each folds into the current
version's ongoing commit history, same as `fix_team_schedule_pk.sql`.

## 1. Centralize `lock_bar` into a real Postgres function - DONE 8/16/26

`GREATEST(35, avg + 0.5*stddev)` is hand-copied across ~10 files
(`lock_decision_input.py`, `fit_hold_value_curve_by_tier.py`,
`grid_search_lock_decision.py`, `validate_lock_decision.py`,
`grid_search_lock_threshold.py`, `validate_lock_threshold.py`,
`grid_search_injury_penalty.py`, `injury_penalty_targeted_check.sql`,
`analyze_ceiling_penalty_by_tier.py`, `hold_value_by_tier_and_grw.sql`),
each carrying a "MUST match game_lock_signal.sql exactly" comment.
Mirror `hold_win_probability_by_tier()`'s pattern: one real function,
everything else calls it.

**Verification required:** no behavior change intended — after the
patch, rerun `weekly_outcome_simulation.sql` and confirm the edge is
still exactly +1.71/+1.72 train/validate before calling it done.

## 2. Rename `game_fantasy_scores_weekly_lock_signal` - DONE 8/17/26

Renamed `game_fantasy_scores_weekly_lock_signal` → `game_fantasy_scores_weekly_percentage_to_lock`

It doesn't compute a lock signal — that's `game_lock_signal`, one layer
downstream. It computes `percentage_to_lock`. The name overlap caused a
real diagramming error this session. Pure rename; referenced in
`percentage_to_lock.sql`'s CASCADE chain and several diagnostic files —
one repo-wide grep-and-rename commit.

## 3. Deduplicate pool/tier CTEs - DONE 8/17/26

`hold_value_by_tier_and_grw.sql`, `analyze_ceiling_penalty_by_tier.py`,
`injury_penalty_targeted_check.sql`, `injury_penalty_null_gap_check.sql`
all re-derive `ranked_pool`/`tiered_pool` inline with fixed
k=1.25/threshold=35 instead of joining `player_tiers` directly. (The
grid-search scripts legitimately need raw CTEs since they parameterize
k/threshold — those are fine as-is, not part of this patch.)

## 4. Add a deployment-tracking registry - DONE 8/17/26

Process gap, not a code bug — nothing currently tracks which `.sql`
files have actually been applied to the live DB vs. just existing in
the repo. Caused real time loss this session (`player_tiers.sql`,
`player_season_fantasy_stats_view.sql` existed but weren't live).
Candidate: a `schema_migrations` table, or a plain `DEPLOYED.md`
checklist.

## 5. Subfolder `scripts/` one level deep (lower priority, organizational only) - DONE 8/17/26

`scripts/` has grown to 40+ flat files. Proposed split — see git
commands below. Reuses the exact import pattern `scripts/analysis/`
already proves works (`parents[1]` instead of `parent` in
`sys.path.append`), not a new convention. Stop at one level of nesting.

**Corrected 8/15/26:** `backfill_sleeper_league.py` and
`backfill_sleeper_points_snapshots.py` belong in `scripts/sleeper/`,
NOT `scripts/ingestion/` as originally proposed — `sleeper_daily_sync.py`
and `resync_matchups.py` both bare-import `backfill_sleeper_league`
directly, and `backfill_sleeper_points_snapshots.py` bare-imports
`sleeper_daily_sync` — splitting these across folders breaks same-folder
bare imports. Keep every Sleeper-interdependent file together.

**Two categories of import fix needed after moving, not one:**
- Files with an existing `sys.path.append(...).parent`: edit to
  `.parents[1]` — `backfill_sleeper_league.py`,
  `backfill_sleeper_points_snapshots.py`, `build_sleeper_player_crosswalk.py`,
  `sleeper_daily_sync.py`, `resync_matchups.py`,
  `sleeper_crosswalk_regression_testing.py`, `sleeper_daily_sync_testing.py`.
- Files with NO `sys.path.append` at all (relied on Python's default
  "script's own directory is on sys.path" behavior, which breaks one
  level deeper): need a new block added —
  `backfill_missing_players.py`, `backfill_single_player.py`,
  `backfill_team_game_stats.py`, `load_daily_game_logs.py`,
  `load_daily_team_schedule.py`, `load_game_logs.py`, `load_players.py`,
  `load_team_schedule.py`. Add, after existing imports and before the
  first local bare import:
  ```python
  import sys
  from pathlib import Path

  sys.path.append(str(Path(__file__).resolve().parents[1]))
  ```
Same-subfolder sibling imports (e.g. `backfill_single_player.py`
importing `load_game_logs`, both landing in `scripts/ingestion/`
together) need NO fix — Python auto-adds a script's own directory to
`sys.path` regardless of nesting depth; the explicit block above is
only for reaching back up to flat `scripts/`.

## 6. Floating — specific tests to build

- **(a)** Confirm `sleeper_matchup_points_latest` actually exists in the
  live DB (`\dv sleeper_matchup_points_latest`). `historical_matchup_results`
  depends on it directly and it was never confirmed across any
  reconciliation batch — if missing, that view and `historical_standings`
  would fail outright, not just return stale data.
- **(b)** Build a `sleeper_transactions` diff-test mirroring
  `verify_matchup_points_independently.py`'s approach (pull twice,
  compare). Transactions was never tested for the same live-API
  instability found in `sleeper_matchups` that drove the whole Step 6
  investigation — currently just assumed reliable, never verified.
- **(c)** Formalize the many ad hoc `-- Verification` SQL blocks already
  scattered through the schema files into a real, runnable test suite
  (pytest or a plain SQL-assertion runner). Prioritize: crosswalk edge
  cases (NULL match, zero-game player), the spike-bar one-year fallback
  boundary (Zach Edey low-games case, Khaman Maluach below-threshold
  case), and ground-truth regression checks (Jokić 113.10 fantasy_score,
  ~79.5 lock_bar) — so future refactors (especially #1 above) can be
  verified automatically instead of manually rerunning
  `weekly_outcome_simulation.sql` by hand.
