-- schema/tables/sleeper_player_fantasy_positions.sql
--
-- Child table for player-side position eligibility (v3.2). One row per
-- eligible position per player -- genuinely 1NF, unlike a position_1..
-- position_5 repeating-group column set. Sourced from Sleeper's real
-- `fantasy_positions` array (NOT the singular `position` field the
-- original crosswalk build used, which silently collapsed 68% of
-- multi-eligible players down to one position).
--
-- Forward-looking only, current-state-only -- same precedent as
-- `roster_ownership`. No historical tracking: Sleeper reassigns
-- eligibility mid-season based on real lineup usage, never formally
-- announced, so there's nothing meaningful to version. Refreshed via a
-- full delete-then-reinsert per player on every crosswalk sync (matches
-- roster_ownership's pattern), not incremental updates, since the row
-- COUNT itself changes whenever a player's eligibility changes.
--
-- Confirmed 8/23/26 (inspect_all_fantasy_position_values.py, full
-- Sleeper player directory scan): only 6 distinct values ever appear in
-- any player's fantasy_positions array -- C, PF, PG, SF, SG, and DEF.
-- DEF is Sleeper's team-defense entry type and never applies to an
-- individual NBA player, so it's filtered out at sync time, not stored
-- here. No generic/group labels (F, G, UTIL) ever appear player-side --
-- those exist only as roster SLOT types.

CREATE TABLE IF NOT EXISTS sleeper_player_fantasy_positions (
    sleeper_player_id  TEXT NOT NULL
        REFERENCES sleeper_player_crosswalk (sleeper_player_id) ON DELETE CASCADE,
    position            TEXT NOT NULL,
    PRIMARY KEY (sleeper_player_id, position)
);
