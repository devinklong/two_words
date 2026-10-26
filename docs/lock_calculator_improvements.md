# Lock calculator: improvements, in order

Added 10/26/26. This is the work list for the lock calculator (`my_lock_report.py`, `roster_lens.py`, `game_lock_signal`). Items 1-8 come first
because the opponent-aware step (item 9) builds on them: a "should I push the bar because I am behind" rule is only as good as the
estimates of what each side can still score. Methodology and test results are logged in `lock_calculator_methodology_log.md`.

## A. Make the existing calculator trustworthy (before touching the matchup)

1. **Backtest the roster lens.** It has only a self-test and a replay of one Sunday. Replay past weeks (2024-25 and 2025-26 have real
   locked decisions in `player_scores`): for each real decision, what would the lens have said, and did following it beat what the owner did?
   Success = average banked weekly total goes up against the owner's actual locks. Nothing else below is worth tuning until this exists.
2. **Put confidence intervals on the lock-bar edge.** The floor search (35 / 0.5) shows a flat top (33-36 within 0.03). Bootstrap by
   player-season so we can say how far apart two settings must be before the difference is real.
3. **Replace normal draws with each player's own game history.** The lens assumes scores are normal, cut at 0. Check skew and tails
   (spike players are probably right-skewed); resample real games instead if the normal assumption is off.
4. **Availability model.** A player's value is points per game times games played. Jokic played 62 games last season (76%), which cut the
   Jokic trade from +28 to about +17 per game. Estimate expected games played and feed it to the lens and to `trade_check.py`.
5. **Role and minutes changes.** The bar uses rolling averages, so it lags a role change (Pritchard after Tatum returned, Naji and
   Risacher with Flagg hurt). Add a with/without-teammate split and a minutes-trend flag, backtested like the injury-return penalty was.
6. **Slot-independence in the lens.** Other open slots claim the best players first, but the same player can still matter for two slots
   across days. Measure how much that biases the bar against a full joint simulation.
7. **Reliable lock tracking.** Sleeper's API does not flag locks, and mid-week inference from frozen slots was wrong twice (Holmgren,
   Queen), so it is off. Until a reliable signal exists: `record_lock.py --from-sleeper` for you, week-end final locks for every team.
   Check Monday's run: 9 slots for each of 10 teams. Investigate whether the app's own requests expose locks (user would inspect them).
8. **Schedule effects inside the lens.** Back-to-back fatigue (x0.9805), rest days and games-left by day are in the bar for single players
   but not yet in the lens's day-by-day simulation.

## B. Opponent-aware bar (the planned next feature)

9. **Matchup-aware lock decisions.** Goal: maximize the chance of winning the week, not expected points. Needs, in order:
   - the opponent's current total and locked slots (Sleeper matchup points plus tracked locks for all teams, so the daily load must run first),
   - the opponent's remaining games, simulated with the same slot logic as the lens, giving a distribution of their final total,
   - your own final-total distribution given each lock/hold choice,
   - decision rule: choose the action with the higher P(my total > their total). Behind, this accepts more variance (hold for a bigger score);
     ahead, it banks. Compare it against the plain expected-value rule in a backtest on 2024-26 weeks.
   Example from 10/25: locking a 34 can be right for the player and wrong for a team that is behind late in the week.

## Not planned
- Draft-pick valuation (too little rookie history; see project notes).
- Retuning the pool rule (`avg + 1.25*sd >= 35`); it only decides who gets a signal.
