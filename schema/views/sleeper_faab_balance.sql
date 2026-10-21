-- sleeper_faab_balance: FAAB budget, spent and remaining per roster per league/season.
-- Added 10/21/26. One row per (league_id, roster_id), all seasons in the chain.
--
-- Sources:
--   budget            sleeper_leagues.settings->>'waiver_budget'
--   budget_used       sleeper_rosters.settings->>'waiver_budget_used'  (Sleeper's own figure;
--                     it also reflects FAAB traded between teams)
--   spent_on_claims   SUM(waiver_bid) of this roster's COMPLETED waiver claims
--                     (sleeper_transactions; failed claims cost nothing)
-- remaining = budget - budget_used.
-- other_budget_movement = budget_used - spent_on_claims. Normally 0. Nonzero means FAAB was
-- traded or a bid is missing/unstored for that roster (past seasons' rosters hold their final figures).
-- Any column is NULL when its source setting isn't stored; nothing is guessed.
-- Safe to re-run: CREATE OR REPLACE, no dependents.
CREATE OR REPLACE VIEW sleeper_faab_balance AS
WITH bids AS (
    SELECT league_id,
           roster_ids[1] AS roster_id,
           COUNT(*) AS claims,
           COALESCE(SUM(waiver_bid), 0) AS spent_on_claims
    FROM sleeper_transactions
    WHERE type = 'waiver' AND status = 'complete'
    GROUP BY league_id, roster_ids[1]
)
SELECT r.league_id,
       l.season,
       r.roster_id,
       u.display_name AS owner_name,
       (l.settings->>'waiver_budget')::int AS budget,
       (r.settings->>'waiver_budget_used')::int AS budget_used,
       (l.settings->>'waiver_budget')::int - (r.settings->>'waiver_budget_used')::int AS remaining,
       COALESCE(b.claims, 0) AS claims,
       COALESCE(b.spent_on_claims, 0) AS spent_on_claims,
       (r.settings->>'waiver_budget_used')::int - COALESCE(b.spent_on_claims, 0) AS other_budget_movement
FROM sleeper_rosters r
JOIN sleeper_leagues l ON l.league_id = r.league_id
LEFT JOIN sleeper_users u ON u.league_id = r.league_id AND u.user_id = r.owner_id
LEFT JOIN bids b ON b.league_id = r.league_id AND b.roster_id = r.roster_id;
