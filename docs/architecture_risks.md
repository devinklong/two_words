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

**Status: 11 of 11 items DONE.**

## 1. `lock_bar` formula duplication is a correctness risk, not style - DONE 8/16/26

**Fix:** Centralized into `models/lock_bar_function.sql`'s `lock_bar()`
across all 9 real duplication sites; verified unchanged (exact
LOCK/HOLD/PASS split, exact Jokić `lock_bar`, exact backtest edge).

`GREATEST(35, avg + 0.5*stddev)` is hand-copied across ~10 files (see
`patch_list.md` #1 for the full list). The risk isn't that it looks
messy — it's that a future formula tune (a new grid-search result, a
recalibrated multiplier) that misses even one of those ~10 copies
causes the backtest numbers and the live decision engine to silently
diverge. Nothing currently detects that drift. Highest-leverage fix on
either list, because the failure mode is invisible until someone goes
looking for it.

## 2. `sleeper_transactions` has never been diff-tested - DONE 8/17/26

**Fix:** Built `verify_transactions_independently.py`, mirroring the
matchups diff-test. 0 mismatches across both real seasons — confirms
the live-API instability found in matchups is specific to Sleeper's
scoring endpoint, not systemic across their API.

The whole Step 6 investigation started because `sleeper_matchups` was
assumed reliable and turned out not to be — a from-scratch verification
script found 195 mismatches between two live pulls of the same
historical data. Nobody has run the equivalent check against
`sleeper_transactions`. It's currently just assumed reliable, which is
exactly the assumption that broke matchups. See `patch_list.md` #6b for
the proposed test.

## 3. Sleeper's JSONB payloads are trusted structurally, never schema-validated - DONE 8/17/26 (partial, by design)

**Fix:** Built `schema/analysis/validate_sleeper_jsonb_shape.sql` using
`jsonb_typeof(...)` checks instead of raw casts, so every failure
reports cleanly. Covers `scoring_settings` and `adds`/`drops` — all
checks pass clean, no shape drift. `players_points` not yet covered:
that field hasn't been manually backfilled yet, so there's nothing live
to validate against.

`adds`/`drops`, `scoring_settings`, `players_points` are all ingested
as JSONB with no validation that Sleeper's shape hasn't changed. This
has already caused two real bugs: the `fga` float-precision artifact in
`scoring_settings` (a raw value of `-.44999...7907104` instead of clean
`-0.45`), and the `jsonb_typeof` bug in transactions (a JSON `null` is
not the same as SQL `NULL`, and the original filter didn't account for
that). A third instance — Sleeper renaming or retyping a key — would
fail the same way: silently, discovered only when someone happens to
inspect that specific field.

## 4. CASCADE-drop rebuild chains are entirely manual - DONE 8/19/26

**Fix:** The real dependency graph is ONE tree, not two — confirmed
directly from Postgres's own output, not inferred from file comments:
rebuilding `game_fantasy_scores` triggers a CASCADE that names 6
dependent objects (`game_fantasy_scores_weekly`,
`player_season_fantasy_stats`, `player_tiers`,
`game_fantasy_scores_weekly_percentage_to_lock`, `ownable_player_pool`,
`game_lock_signal`). Two earlier scripts that modeled this as two
separate chains both failed in practice — each only knew about half
the tree — and one of those failures briefly took down a previously
fully-working pipeline. Replaced both with a single
`scripts/rebuild_lock_pipeline.py` covering the full, true 11-step
order in one command. Each step is verified by checking directly
whether the object it produces exists (not by trusting `psql`'s exit
code, since these files intentionally bundle core DDL with
non-fatal human-review verification queries). The final step is a real
regression check against known-good production numbers — the live
LOCK/HOLD/PASS split and Nikola Jokić's `lock_bar` on a specific date —
not just object existence, so a rebuild that runs clean but produces
silently wrong numbers would still be caught. Confirmed with a full
clean run: effective-table gap = 0, split exactly 52.7/24.2/23.0,
Jokić `lock_bar` exactly 79.49.

`percentage_to_lock.sql` and the `game_fantasy_scores_weekly_effective`
table migration both carry explicit header warnings along the lines of
"you MUST rerun X after this or things go stale." That's correct
guidance, but it's enforced only by a comment a human has to remember
to read and follow — not by any tooling. A future edit to either root
object, without the exact correct rebuild order, leaves 2-4 downstream
views silently stale. Same underlying gap as `patch_list.md` #4
(deployment tracking), called out here specifically because the risk is
architectural, not just "nice to track."

