# Lock calculator: methodology and testing log

Started 10/26/26. This document records the chain of math behind the lock calculator and every test that shaped it, with results and the
decision taken. Append new entries at the bottom of each section; do not rewrite history. Work still to do is in `lock_calculator_improvements.md`.

## 1. The chain (current)

1. **Score.** Fantasy points per game from nba_api box scores, using the league's own scoring constants (`sleeper_scoring_constants`).
2. **Baseline.** Mean and standard deviation per player: current season once he has 20+ games, otherwise a rolling window of the last 20
   across two seasons. Players who changed teams in the offseason are scaled by 0.90 (mean and sd) until they have 20 games.
3. **Player lock bar.** `lock_bar = max(35, mean + 0.5*sd)`. LOCK if score >= bar; PASS if below with no games left; otherwise HOLD.
4. **Odds.** HOLD/PASS rows: `percentage_to_lock = 1 - a*(1-(1-b)^g)` by tier and effective games left g (fitted on below-bar games).
   LOCK rows: `nba_lock_win_rate`, the historical share of locks at that tier, games left and gap above the bar that were not beaten later that week.
   Games left use a back-to-back factor of 0.9805.
5. **Roster lens** (`roster_lens.py`). For each open slot: take your other unlocked, eligible players and their remaining games; fill the other
   open slots first (scarcest first, best player each); simulate the remaining days from each player's mean and sd (normal, cut at 0);
   working back from the last day, `V = E[max(best draw that day, V_next)]` gives the slot's bar. Today's scores are then matched to open
   slots to maximize margin over the bar (assignment problem): LOCK, HOLD, or TAKEN (a better score fills the slot).
6. **Not yet built:** opponent-aware bar (improvements list, item 9).

## 2. Test log

| Date | Test | Result | Decision |
|---|---|---|---|
| 8/12/26 | Team-level inputs (pace, ratings, home/away, B2B) in 22 tests | None survived confound control | v2.0 closed; nothing wired in |
| 8/2026 | Injury-return penalty | 32.5% / 42.5% correct, below coin flip | Rejected; kept as data only |
| 8/15/26 | Floor/multiplier grid search, train seasons 2021-24 | Best floor 35, mult 0.5, edge +1.712 over naive | Adopted; centralized in `lock_bar()` |
| 8/16/26 | Re-verification after centralizing `lock_bar` | Edge 1.72 on validation (2024-26), same LOCK/HOLD/PASS split | Confirmed |
| 10/20/26 | Fit of `hold_win_probability_by_tier` | Fitted on below-bar games only | Valid for HOLD/PASS rows, not LOCK rows |
| 10/22/26 | Mover discount, 4 offseasons | Movers ~0.89x vs non-movers ~0.98x of prior-season average in first 20 games | 0.90 factor on pool/advisor |
| 10/23/26 | `nba_lock_win_rate` view | Elite, 1 game left, gap bucket a-d: 0.83 / 0.87 / 0.92 / 0.99 | Used for "Lock right" on LOCK rows |
| 10/26/26 | Finer floor grid (33-37), mult 0.25/0.5/0.75 | At mult 0.5: 1.689 / 1.701 / 1.712 / 1.685 / 1.644; 0.25 within ~0.05; 0.75 worse | 35 kept; peak is flat (33-36 within 0.03) |
| 10/26/26 | Pool threshold 30 / 35 / 40, floors 30 / 35 / 40 | Floor 35 best in every pool (edges 1.25 / 1.71 / 2.45; pool sizes 659 / 476 / 319) | Floor not an artifact of the pool rule |
| 10/25/26 | Roster lens self-test and replay of 10/24 | 8 scores for 6 slots: best 6 LOCK, 2 TAKEN | Allocation fix shipped |
| 10/27/26 | Week 1 matchup totals: Sleeper API vs app (final) | 9 of 10 teams differed; one team's total moved 389.75 -> 395.95 -> 353.7 within a day after the week closed | API matchup points are not a final record; app and `fpts` are |
| 10/27/26 | Sleeper season total `fpts` vs app, week 1 | 9 of 10 exact to the cent (roster 4: 360.30 vs app 358.30) | `fpts` used as the final week total |
| 10/27/26 | Frozen-slot rule on rosters 7 and 8 (known app answers) | Without a best-game gate 2 of 4 detections wrong (API lag); with gate + never-swapped-out rule 17 of 18 slots right | Gate and swap rule kept |
| 10/27/26 | Week-end total check (nine slots must add to `fpts`) on rosters 7, 8 | Unique combination = the app's nine locked games, both teams | Used as the week-end step |
| 10/28/26 | Technical/flagrant scan: 0 of 43 games this season had been scanned | After scan + stored-score refresh (50 rows lowered): rosters 5-9 each have one exact combination; 2.00 offsets gone | Scan + refresh added to `run_daily.py` |
| 10/29/26 | Game-log audit coverage | Audit covered 3 of 81 games: `team_schedule` results were blank, so most games were skipped | Loaders changed from insert-once to upsert with a change log; audit now 81 of 81 |
| 10/30/26 | Week 1 week-end solve on the values Sleeper itself reported after the week closed | Exactly one combination per team for 10 of 10 rosters; game-score checksum alone had picked wrong games for rosters 1, 3, 4, 5 by coincidence | Observed-values method is the primary week-end solve; checksum is the fallback |
| 10/30/26 | Totals vs NBA corrections | Two rosters' Sleeper totals are lower or higher by the exact amount of a later NBA stat correction (Daniels -2.00; Clingan -2.00 and Reed +3.00) | Database follows the official NBA box score; the gap is stored as `explained_diff` and shown as explained |
| 10/31/26 | Sleeper GraphQL `matchup_legs_raw.starters_games` vs the 64 week 1 locks proven by checksum | 64 agree, 0 disagree, 0 missing; all 26 unflagged slots equal the player's last game of the week | Flag adopted as the in-week lock signal (`sleeper_flag`); unflagged = auto last game |
| 10/31/26 | App screenshots, all 10 week 1 rosters (90 slots) vs `nba_lock_decisions` | 87 exact to the cent; 3 differ by the corrections above; app shows a bold white score for a manual lock, gray for the auto-banked last game | Week 1 locks accepted as proven |

