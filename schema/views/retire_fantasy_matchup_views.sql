-- Retired 8/14/26: these existed to reconstruct aggregate weekly team
-- scores from this project's own per-game fantasy_score formula, for
-- "real league standings." No longer needed -- historical_matchup_results
-- (built off sleeper_matchup_points_snapshots, Sleeper's own recorded
-- points) does that job correctly using real numbers. The future
-- weekly-matchups lock/hold tool evaluates every rostered player
-- individually against game_fantasy_scores_weekly_effective
-- (roster_ownership + the existing independent lock signal) -- it was
-- never going to need an aggregate team score from these views either.
--
-- fantasy_matchup_points also had a separate, unrelated, unfixable
-- limitation even setting aside standings: it summed every game a
-- "currently synced" starter played that week rather than only the
-- day(s) they were actually locked in -- sleeper_matchups only ever
-- stores one overwritten snapshot per week, never true daily history.

DROP VIEW IF EXISTS fantasy_matchup_results CASCADE;
DROP VIEW IF EXISTS fantasy_matchup_team_totals CASCADE;
DROP VIEW IF EXISTS fantasy_matchup_points CASCADE;
