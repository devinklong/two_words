# Sleeper Lock-In Mode data reliability — investigation log (8/14/26)

Two Words is a Sleeper NBA dynasty league running **Lock-In Mode**
(confirmed in-app, confirmed against Sleeper's own support docs): each
starter contributes exactly ONE game per week toward the team's
matchup score — whichever game the manager explicitly locks, or,
if unlocked by the manager, whichever game Sleeper's app auto-selects
as the "final game of the week" fallback. This is fundamentally
different from "every game a starter played that week counts," which
is what this project initially assumed.

**Status: root cause of the 2025-26 discrepancy is NOT confirmed.**
This document exists so the investigation doesn't need to be repeated,
and so a future, simpler explanation isn't missed for lack of a
written record of what's already been ruled out.

## The observation

- 2024-25 season: this project's data (both the independently-computed
  reconstruction and the later Sleeper-points-based snapshot table)
  matched the app's real historical record closely to exactly, across
  every check run (season standings, week 1 team-by-team, PF/PA).
- 2025-26 season: weeks 1-18 did NOT match the app's real record when
  queried via Sleeper's public API, sometimes by large margins (one
  case: reconstruction totaled 1065.00 vs the app's real 453.45).
  Weeks 19-24 matched exactly. Playoff-adjacent weeks and the prior
  season were unaffected.

## Theories tested, in order, with evidence

**1. API recalculates points using the CURRENT roster, applied
retroactively to past weeks.**
Ruled out. If true, 2024-25 (older, more roster turnover between then
and now) should be less accurate than 2025-26, not more. Observed the
opposite.

**2. League/season completion status differs between the two
league_ids.**
Ruled out. Both confirmed `status = 'complete'` in `sleeper_leagues`.

**3. Roster churn / transaction volume on the specific affected
roster.**
Partially suggestive, then ruled out as the general mechanism. One
real case (Jerami Grant, on the affected roster during week 1 2025-26,
cut before this project's backfill ran) fit the pattern. But a
control test — the roster with the LOWEST transaction count in the
entire league (25, vs. 189-359 for others) — was still wrong. A
roster with almost no activity failing the same way as a
heavily-traded one rules out transaction volume as the driver.

**4. IR/taxi-squad moves bypassing the visible transaction log.**
Ruled out. `sleeper_rosters.settings->'reserve'` (Sleeper's raw IR
slot field) returned NULL for every roster in the league — either
never populated or never used, either way not the mechanism.

**5. League scoring_settings changed mid-season.**
Untestable directly (this project's schema only stores current
settings, no historical snapshot), and no one in the league recalled
any such change. Not confirmed, not ruled out — deprioritized after
(6) below made it moot.

**6. A bug in this project's own ingestion/fetch code.**
Ruled out with the strongest evidence in this investigation: built
`verify_matchup_points_independently.py`, a standalone script sharing
ZERO code with the existing pipeline (separate fetch function,
separate DB query, no imports from `backfill_sleeper_league.py` or
`sleeper_daily_sync.py`). Result: **195 mismatches** between a fresh
live pull and already-stored data for the SAME season, weeks, and
rosters — pulled minutes apart, same afternoon. This proves Sleeper's
public `/league/{id}/matchups/{week}` endpoint is not returning
stable, deterministic values for at least some historical weeks in
this league, independent of anything in this project's code.

## What this does and doesn't explain

Sleeper's actual Lock-In Mode design (only one game/player/week
counts) fully explains why this project's OWN independently-computed
reconstruction was structurally wrong — it was summing every game,
not the one locked game. That's a real, understood, and now-corrected
design mismatch (see: retirement of `fantasy_matchup_points` /
`fantasy_matchup_team_totals` / `fantasy_matchup_results`).

It does NOT explain why Sleeper's own API returns unstable values for
the SAME historical week across two calls made close together. That
remains unexplained.

## Attempts to find an authoritative source

- Searched Sleeper's official support docs (`support.sleeper.com`) —
  found and confirmed real documentation of Lock-In Mode's rules
  (one game/week, manual lock, "final game of week" fallback if
  unlocked). This is genuine, verified documentation, not a guess.
- Searched Sleeper's public API docs (`docs.sleeper.app`) for an
  explicit lock-status field — none documented. However, this same
  doc source is also missing `starters_points` and `players_points`,
  two fields confirmed present in real payloads throughout this
  investigation — so its silence on a lock field is NOT proof one
  doesn't exist elsewhere in Sleeper's system.
- A separate AI session's search results (screenshotted, not
  independently verified) claimed specific "documented bugs" citing
  only generic "Reddit" tags with no real link, thread, or date.
  Treated as unverifiable and explicitly NOT used as evidence anywhere
  in this investigation or its conclusions.
- Attempted to inspect the live app's actual network traffic for an
  undocumented endpoint. Blocked: Sleeper's web app has no NBA
  matchup/team/scores panel at all ("please use the mobile app"), and
  mobile traffic inspection requires a proxy tool (Charles, mitmproxy)
  not available in this session.

## Current mitigations / architecture decisions made as a result

- `fantasy_matchup_points` / `fantasy_matchup_team_totals` /
  `fantasy_matchup_results` (independently-computed weekly totals)
  permanently retired — structurally incompatible with Lock-In
  scoring regardless of the instability issue above.
- `sleeper_matchup_points_snapshots` (isolated, change-logged table)
  + `historical_matchup_results` / `historical_standings` remain the
  intended architecture for real historical results, using Sleeper's
  own points directly rather than re-deriving them. BUT: given the
  195-mismatch finding, any value in this table for 2025-26 weeks
  1-18 should be treated as UNVERIFIED until manually cross-checked
  against the app, not assumed correct just because it came from the
  "ground truth" source.
- Project-wide architecture rule adopted: `sleeper_rosters` /
  `sleeper_users` (current-state-only, no history) must never be
  joined directly into any view whose correctness matters (results,
  standings, transaction logs). Every such view is split into a
  roster_id-pure core (correctness) + a separately-labeled
  current-ownership display layer.
- No live-tracking "which player is currently locked" feature has
  been built. No confirmed field exists to build it on with certainty;
  it would currently have to rely on an inference heuristic (a
  player's score holding steady across daily snapshots even after
  their team plays again = likely locked), which is not the same as
  a guaranteed signal.

## What would actually resolve this

- Mobile app traffic inspection via a proxy tool, specifically
  looking for the request that fires when interacting with a Lock-In
  button, or when loading a past week's Schedule screen.
- A direct support ticket to Sleeper (`support@sleeper.app`) citing
  a specific, reproducible discrepancy (e.g., "week 1 2025-26,
  roster_id 8, league {id}: API returns 304.25, app displays
  453.45") — a concrete bug report, not a guess.
- Periodically re-running `verify_matchup_points_independently.py`
  to see whether the instability is time-bound (e.g., settles once
  enough time passes after a week closes) or persistent indefinitely.