## 3. Incidents and what they taught

- **10/23: frozen-slot lock inference (Holmgren).** Sleeper showed a stale first-game score (37.9) in an unlocked slot; rule read it as a lock.
  Raised the rule to "unchanged 2+ days after a later game".
- **10/24: same rule wrote Queen as locked in UTIL.** Mid-week inference turned off by default (`--include-frozen`); only week-end finals are written.
  Lesson: Sleeper's slot points cannot distinguish a locked slot from an unlocked one.
- **10/24: lock report showed players picked up after the game.** Fixed by excluding players added by a waiver claim placed on or after the game's day
  (waivers process overnight), or a later trade. Tested on made-up cases.
- **10/25: roster lens called LOCK on every score** when no one had games left. Fixed by assigning scores to open slots.
- **10/26-28: Sleeper matchup points drift.** After week 1 closed, the matchups endpoint still changed slot values between pulls, and 9 of 10 team totals
  did not match the app's final scores. Lagging unlocked slots look exactly like locks for 2+ days (George, LeBron). The endpoint has no lock flag, and a lock on
  the game just played changes nothing in it, so a lock is only visible after the player's next game. Rules now: best game so far, never swapped out of the
  slot, decided once; week-end check against `fpts`. The 'infer from final API state' rule (`sleeper_final`) was removed.
- **10/28: technical fouls never scanned for the new season.** `run_daily.py` did not call the scan, and the weekly-effective table never updates stored rows,
  so 2.00 gaps appeared (Haliburton, Edwards, Jerome). Fixed with a daily scan and a refresh step.
- **10/28-30: loaders froze early data.** `load_team_schedule` and `load_game_logs` used insert-once, so a box score pulled mid-game (first five minutes) or
  a result the NBA corrected a day later never changed. Now both upsert, re-pull a rolling window (3 days; 10 on Monday mornings), and write every changed
  value to `game_log_changes`. `log_score_corrections.py` turns those into before/after fantasy scores in `game_score_corrections`.
- **10/29-30: stats.nba.com read timeouts** killed range loads (8 games on 10/28). Loader retries with 5/10/20 s backoff, 60 s timeout, 0.6 s between games, and
  continues past a failed day (exit code 1 at the end). A game that fails on a rookie missing from `nba_players` is healed by `add_player_by_id.py` and reloaded.
- **10/30: Sleeper freezes the week's total; the NBA corrects later.** Sleeper's final week total can hold a pre-correction score (Daniels, Clingan, Reed).
  Treated as two numbers: NBA official stats are the database truth, Sleeper's final is a frozen record, and each difference must be explained by a logged
  correction (`explained_diff`) or it stays red.
- **10/30: emptied slot and settled weeks.** A starter slot set to 0 in the latest snapshot dropped rosters 3 and 5 out of the week-end solve (now carried
  forward), and weeks already marked settled were never rechecked (now re-derived for 7 days after the week ends).
- **10/31: Sleeper does expose locks.** The REST matchups endpoint has no lock flag, but Sleeper's GraphQL `matchup_legs_raw(round, league_id)` answers
  without a login and returns `starters_games`, a map of player to game for every starter the manager explicitly locked. Unlisted starters bank their last game
  at week end. `scores(date, sport, season, season_type)` gives each game's date and teams, which is how Sleeper game ids are matched to NBA game ids.
  `matchup_legs` (without `_raw`) needs a login and is not used.

## 4. Known assumptions and limits

- Scores are modeled as normal draws; not yet tested against real tails.
- No injuries, minutes or role changes (the bar lags a role change by a few games).
- The lock-bar edge is per player-week against a naive rule and an oracle, with replacement level fixed at 30 (user decision); no confidence intervals yet.
- Open slots depend on recorded locks; unrecorded locks make the lens wrong.
- The roster lens has not been backtested.
- A lock is detectable only after the player's next game (no lock flag in the public matchups endpoint); the app shows padlocks, source unknown.
- Week-end total check: with 4-5 games per player, several combinations can add up to the same total; rosters 1, 2, 3, 4, 10 in week 1 are undetermined
  until checked against the app. A 'Double Technical' second player ('Portis Jr.', game 0022600104) could not be resolved by the scan.

Updates 10/31/26 (these supersede the two week-end lines above):
- A lock is now visible the hour it is made: `ingest_lock_flags.py` reads Sleeper's own flag every run and `infer_lock_decisions.py` records it as `sleeper_flag`
  (only a manual entry can override it). The statistical in-week inference is no longer needed and stays off.
- The week-end solve no longer guesses between combinations: flagged slots are fixed and the rest are solved from the values Sleeper reported. Week 1: 10 of 10 proven.
- Still open: Portis Jr. 'Double Technical' (game 0022600104) is unresolved by the scan; one NBA minutes total (Indiana) is off in the NBA's own box score
  (soft check, no fantasy effect); corrections the NBA makes more than 10 days after a game are not picked up; the flag and score pulls depend on the Mac
  being awake (see the runbook); the flag's first_seen_at is the first run that saw it, not the exact lock time.