**Update 8/23/26 — this same regression check earned its keep for real.**
When item #9 below (`ownable_player_pool`) broke, `rebuild_lock_pipeline.py`'s
final check caught it immediately and correctly: Jokić's `lock_bar` came
back `None` instead of a number, and the split shifted. Exactly the
"rebuild runs clean but produces silently wrong numbers" failure mode
this check exists to catch — it worked on the first real bug it
encountered.

## 5. `sleeper_matchup_points_latest` — existence unconfirmed - DONE 8/17/26

**Fix:** Confirmed live via `\dv sleeper_matchup_points_latest` — the
view exists (it was built during Step 7 after being found genuinely
missing).

`historical_matchup_results` depends on this view directly. It was
never sent across any batch during the full schema reconciliation. If
it doesn't exist in the live DB, `historical_matchup_results` and
`historical_standings` fail outright, not just return stale data.
Same item as `patch_list.md` #6a — listed here too since it's a
structural gap, not just a missing test.

## 6. No all-or-nothing transactions in most ingestion scripts - DONE 8/20/26

**Fix:** Fixed `backfill_sleeper_league.py` and
`backfill_sleeper_points_snapshots.py` — each script's entire run (all
seasons, every step) is now one transaction: a single `commit()` after
everything succeeds, `rollback()` on any exception. Verified with a
real forced-failure test on each, not just code review: injected a
temporary exception partway through a live run (after real data was
already staged — 240 matchup rows/10 rosters/10 users for one script,
240 snapshot rows for the other), confirmed via `synced_at` timestamp
queries that **zero rows persisted** despite the mid-run work, then
removed the test and reran clean to confirm the happy path still
completes normally end to end. Other ingestion scripts with the same
per-step commit pattern (if any) are not yet audited.

Loops in `backfill_sleeper_league.py` and similar files `commit()`
after each step rather than wrapping the whole run in one transaction.
A script interrupted mid-run leaves a plausible-looking but partially
synced DB state, with nothing flagging that the run didn't finish.

## 7. Crosswalk is name-only, no collision handling - DONE 8/21/26 (tested, not currently a live risk)

**Fix:** Built `scripts/sleeper/test_crosswalk_name_collisions.py`,
reusing `normalize_name()` directly from
`build_sleeper_player_crosswalk.py` (not reimplemented) so the test
checks exactly what production matching would do — scoped to every
player with `game_logs` data before season 2026-27, the existing pool
the crosswalk actually draws from. Run live: **891 distinct players, 0
collisions** on either exact or suffix-stripped normalization. Confirms
no two players in the current dataset would be silently conflated.
Structural collision-handling logic (a tiebreaker for if one is ever
found — team, position, draft year) remains unbuilt, but is no longer
urgent given the clean result.

Nothing in `sleeper_player_crosswalk`'s matching logic accounts for two
different players sharing an identical normalized name, or a player's
registered name changing mid-career. As of 8/21/26 this has been tested
directly against the live dataset and confirmed not to be occurring —
kept open as a design gap (no handling exists if it ever does happen),
not as an active bug.

**Superseded 8/23/26 — see item #11.** The "0 collisions" result above
was correct for *exact-name* collisions, but a real, different bug in
the same matching logic (Jr./Sr. suffix handling) went undetected by
this exact test and caused 4 real silent mismatches. The two bugs are
related but distinct — this item's fix and #11's fix are both needed,
not redundant.

## 8. Season/week-count constants scattered, not centralized - DONE 8/22/26

**Fix:** Created `scripts/constants.py` as the single source for all 3
scattered constants, replacing every local redefinition with an
import — a pure refactor, no literal values changed. Full grep-confirmed
file list, 9 files patched:
- `TRAIN_SEASONS`/`VALIDATE_SEASONS`: `grid_search_injury_penalty.py`,
  `grid_search_lock_decision.py`, `grid_search_lock_threshold.py`,
  `validate_lock_decision.py`, `validate_lock_threshold.py`
