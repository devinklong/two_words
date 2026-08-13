-- Extracts this league's Sleeper-declared scoring settings into typed,
-- named columns keyed by season_id (matches game_logs.season_id format,
-- e.g. '22024'), so game_fantasy_scores can join on season_id directly
-- instead of hardcoding point values per stat category.
--
-- All 14 mapped columns are ROUND(..., 4). Confirmed 8/13/26: fga's raw
-- JSONB value was -.44999...7907104, not a clean -0.45 — an IEEE-754
-- float artifact (-0.45 isn't exactly representable in binary floating
-- point, so it picked up trailing noise somewhere upstream before
-- landing in scoring_settings). Rounding to 4 decimal places clears
-- that noise without risking rounding away a real value — Sleeper's UI
-- doesn't let commissioners configure anything finer than hundredths,
-- so 4 decimals is generous headroom. Applied to all 14 columns, not
-- just fga, in case others carry the same artifact less visibly (one
-- that rounds to the "right" value by luck wouldn't have failed the
-- sanity check below the way fga did).
--
-- NOT included here: bonus_pt_40p, bonus_pt_50p, bonus_ast_15p,
-- bonus_reb_20p. Sleeper does store these as real keys in
-- scoring_settings, but they're intentionally left hardcoded in
-- game_fantasy_scores instead of pulled from here — decision made
-- 8/13/26, this project is league-specific and these 4 aren't expected
-- to change.
--
-- Also not included: ff (flagrant foul), tf (technical foul), fgmi
-- (FG missed), ftmi (FT missed), tpa (3PT attempted) — none of these
-- are used in game_fantasy_scores, since game_logs has no columns to
-- support them (see game_fantasy_scores_view.sql header).

-- CASCADE: game_fantasy_scores now joins against this view (and
-- game_fantasy_scores_weekly / _weekly_context / _weekly_full all
-- depend on game_fantasy_scores in turn), so this one drop takes out
-- the whole downstream chain. Rebuild order after running this file:
-- game_fantasy_scores_view.sql, then game_fantasy_scores_weekly_view.sql,
-- then game_fantasy_scores_weekly_context_view.sql (also recreates
-- game_fantasy_scores_weekly_full).
DROP VIEW IF EXISTS sleeper_scoring_constants CASCADE;

CREATE VIEW sleeper_scoring_constants AS
SELECT
    league_id,
    season,
    ('2' || season) AS season_id,
    ROUND((scoring_settings->>'pts')::numeric, 4)  AS pts_mult,
    ROUND((scoring_settings->>'reb')::numeric, 4)  AS reb_mult,
    ROUND((scoring_settings->>'oreb')::numeric, 4) AS oreb_mult,
    ROUND((scoring_settings->>'ast')::numeric, 4)  AS ast_mult,
    ROUND((scoring_settings->>'stl')::numeric, 4)  AS stl_mult,
    ROUND((scoring_settings->>'blk')::numeric, 4)  AS blk_mult,
    ROUND((scoring_settings->>'to')::numeric, 4)   AS tov_mult,
    ROUND((scoring_settings->>'fgm')::numeric, 4)  AS fgm_mult,
    ROUND((scoring_settings->>'fga')::numeric, 4)  AS fga_mult,
    ROUND((scoring_settings->>'ftm')::numeric, 4)  AS ftm_mult,
    ROUND((scoring_settings->>'fta')::numeric, 4)  AS fta_mult,
    ROUND((scoring_settings->>'tpm')::numeric, 4)  AS fg3m_mult,
    ROUND((scoring_settings->>'dd')::numeric, 4)   AS dd_bonus,
    ROUND((scoring_settings->>'td')::numeric, 4)   AS td_bonus
FROM sleeper_leagues;

-- =========================
-- Verification
-- =========================

-- One row per Sleeper league you've ingested (2024, 2025 real seasons +
-- 2026 pre-draft shell). Eyeball that every _mult/_bonus column is
-- non-null and looks like a plausible, clean point value now.
SELECT league_id, season, season_id, pts_mult, reb_mult, oreb_mult,
       ast_mult, stl_mult, blk_mult, tov_mult, fgm_mult, fga_mult,
       ftm_mult, fta_mult, fg3m_mult, dd_bonus, td_bonus
FROM sleeper_scoring_constants
ORDER BY season;

-- Sanity check against the known-good hardcoded values from the
-- original game_fantasy_scores formula (pts .5, reb 1.5, oreb .5,
-- ast 2, stl 3, blk 3, tov -1, fgm 1, fga -.45, ftm 1, fta -.5,
-- fg3m .5, dd +3, td +5). Should be TRUE across every row now that
-- rounding clears the float noise — fga_ok was the one that failed
-- before this fix (raw value was -.44999...7907104, not -0.45).
SELECT season_id,
       (pts_mult = 0.5)   AS pts_ok,
       (reb_mult = 1.5)   AS reb_ok,
       (oreb_mult = 0.5)  AS oreb_ok,
       (ast_mult = 2)     AS ast_ok,
       (stl_mult = 3)     AS stl_ok,
       (blk_mult = 3)     AS blk_ok,
       (tov_mult = -1)    AS tov_ok,
       (fgm_mult = 1)     AS fgm_ok,
       (fga_mult = -0.45) AS fga_ok,
       (ftm_mult = 1)     AS ftm_ok,
       (fta_mult = -0.5)  AS fta_ok,
       (fg3m_mult = 0.5)  AS fg3m_ok,
       (dd_bonus = 3)     AS dd_ok,
       (td_bonus = 5)     AS td_ok
FROM sleeper_scoring_constants;
