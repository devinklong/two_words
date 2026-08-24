-- schema/views/player_scores_by_position_tier.sql
--
-- v3.2 player-side analysis view. Joins existing data only -- no new
-- table, no schema change (per 8/24/26 discussion). Row-level, one row
-- per (player, game, eligible position, tier): deliberately NOT
-- pre-aggregated, since Kruskal-Wallis has to run in Python (Postgres
-- has no significance-test functions) and both the percentile table and
-- the significance test should read the exact same underlying rows.
--
-- CAVEAT (inherited from sleeper_player_fantasy_positions' own design,
-- confirmed 8/24/26): that table is current-state-only, no historical
-- tracking. Every past season's games get labeled with a player's
-- CURRENT eligibility, not necessarily what it was at the time -- a
-- known, accepted limitation given 4 real players already confirmed to
-- show position drift over time (see check_fantasy_positions_spot_check.sql).
-- This view does not attempt to correct for that; it's why the v3.2
-- framing decision treats this as descriptive, not predictive.
--
-- Multi-eligible players intentionally appear once per eligible
-- position -- e.g. a SF/SG player's games show up in both groups. This
-- is deliberate: it matches the real decision this analysis supports
-- ("which position pool would this player's score count toward"), not
-- a forced single-position assignment.
--
-- ASSUMPTION flagged for review: assumes `game_fantasy_scores_weekly_effective`
-- has `player_id`, `game_id`, `season_id`, `fantasy_score` columns, and
-- `player_tiers` has `player_id`, `season_id`, `tier` -- adjust below if
-- the real schema differs.

CREATE OR REPLACE VIEW player_scores_by_position_tier AS
SELECT
    gfs.player_id  AS nba_player_id,
    gfs.game_id,
    gfs.season_id,
    gfs.fantasy_score,
    pfp.position,
    pt.tier                                    -- LEFT JOIN: untiered players kept, not dropped
FROM game_fantasy_scores_weekly_effective gfs
JOIN sleeper_player_crosswalk spc
    ON spc.nba_player_id = gfs.player_id
JOIN sleeper_player_fantasy_positions pfp
    ON pfp.sleeper_player_id = spc.sleeper_player_id
LEFT JOIN player_tiers pt
    ON pt.player_id = gfs.player_id
   AND pt.season_id = gfs.season_id;
