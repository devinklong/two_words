-- nba_player_tiers_in_use: the tiers the lock chain should use for each season.
-- Added 10/21/26; rewritten 10/21/26 (first version timed out: correlated
-- subqueries re-ran the player_tiers ranking once per row).
-- player_tiers ranks a season from player_season_fantasy_stats (players with
-- >= 20 games), so it has NO rows for a season in progress. The lock chain
-- (game_fantasy_scores_weekly_percentage_to_lock -> game_lock_signal)
-- inner-joined player_tiers and returned 0 rows for the whole 2026-27 opener.
-- This view fills the gap without changing any finished season.
--
-- Rule: a season uses its own tiers, unless it is the current season
-- (current_season_config id = 1) AND fewer than 100 players have been ranked
-- so far; then it borrows the most recent earlier season's tiers.
-- Why a minimum and not "any rows": a handful of players reach 20 games first
-- (mid-November), and ranking them only against each other would label the 5
-- best-so-far '1_elite' and drop everyone else. 100 is an ASSUMPTION (finished
-- seasons rank 155-169); change MIN_RANKED (the two "< 100" below) to move the cutover.
--
-- tier_source_season_id shows which season the tier actually came from.
-- player_tiers is read ONCE (MATERIALIZED) because it is an expensive window query.
-- Safe to re-run: CREATE OR REPLACE with the same columns/types.
CREATE OR REPLACE VIEW nba_player_tiers_in_use AS
WITH tiers AS MATERIALIZED (
    SELECT player_id, season_id, avg_fantasy_score, stddev_fantasy_score, rank_in_season, tier
    FROM player_tiers
),
own_counts AS (
    SELECT season_id, COUNT(*) AS n_ranked FROM tiers GROUP BY season_id
),
-- zero or one row: the current season, only if it is not yet ranked enough
borrow AS (
    SELECT c.season_id::varchar AS target_season_id,
           (SELECT MAX(oc.season_id) FROM own_counts oc WHERE oc.season_id < c.season_id) AS source_season_id
    FROM current_season_config c
    WHERE c.id = 1
      AND COALESCE((SELECT oc.n_ranked FROM own_counts oc WHERE oc.season_id = c.season_id), 0) < 100
)
-- own tiers for every season except one that is borrowing
SELECT t.player_id, t.season_id, t.avg_fantasy_score, t.stddev_fantasy_score,
       t.rank_in_season, t.tier, t.season_id AS tier_source_season_id
FROM tiers t
WHERE t.season_id NOT IN (SELECT target_season_id FROM borrow)
UNION ALL
-- the borrowing season gets the source season's tiers
SELECT t.player_id, b.target_season_id, t.avg_fantasy_score, t.stddev_fantasy_score,
       t.rank_in_season, t.tier, t.season_id AS tier_source_season_id
FROM borrow b
JOIN tiers t ON t.season_id = b.source_season_id;
