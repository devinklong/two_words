## v3.0 — Sleeper league integration (CLOSED 8/15/26)

Closes the data-source split from v1.0 (`nba_api` = on-court events, Sleeper = fantasy league events, "no overlap, integrated later"). Sleeper's API is read-only, free, no auth token — endpoints below confirmed against current docs (docs.sleeper.com), not assumed.

**Relevant endpoints** (all under `https://api.sleeper.app/v1`):
- `/league/<league_id>` — league details + scoring settings
- `/league/<league_id>/rosters` — who owns which player, starters
- `/league/<league_id>/users` — league members
- `/league/<league_id>/matchups/<week>` — weekly head-to-head + points
- `/league/<league_id>/transactions/<round>` — waivers, trades, drops
- `/league/<league_id>/traded_picks` — traded draft picks
- `/players/nba` — full player directory + cross-reference IDs (large, near-static — fetch rarely, not per-sync)

**Known gotcha to plan around:** Sleeper's `player_id` is Sleeper's own scheme, not `nba_api`'s `PERSON_ID` — rosters/transactions/matchups are meaningless against `game_logs` until a crosswalk table exists. `/players/nba` includes some cross-reference IDs (espn_id, etc.) but not guaranteed to include `nba_api`'s ID directly — step 2 below needs to confirm what's actually usable before building the join, not assume a clean match.

