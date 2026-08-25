-- schema/views/locked_scores_by_slot.sql
--
-- Team-side counterpart to player_scores_by_position_tier: one row per
-- REAL locked decision (roster, week, starting slot), not per raw
-- per-game distribution. Answers "which slot produced the highest
-- locked score," evaluated across the league's own 2 real seasons of
-- history (2024-25, 2025-26) -- fantasy rosters, not NBA teams.
--
-- Joins player_scores (the real locked score) to its real slot via the
-- same starters[]/roster_positions[] alignment already confirmed sound
-- in check_starters_roster_positions_alignment.sql (90.83% exact
-- match, remaining gap explained by known position-eligibility drift,
-- not a real indexing problem).
--
-- Only 2 real seasons of history exist here (vs. 5 for the player-side
-- work) -- sample size is genuinely small (~10 rosters x ~20 weeks x
-- ~9 slots per season), so this view's output should be treated with
-- more caution than the player-side analysis, not equal confidence.

CREATE OR REPLACE VIEW locked_scores_by_slot AS
WITH starter_slots AS (
    SELECT
        ps.league_id, ps.week, ps.roster_id, ps.sleeper_player_id, ps.points, s.ord AS slot_index
    FROM player_scores ps
    JOIN sleeper_matchups m
        ON m.league_id = ps.league_id AND m.week = ps.week AND m.roster_id = ps.roster_id
    CROSS JOIN LATERAL unnest(m.starters) WITH ORDINALITY AS s(sleeper_player_id, ord)
    WHERE s.sleeper_player_id = ps.sleeper_player_id
),
slot_labels AS (
    SELECT l.league_id, p.ord AS slot_index, p.position_label
    FROM sleeper_leagues l
    CROSS JOIN LATERAL unnest(l.roster_positions) WITH ORDINALITY AS p(position_label, ord)
    WHERE p.position_label != 'BN'
)
SELECT
    ss.league_id,
    l.season,
    ss.week,
    ss.roster_id,
    ss.slot_index,
    sl.position_label AS slot,
    ss.sleeper_player_id,
    spc.nba_player_id,
    pt.tier,
    ss.points
FROM starter_slots ss
JOIN sleeper_leagues l ON l.league_id = ss.league_id
JOIN slot_labels sl ON sl.league_id = ss.league_id AND sl.slot_index = ss.slot_index
LEFT JOIN sleeper_player_crosswalk spc ON spc.sleeper_player_id = ss.sleeper_player_id
LEFT JOIN player_tiers pt ON pt.player_id = spc.nba_player_id AND pt.season_id = ('2' || l.season);
