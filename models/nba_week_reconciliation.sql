-- Does each team's locked games add up to Sleeper's final score for the week?
-- final_points comes from Sleeper's season total (fpts) via infer_lock_decisions.py (sleeper_roster_week_totals);
-- locked_points is the sum of that team's recorded lock rows, scored from the OFFICIAL NBA box scores as they stand now.
-- Sleeper freezes a week's total when the week ends, so it can hold the score from before a later NBA stat correction.
-- explained_diff is that effect: the (credited minus official) score of the corrected games in the matched combination
-- (see log_score_corrections.py). reconciled = they match to the cent, either directly or once explained_diff is applied.
-- checksum_status: unique / offset = the lock choice is proven; multi / ambiguous = scores match but several game
-- combinations add up to the same total; no_match = rows written 'unverified' and the totals do not agree.
ALTER TABLE sleeper_roster_week_totals ADD COLUMN IF NOT EXISTS explained_diff numeric NOT NULL DEFAULT 0;

CREATE OR REPLACE VIEW nba_week_reconciliation AS
SELECT t.league_id, t.season_id, t.week_number, t.roster_id, t.final_points,
       COALESCE(SUM(d.fantasy_score), 0) AS locked_points,
       COUNT(d.slot) AS slots,
       ROUND(t.final_points - COALESCE(SUM(d.fantasy_score), 0), 2) AS diff,
       t.explained_diff,
       (ABS(t.final_points - COALESCE(SUM(d.fantasy_score), 0)) < 0.005
        OR (t.explained_diff <> 0
            AND ABS(t.final_points - COALESCE(SUM(d.fantasy_score), 0) - t.explained_diff) < 0.005)) AS reconciled,
       t.status AS checksum_status
FROM sleeper_roster_week_totals t
LEFT JOIN nba_lock_decisions d
       ON d.league_id = t.league_id AND d.season_id = t.season_id
      AND d.week_number = t.week_number AND d.roster_id = t.roster_id
GROUP BY t.league_id, t.season_id, t.week_number, t.roster_id, t.final_points, t.explained_diff, t.status;