**Roadmap — all 9 steps DONE as of 8/15/26:**
1. **DONE.** Raw ingestion tables for league settings, rosters, users, matchups, transactions — one row per API entity, 3NF, same raw-table-plus-view pattern as the rest of the project. Rosters/transactions/matchups change during the season (unlike historical `game_logs`), so these need a real sync cadence, not a one-time backfill.
2. **DONE.** Player ID crosswalk table: Sleeper `player_id` ↔ this project's player identity (whatever `game_logs`/`players` uses). Confirm what `/players/nba`'s cross-reference IDs actually cover before assuming a clean automatic match — likely needs a name/team fallback match with an ambiguous-match log, same pattern as `cleaning_logs/`.
3. **DONE.** League scoring settings as the source of truth for `fantasy_score`'s formula constants, replacing whatever's currently hardcoded — makes the formula config-driven from Sleeper's own declared settings rather than assumed.
4. **DONE.** Roster/ownership table — who owns which player, updated on sync. Unlocks the "real replacement-level data" item that's been on the parking lot since v1.0.
5. **DONE.** Transactions table — waiver adds/drops, trades. Feeds draft-pick analysis (also parking-lot since v1.0) and ownership history.
6. **DONE.** Matchups table — weekly fantasy opponent + points, distinct from the NBA opponent tracked elsewhere in this project. Needed for real league standings, not just per-player LOCK/HOLD decisions.
7. **DONE.** Once 1-6 are in place, publish a full relationship diagram (all tables, both the `nba_api` side and the Sleeper side, FK relationships) to GitHub as project documentation.
8. **DONE.** Opponent threat assessment: apply v1.0's self-relative lock-bar logic (`GREATEST(35, player's own mean + 0.5*stddev)`) to an opponent's rostered players, not just your own — surfaces whether an opponent is sitting on a game worth locking, using the same validated math against a new axis. Depends on step 4 (roster/ownership table) and step 6 (matchups, to know who's actually being faced that week).
9. **DONE.** Upside/stay-away finder: re-reads the existing tier and ownable-pool logic (`mean + 1.25*stddev >= 35`) as a scouting lens instead of an ownership filter — high-stddev-relative-to-mean players flagged as "sneaky upside" streamers, high-mean-low-stddev players flagged as capped-ceiling/stay-away. Depends on step 4 (roster/ownership table) to know who's actually available/rostered.

Rate limits/caching: Sleeper's docs recommend caching and backend-proxying rather than calling live per-request — worth building the sync scripts with that in mind from the start rather than retrofitting later.

---

## Status as of 8/14/26

Steps 1-5 done. Step 6 (matchups/standings) hit a major, still-unresolved
data-reliability issue specific to the 2025-26 season — **full
investigation log in `SLEEPER_LOCKIN_METHODOLOGY.md`**, summary below.
Steps 8/9 (which depend on step 6 for the "opponent" axis) are blocked
on that being resolved for anything requiring historical accuracy, but
NOT blocked for anything forward-looking (see architecture decision
below).

**Step 6 summary:** This league runs Sleeper's Lock-In Mode (one game
per player per week counts, not every game — confirmed against
Sleeper's own support docs). This project's first approach
(reconstructing weekly team totals from its own independently-verified
`fantasy_score` formula) was structurally wrong for this scoring
format and has been retired. The corrected approach — using Sleeper's
own recorded points directly via a new isolated, change-logged table
(`sleeper_matchup_points_snapshots`) — works and is verified exactly
correct for the 2024-25 season and for 2025-26 weeks 19-24, but
returns unverified/inconsistent values for 2025-26 weeks 1-18. Root
cause not confirmed after six tested theories, including a
from-scratch independent re-verification script that found the live
API itself returns different values across separate calls for the
same historical week. See the methodology doc for the full test log
before re-investigating from scratch.

**Architecture decision (applies going forward, not just to this
issue):** `sleeper_rosters`/`sleeper_users` are current-state-only,
no history. No view that determines a result or a label may join them
directly — split into a roster_id-pure correctness layer + a
separately-labeled current-ownership display layer. Applied to
`historical_matchup_results`, `historical_standings`, and
`transaction_players_detail`.

**Future weekly-matchups/lock tool (design decision, 8/14/26):** does
not need Sleeper-reconstructed aggregate team scores at all. Evaluates
every current rostered player (via `roster_ownership`, already
full-roster + daily-refreshed) against the existing independent
lock/hold signal (`game_fantasy_scores_weekly_effective`) — no
starter/bench distinction needed in the database, no dependency on
resolving the points-reliability issue above. The core lock/hold
formula pipeline was never affected by any of this.

---

## Status as of 8/15/26 — v3.0 CLOSED, all 9 roadmap steps done

**Step 6 — CLOSED.** Root cause of the Sleeper API instability remains
officially unconfirmed after six tested theories (see
`SLEEPER_LOCKIN_METHODOLOGY.md` for the full log — that investigation
stands as written, nothing above needs revising). The practical problem
is resolved: `team_scores`/`player_scores` were manually re-entered
from the app's real Schedule screens and hand-verified, then upserted
into `sleeper_matchup_points_snapshots` via
`scripts/sleeper/backfill_manual_team_points.py`. A full audit against
the real app's League History pages caught and fixed 6 total
discrepancies across both seasons — 2 manual-entry typos and 4 genuine,
previously-undetected errors in what had been assumed "verified exactly
correct" 2024-25 data. Both completed seasons now match the real app
exactly on wins/losses/ties/PF/PA. Full comparison table:
`docs/step6_verification_results.md`.

**Steps 8/9 — DONE**, shipped independent of step 6 exactly as the
8/14/26 design decision above anticipated:
`scripts/opponent_scout.py` (opponent roster spike-threat ranking, via
`sleeper_matchups` + `roster_ownership` + `player_tiers`) and
`scripts/waiver_wire_finder.py` (free-agent spike-threat ranking, same
underlying logic). Both auto-fall back one season if the current season
has no games yet.

**Step 7 — DONE.** `docs/schema_relationship_diagram.md` fully
reconciled against real schema files (not assumed) across the whole
project, not just the Sleeper side — surfaced and fixed several real
gaps in the process (a missing intermediate view in the core lock/hold
chain, a few undeployed `.sql` files, some stale/pre-fix file versions).

**Built beyond the original 9 steps, while closing them out:**
`schema/views/playoff_bracket_results.sql` (fixed-slot 10-team bracket
resolver — 6-team playoff + 4-team consolation, seeded off week-21
standings) and `schema/views/sleeper/roster_transaction_summary.sql`
(per-owner transaction counts by type/action). Confirmed a real,
non-obvious finding along the way: `free_agent`-type transactions are
drop-only in this league's actual data (not pickups, despite the name)
— `waiver` is the type that covers both adds and drops.

**What's left is refinement, not new scope:** a 6-item
consistency/refactor list (`docs/patch_list.md`) and a 5-item
correctness-risk list (`docs/architecture_risks.md`) — centralizing the
duplicated `lock_bar` formula is the highest-priority item on either.
Two smaller open threads, neither blocking: the `player_scores` sheet
(4,991 rows) was built but never wired into a table/upsert script the
way `team_scores` was; FAAB (waiver budget) tracking is explicitly
deferred, safely backfillable whenever wanted since Sleeper retains
full historical transaction settings data.
