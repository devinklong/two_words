-- Does each team's locked games add up to Sleeper's final score for the week?
-- final_points comes from Sleeper's season total (fpts) via infer_lock_decisions.py (sleeper_roster_week_totals);
-- locked_points is the sum of that team's recorded lock rows. reconciled = they match to the cent.
-- checksum_status: unique / offset = the lock choice is proven; multi / ambiguous = scores match but several game
-- combinations add up to the same total; no_match = rows written 'unverified' and the totals do not agree.
CREATE OR REPLACE VIEW nba_week_reconciliation AS
SELECT t.league_id, t.season_id, t.week_number, t.roster_id, t.final_points,
       COALESCE(SUM(d.fantasy_score), 0) AS locked_points,
       COUNT(d.slot) AS slots,
       ROUND(t.final_points - COALESCE(SUM(d.fantasy_score), 0), 2) AS diff,
       ABS(t.final_points - COALESCE(SUM(d.fantasy_score), 0)) < 0.005 AS reconciled,
       t.status AS checksum_status
FROM sleeper_roster_week_totals t
LEFT JOIN nba_lock_decisions d
       ON d.league_id = t.league_id AND d.season_id = t.season_id
      AND d.week_number = t.week_number AND d.roster_id = t.roster_id
GROUP BY t.league_id, t.season_id, t.week_number, t.roster_id, t.final_points, t.status;