- `REPLACEMENT_LEVEL = 30`: `grid_search_lock_decision.py`,
  `grid_search_lock_threshold.py`, `validate_lock_decision.py`,
  `validate_lock_threshold.py`
- `MAX_WEEK = 24`: `verify_transactions_independently.py`,
  `verify_matchup_points_independently.py`,
  `scripts/sleeper/backfill_sleeper_league.py`,
  `scripts/sleeper/backfill_sleeper_points_snapshots.py`

`REPLACEMENT_LEVEL` stays a fixed 30, not derived — with only 2 seasons
of real league history there isn't enough data to model it empirically.
Kept parameterized (passed as an arg, same pattern as `lock_bar()`'s
floor/mult) so it can be revisited once more seasons of data exist,
without another multi-file hunt.

All 9 files individually reran clean post-patch, confirming the
refactor changed nothing behaviorally:
- `grid_search_lock_decision.py`: floor=35, mult=0.5, train edge=+1.712
  — matches the historical +1.71/+1.72 train/validate range exactly.
- `verify_transactions_independently.py`: 0 mismatches across all 3
  league_ids (2024-25: 538, 2025-26: 902, 2026-27: 51 transactions).
- `scripts/sleeper/backfill_sleeper_league.py`: full 3-season run
  matched the same counts exactly, committed cleanly as one
  all-or-nothing transaction.
- `scripts/sleeper/backfill_sleeper_points_snapshots.py`: 240/240
  roster/week points staged both seasons, clean commit.
- `verify_matchup_points_independently.py`: see below — surfaced and
  fixed a real bug along the way, then reran clean at 0/240 mismatches
  on both leagues.

**Bonus fix surfaced during verification, unrelated to the constants
refactor itself:** `verify_matchup_points_independently.py`'s
mismatch check compared Postgres's `Decimal`-typed stored points
directly against a JSON-sourced Python `float` with `==`, which
fails even for genuinely identical values (`Decimal('393.3') ==
393.3` is `False` due to float binary imprecision). This was
producing large numbers of false-positive mismatches — initially
misread as a re-confirmation of the known Step 6 Sleeper matchup-
points API instability, since the false-positive count (230/240 on
the 2025-26 league) looked consistent with that prior finding. Fixed
by rounding both sides to 2 decimals before comparing. Rerun after
the fix: **0 real mismatches on either completed season** — the live
matchup-points endpoint is behaving reliably right now for both
2024-25 and 2025-26, and essentially 100% of the previously-reported
mismatches (in both leagues, not just one) were the comparison bug,
not real data disagreement or API flakiness.

`MAX_WEEK = 24`, the `('22021','22022','22023')` train split, and the
`('22024','22025')` validate split were hardcoded as literal tuples
across several grid-search files rather than defined once. Same
duplication pattern as item #1 above, lower stakes since it only
mattered if the league format itself changed (different playoff
structure, a 6th backfilled season).

Two related, smaller items surfaced but deliberately left out of this
patch, not yet resolved:
- `schema/views/matchups_view.sql` line 36 has a stale-risk comment
  referencing `MAX_WEEK` (the comment text, not the constant itself) —
  may be worth updating to point at the new module.
- `schema/views/playoff_bracket_results.sql` hardcodes `week = 24`
  four times as the specific championship week — a different use of
  the number than the other files' loop bound (it's a fixed playoff
  slot, not an iteration limit). Undecided whether it belongs in the
  same centralized module or should stay a deliberate literal.
- `weekly_outcome_simulation.sql`, referenced by several scripts'
  docstrings and in patch #1's deploy chain as a real deployed file,
  is genuinely missing from disk. Unresolved — may be a deliberately
  retired/renamed file (like `grid_search_lock_threshold.py` turned
  out to be) rather than truly lost, but not yet confirmed either way.

## 9. `ownable_player_pool`'s season-bootstrap redesign silently dropped every historical season - DONE 8/23/26

**Fix:** Historical seasons (`season_id != current_season_config`) now
read `player_season_fantasy_stats` directly again, completely
unaffected by any bootstrap logic — restored to exactly how this view
worked before the bootstrap redesign. The rolling-window fallback is
now correctly scoped to apply ONLY within the current live season, on
top of the restored historical coverage, not instead of it.

