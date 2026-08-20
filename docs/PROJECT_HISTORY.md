# two_words — Project History

One clean narrative of how this project got to its current state.
Where a topic has its own detailed writeup elsewhere, this doc
summarizes and points there rather than re-explaining it — several
topics (especially the Step 6 Sleeper API instability) were previously
explained at length in three or four different docs.

## v1.0-1.2 — Core lock/hold engine (complete 8/11/26)

Built the fantasy scoring formula and verified it exactly against a
real Sleeper score (Jokić vs. PHX, 3/7/25 — 113.10). Two design
iterations before landing on the final logic: a league-relative
absolute bar was tried and rejected (let high-average stars auto-LOCK
on ordinary games), settling on the current fully self-relative rule —
`LOCK` if `fantasy_score >= GREATEST(35, player's own mean +
0.5*stddev)`. The ownable player pool uses the same ceiling logic
(`mean + 1.25*stddev >= 35`) rather than raw average, since average-only
selection misses streamer-type spike players. A tier-based hold-value
curve (saturating exponential, not linear) estimates whether a later
game that week is likely to beat the current one. Calibrated via a
proper train/validate backtest — `floor=35`, `ceiling_multiplier=0.5`,
edge +1.71/+1.72 over naive.

Two real corrections were tested here: back-to-back fatigue (confirmed
real, wired in as a fractional discount) and an injury-return penalty
(built, tested, and correctly rejected — it scored below a coinflip on
a targeted backtest, a real negative result from the process working as
intended). Daily `nba_api` ingestion was automated, including migrating
off two endpoints that turned out to silently return no real 2025-26
data. `game_fantasy_scores_weekly_effective` moved from a full-rebuild
materialized view to an incrementally-synced real table.

## v2.0 — Team-level data (closed 8/12/26, exhaustively negative)

Tested whether team-level signals (pace, offensive/defensive rating,
home/away, B2B) added anything beyond the player-level formula. Every
candidate either failed significance outright or looked real at a
pooled level and then collapsed once confounds — schedule position,
team composition — were controlled for. Two metrics survived every
confound check and still failed a targeted backtest against real
close-call decisions. Nothing cleared the bar. Full 22-test log in
`v2_roadmap_section.md` — not repeated here since that log is the
primary record, not background context.

## v3.0 — Sleeper league integration (closed 8/15/26)

Full Sleeper API integration: raw ingestion (leagues, rosters, users,
matchups, transactions), a player-ID crosswalk (Sleeper's IDs are a
different scheme from `nba_api`'s), scoring-settings-as-config, and
roster/ownership tracking. Two tools shipped on top of this:
`opponent_scout.py` (rank an opponent's roster by spike threat) and
`waiver_wire_finder.py` (same lens applied to free agents).

The one real complication: this league runs Sleeper's Lock-In Mode
(one game per player per week counts, not every game), and 2025-26
weeks 1-18 didn't match the app's real recorded history when pulled via
Sleeper's public API — sometimes by large margins. Six theories were
tested; the root cause was never confirmed, including a from-scratch
independent verification script that proved Sleeper's own live endpoint
returns different values across separate calls for the same historical
week. The practical resolution was manual re-entry, hand-verified
against the app's real Schedule screens — both completed seasons now
match the real app exactly on every result. Full investigation log:
`SLEEPER_LOCKIN_METHODOLOGY.md`. Full before/after verification table:
`step6_verification_results.md`. Neither is repeated here.

A full schema relationship diagram (`schema_relationship_diagram.md`)
was published once the Sleeper side was stable, reconciled against real
files rather than assumed — this pass surfaced several real gaps
(undeployed `.sql` files that existed in the repo but not the live DB,
a missing intermediate view) that became the seed of the two backlogs
below.

## v3.1 — Corrected player_scores pipeline (declared 8/15/26, not started)

Scope: the original 1-row-per-roster-per-week plan was wrong for
Lock-In Mode — the real design needs up to 9 rows per roster per week
(one per starting slot's locked player). Declared but genuinely not
started; manual entry for this will take weeks once begun.

## Two backlogs, surfaced together, both now substantially closed

The Step 7 reconciliation pass split what it found into two lists:
`patch_list.md` (naming, duplication, deployment tracking, test
coverage — nothing that changes what the pipeline produces) and
`architecture_risks.md` (places a future change could cause a silent
wrong result). All 6 `patch_list.md` items are done. 4 of 5
`architecture_risks.md` items are done, including the most
consequential fix of the whole project: centralizing the previously
10-times-duplicated `lock_bar` formula into one real Postgres function.

The most recent architecture-risk item (CASCADE-drop rebuild chains)
surfaced something genuinely new about the project's own structure: the
entire NBA-stats side of the pipeline — from raw `game_logs` all the
way through `game_lock_signal` — is one single connected dependency
tree, not the two separate pieces it had been assumed to be. That's now
reflected in one unified rebuild script
(`scripts/rebuild_lock_pipeline.py`) instead of two, verified with a
real regression check against known-good production numbers rather than
just checking that objects exist. See `architecture_risks.md` #4 for
the full account.

## Where things stand now

Core engine: verified and stable, unchanged since v1.0-1.2's
calibration. Sleeper integration: closed, with one open root cause
(Step 6) that's practically resolved but not explained. Non-functional
backlog: closed. Architecture risks: 4 of 5 closed, one lower-severity
list remains (no all-or-nothing ingestion transactions, crosswalk
collision handling, scattered season/week constants — none urgent).
Open scope: v3.1 (not started), the v2.0 parking lot (position-based
scoring, draft-pick analysis — genuinely untried, not rejected).
