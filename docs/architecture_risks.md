# Architecture Pressure Points (identified 8/15/26)

Separate from `docs/patch_list.md`. That doc is style/consistency/
organization — none of it changes what the pipeline can produce. This
doc is the opposite: these are places where a future change, or an
already-live gap, could cause a *silent wrong result* — one that looks
plausible and goes undetected, the same failure shape that made the
Step 6 Sleeper API instability take six theories to even characterize.

Two items already resolved during the same reconciliation session are
NOT listed here (both were false alarms, corrected and closed):
`lock_decision_input.py`'s fallback path (was never actually broken —
misdiagnosed as unrelated to a deployment gap that was already fixed),
and a docstring stat-line typo in the same file (Jokić ground-truth
example had the wrong oreb/dreb split; the real backfilled data was
always correct).

## 1. `lock_bar` formula duplication is a correctness risk, not style

`GREATEST(35, avg + 0.5*stddev)` is hand-copied across ~10 files (see
`patch_list.md` #1 for the full list). The risk isn't that it looks
messy — it's that a future formula tune (a new grid-search result, a
recalibrated multiplier) that misses even one of those ~10 copies
causes the backtest numbers and the live decision engine to silently
diverge. Nothing currently detects that drift. Highest-leverage fix on
either list, because the failure mode is invisible until someone goes
looking for it.

## 2. `sleeper_transactions` has never been diff-tested

The whole Step 6 investigation started because `sleeper_matchups` was
assumed reliable and turned out not to be — a from-scratch verification
script found 195 mismatches between two live pulls of the same
historical data. Nobody has run the equivalent check against
`sleeper_transactions`. It's currently just assumed reliable, which is
exactly the assumption that broke matchups. See `patch_list.md` #6b for
the proposed test.

## 3. Sleeper's JSONB payloads are trusted structurally, never schema-validated

`adds`/`drops`, `scoring_settings`, `players_points` are all ingested
as JSONB with no validation that Sleeper's shape hasn't changed. This
has already caused two real bugs: the `fga` float-precision artifact in
`scoring_settings` (a raw value of `-.44999...7907104` instead of clean
`-0.45`), and the `jsonb_typeof` bug in transactions (a JSON `null` is
not the same as SQL `NULL`, and the original filter didn't account for
that). A third instance — Sleeper renaming or retyping a key — would
fail the same way: silently, discovered only when someone happens to
inspect that specific field.

## 4. CASCADE-drop rebuild chains are entirely manual

`percentage_to_lock.sql` and the `game_fantasy_scores_weekly_effective`
table migration both carry explicit header warnings along the lines of
"you MUST rerun X after this or things go stale." That's correct
guidance, but it's enforced only by a comment a human has to remember
to read and follow — not by any tooling. A future edit to either root
object, without the exact correct rebuild order, leaves 2-4 downstream
views silently stale. Same underlying gap as `patch_list.md` #4
(deployment tracking), called out here specifically because the risk is
architectural, not just "nice to track."

## 5. `sleeper_matchup_points_latest` — existence unconfirmed

`historical_matchup_results` depends on this view directly. It was
never sent across any batch during the full schema reconciliation. If
it doesn't exist in the live DB, `historical_matchup_results` and
`historical_standings` fail outright, not just return stale data.
Quick check: `\dv sleeper_matchup_points_latest`. Same item as
`patch_list.md` #6a — listed here too since it's a structural gap, not
just a missing test.

## Lower-severity, worth knowing, not urgent

- **No all-or-nothing transactions in most ingestion scripts.** Loops
  in `backfill_sleeper_league.py` and similar files `commit()` after
  each item rather than wrapping the whole run in one transaction. A
  script interrupted mid-run leaves a plausible-looking but partially
  synced DB state, with nothing flagging that the run didn't finish.
- **Crosswalk is name-only, no collision handling.** Nothing in
  `sleeper_player_crosswalk`'s matching logic accounts for two
  different players sharing an identical normalized name, or a
  player's registered name changing mid-career. Hasn't happened yet;
  nothing structurally prevents it as the crosswalk grows across more
  seasons and draft classes.
- **Season/week-count constants scattered, not centralized.**
  `MAX_WEEK = 24`, the `('22021','22022','22023')` train split, and the
  `('22024','22025')` validate split are hardcoded as literal tuples
  across several grid-search files rather than defined once. Same
  duplication pattern as item #1 above, lower stakes since it only
  matters if the league format itself changes (different playoff
  structure, a 6th backfilled season).