The 8/22/26 season-bootstrap fix (adding a rolling-last-20-games
fallback so the pool isn't empty at the start of a new season) had a
real bug: its `current_season_stats` CTE filtered to ONLY the live
`current_season_config` season, and `rolling_stats` similarly only
ever computed the live bootstrap window — meaning every OTHER season
silently got ZERO rows in the view. This collapsed `game_lock_signal`
(which joins `ownable_player_pool`) from ~122,573 rows down to ~9,509
— roughly one season's worth of data total across the entire 5-season
history. Caught by `rebuild_lock_pipeline.py`'s own regression check
(item #4 above) — Jokić's `lock_bar` on a known date returned `None`
instead of a number. Not caught by the original bootstrap fix's own
verification, which only checked total pool size and the rolling/
season ratio for the current season, never whether OTHER seasons still
had any rows at all — a real gap in what "verified" meant for that
change. New verification query added (`GROUP BY season_id`, checking
every season has real rows) that would have caught this the first
time.

## 10. `sleeper_matchup_points_snapshots` can silently drift stale for a completed season - DONE 8/23/26 (fixed this instance, not proactively monitored)

**Fix:** A full per-row comparison (`scripts/verify_team_scores_against_xlsx.py`,
built 8/23/26) against the manually-verified `2024_2025_all_scores.xlsx`
found 166/240 2025-26 rows mismatched — the stored DB value differed
from the real, correct value. Root cause: Step 6's original
verification (see `v3_roadmap_sleeper_integration.md`) was an
AGGREGATE match only (season-total wins/losses/PF/PA) — never a true
per-row audit of all 480 entries, so individual errors could exist
while still balancing out at the season-total level. A fresh full-
season re-sync (`sync_matchup_points_snapshot()`, called directly for
all 24 weeks) self-corrected most of it (166 → 23), confirming most of
the drift was simply stale data a fresh pull resolves cleanly for a
completed season — not a data-entry error and not ongoing live-API
instability. The remaining 23 (3 pre-existing anomalies + 20 in
2025-26 weeks 21-22, week 21 100% mismatched across all 10 rosters)
needed real hand-verification against the app; all confirmed and
corrected via `schema/fixes/team_scores_manual_fix.sql`. Final state:
**0 mismatches across both completed seasons.**

This is a real, still-open structural risk, not fully closed by this
one fix: `sleeper_matchup_points_snapshots` is append-only and change-
detecting, but nothing currently re-syncs a completed season
periodically or flags when its "latest" snapshot has silently gone
stale relative to reality. The 2025-26 weeks-21-22 cluster is
plausibly a fresh instance of the same never-fully-root-caused Sleeper
live-API instability from Step 6 (6 tested theories, no confirmed
cause) — recurring at the regular-season/playoff transition boundary
specifically, not season-wide. No proactive monitoring exists for this
happening again in a future season.

## 11. `sleeper_player_crosswalk`'s exact-name matching mishandles Jr./Sr. suffixes - DONE 8/23/26

**Fix:** `build_sleeper_player_crosswalk.py`'s suffix-stripped fallback
tier (`resolve_suffix_stripped_match()`) now checks whether a candidate
match's own `players` row carries a conflicting suffix before
auto-accepting it, instead of blindly trusting any single candidate.
Real conflicts route to a new `suffix_conflicts` print bucket for
manual review instead of silently guessing. Confirmed fixed:
0 suffix conflicts on rerun.

Found via the same full player_scores audit as item #10: 4 sleeper_ids
(Jaren Jackson Jr., Jabari Smith Jr., Kevin Porter Jr., Orlando
Robinson) were silently mapped to the wrong `nba_player_id` — for 3 of
the 4, `nba_api` has two candidate rows (an older non-suffixed
namesake + the real Jr.) and exact-name matching grabbed the wrong
one; Orlando Robinson only has one candidate row on file (missing his
real-life "Jr." suffix), so there was no wrong row to grab, but the
same underlying suffix-blindness meant no match was made at all. Same
class of gap as item #7's collision-handling logic, but a genuinely
different bug — #7's own collision test (891 players, 0 collisions)
did not catch this, since these 4 aren't *exact*-name collisions, they
only collide after suffix-stripping. Both fixes are needed; neither
supersedes the other. All 4 crosswalk rows corrected via direct
`UPDATE`, confirmed against `players` and reflected in the final
player_scores verification (69 → 2 remaining "no games" rows).
