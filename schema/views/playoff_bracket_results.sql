-- Playoff bracket resolution: fixed-slot 10-team bracket (6-team playoff
-- + 4-team consolation), seeded by regular-season wins/points through
-- week 21. No reseeding -- every week's PAIRING is fully determined by
-- seed number alone; only the WINNER at each stage is data-dependent.
-- That's what makes this buildable as one flat view rather than needing
-- recursion.
--
-- Bracket rules (confirmed 8/15/26):
--   Playoff (seeds 1-6):
--     Week 22: 1,2 bye. 3v6, 4v5.
--     Week 23: 1 vs winner(4v5), 2 vs winner(3v6) -- semis.
--              loser(4v5) vs loser(3v6) -- 5th place game.
--     Week 24: winner(semis) vs winner(semis) -- championship (1st/2nd).
--              loser(semis) vs loser(semis) -- 3rd place game.
--              5th-place-game teams are done -- bye week 24.
--   Consolation (seeds 7-10):
--     Week 22: 7v10, 8v9.
--     Week 23: winners -> 8th place game (7th/8th).
--              losers -> toilet bowl. WINNER of toilet bowl = 9th
--              (confirmed convention), loser = 10th (last place).
--     Week 24: everyone bye.
--
-- Deliberately reads sleeper_matchup_points_snapshots directly (with its
-- own DISTINCT ON dedup below) rather than depending on
-- sleeper_matchup_points_latest -- that view's existence is still
-- unconfirmed (see docs/architecture_risks.md #5); no reason to inherit
-- that open risk here.
--
-- TIEBREAK ASSUMPTION (not explicitly stated -- flagged for confirmation):
-- if two bracket opponents score EXACTLY equal points in a single-
-- elimination game, the higher (better) seed is treated as the winner.
-- No such tie has been confirmed in the real 2024/2025 playoff weeks;
-- this only matters if one ever occurs. The one confirmed real tie this
-- session (2024 week 10, roster 2 vs 7) was a REGULAR SEASON game, not
-- a bracket game -- handled correctly already via historical_matchup_
-- results' 'T' result, unrelated to this assumption.
--
-- Roster_id-pure by design, matching the project's architecture rule --
-- resolve owner names via a join to sleeper_roster_labels_current at
-- query time, not stored here.

DROP VIEW IF EXISTS playoff_bracket_results;

CREATE VIEW playoff_bracket_results AS
WITH points_by_week AS (
    -- Most recent snapshot per (league_id, week, roster_id) -- same
    -- dedup logic sleeper_matchup_points_latest would provide, done
    -- inline since that view's existence isn't confirmed.
    SELECT DISTINCT ON (league_id, week, roster_id)
        league_id, week, roster_id, points
    FROM sleeper_matchup_points_snapshots
    ORDER BY league_id, week, roster_id, synced_at DESC
),
seeds AS (
    SELECT
        hmr.league_id,
        hmr.roster_id,
        ROW_NUMBER() OVER (
            PARTITION BY hmr.league_id
            ORDER BY
                COUNT(*) FILTER (WHERE hmr.result = 'W') DESC,
                SUM(hmr.team_points) DESC
        ) AS seed
    FROM historical_matchup_results hmr
    WHERE hmr.week <= 21
    GROUP BY hmr.league_id, hmr.roster_id
),
pivot AS (
    -- One row per league, each seed's roster_id as its own column --
    -- makes every downstream join explicit and readable by seed number
    -- instead of chaining anonymous self-joins.
    SELECT
        league_id,
        MAX(roster_id) FILTER (WHERE seed = 1)  AS r1,
        MAX(roster_id) FILTER (WHERE seed = 2)  AS r2,
        MAX(roster_id) FILTER (WHERE seed = 3)  AS r3,
        MAX(roster_id) FILTER (WHERE seed = 4)  AS r4,
        MAX(roster_id) FILTER (WHERE seed = 5)  AS r5,
        MAX(roster_id) FILTER (WHERE seed = 6)  AS r6,
        MAX(roster_id) FILTER (WHERE seed = 7)  AS r7,
        MAX(roster_id) FILTER (WHERE seed = 8)  AS r8,
        MAX(roster_id) FILTER (WHERE seed = 9)  AS r9,
        MAX(roster_id) FILTER (WHERE seed = 10) AS r10
    FROM seeds
    GROUP BY league_id
),

-- =========================
-- Week 22: 3v6, 4v5, 7v10, 8v9. (1,2 bye -- no points needed for them yet.)
-- =========================
week22_pts AS (
    SELECT
        p.*,
        pw3.points AS pts3, pw6.points AS pts6,
        pw4.points AS pts4, pw5.points AS pts5,
        pw7.points AS pts7, pw10.points AS pts10,
        pw8.points AS pts8, pw9.points AS pts9
    FROM pivot p
    JOIN points_by_week pw3  ON pw3.league_id = p.league_id  AND pw3.week = 22  AND pw3.roster_id = p.r3
    JOIN points_by_week pw6  ON pw6.league_id = p.league_id  AND pw6.week = 22  AND pw6.roster_id = p.r6
    JOIN points_by_week pw4  ON pw4.league_id = p.league_id  AND pw4.week = 22  AND pw4.roster_id = p.r4
    JOIN points_by_week pw5  ON pw5.league_id = p.league_id  AND pw5.week = 22  AND pw5.roster_id = p.r5
    JOIN points_by_week pw7  ON pw7.league_id = p.league_id  AND pw7.week = 22  AND pw7.roster_id = p.r7
    JOIN points_by_week pw10 ON pw10.league_id = p.league_id AND pw10.week = 22 AND pw10.roster_id = p.r10
    JOIN points_by_week pw8  ON pw8.league_id = p.league_id  AND pw8.week = 22  AND pw8.roster_id = p.r8
    JOIN points_by_week pw9  ON pw9.league_id = p.league_id  AND pw9.week = 22  AND pw9.roster_id = p.r9
),
week22_results AS (
    SELECT *,
        CASE WHEN pts3  >= pts6  THEN r3  ELSE r6  END AS w_3v6,  CASE WHEN pts3  >= pts6  THEN r6  ELSE r3  END AS l_3v6,
        CASE WHEN pts4  >= pts5  THEN r4  ELSE r5  END AS w_4v5,  CASE WHEN pts4  >= pts5  THEN r5  ELSE r4  END AS l_4v5,
        CASE WHEN pts7  >= pts10 THEN r7  ELSE r10 END AS w_7v10, CASE WHEN pts7  >= pts10 THEN r10 ELSE r7  END AS l_7v10,
        CASE WHEN pts8  >= pts9  THEN r8  ELSE r9  END AS w_8v9,  CASE WHEN pts8  >= pts9  THEN r9  ELSE r8  END AS l_8v9
    FROM week22_pts
),

-- =========================
-- Week 23: semis (1 vs w(4v5), 2 vs w(3v6)), 5th place, 8th place, toilet bowl
-- =========================
week23_pts AS (
    SELECT
        wr.*,
        pw1.points    AS pts1,    pwW45.points  AS ptsW45,
        pw2.points    AS pts2,    pwW36.points  AS ptsW36,
        pwL45.points  AS ptsL45,  pwL36.points  AS ptsL36,
        pwW710.points AS ptsW710, pwW89.points  AS ptsW89,
        pwL710.points AS ptsL710, pwL89.points  AS ptsL89
    FROM week22_results wr
    JOIN points_by_week pw1    ON pw1.league_id = wr.league_id    AND pw1.week = 23    AND pw1.roster_id = wr.r1
    JOIN points_by_week pwW45  ON pwW45.league_id = wr.league_id  AND pwW45.week = 23  AND pwW45.roster_id = wr.w_4v5
    JOIN points_by_week pw2    ON pw2.league_id = wr.league_id    AND pw2.week = 23    AND pw2.roster_id = wr.r2
    JOIN points_by_week pwW36  ON pwW36.league_id = wr.league_id  AND pwW36.week = 23  AND pwW36.roster_id = wr.w_3v6
    JOIN points_by_week pwL45  ON pwL45.league_id = wr.league_id  AND pwL45.week = 23  AND pwL45.roster_id = wr.l_4v5
    JOIN points_by_week pwL36  ON pwL36.league_id = wr.league_id  AND pwL36.week = 23  AND pwL36.roster_id = wr.l_3v6
    JOIN points_by_week pwW710 ON pwW710.league_id = wr.league_id AND pwW710.week = 23 AND pwW710.roster_id = wr.w_7v10
    JOIN points_by_week pwW89  ON pwW89.league_id = wr.league_id  AND pwW89.week = 23  AND pwW89.roster_id = wr.w_8v9
    JOIN points_by_week pwL710 ON pwL710.league_id = wr.league_id AND pwL710.week = 23 AND pwL710.roster_id = wr.l_7v10
    JOIN points_by_week pwL89  ON pwL89.league_id = wr.league_id  AND pwL89.week = 23  AND pwL89.roster_id = wr.l_8v9
),
week23_results AS (
    SELECT *,
        CASE WHEN pts1    >= ptsW45  THEN r1     ELSE w_4v5 END AS w_semi1, CASE WHEN pts1    >= ptsW45  THEN w_4v5 ELSE r1     END AS l_semi1,
        CASE WHEN pts2    >= ptsW36  THEN r2     ELSE w_3v6 END AS w_semi2, CASE WHEN pts2    >= ptsW36  THEN w_3v6 ELSE r2     END AS l_semi2,
        CASE WHEN ptsL45  >= ptsL36  THEN l_4v5  ELSE l_3v6 END AS place5,  CASE WHEN ptsL45  >= ptsL36  THEN l_3v6 ELSE l_4v5  END AS place6,
        CASE WHEN ptsW710 >= ptsW89  THEN w_7v10 ELSE w_8v9 END AS place7,  CASE WHEN ptsW710 >= ptsW89  THEN w_8v9 ELSE w_7v10 END AS place8,
        -- toilet bowl: WINNER = 9th (confirmed), loser = 10th
        CASE WHEN ptsL710 >= ptsL89 THEN l_7v10 ELSE l_8v9 END AS place9,  CASE WHEN ptsL710 >= ptsL89 THEN l_8v9  ELSE l_7v10 END AS place10
    FROM week23_pts
),

-- =========================
-- Week 24: championship, 3rd place game. (Everyone else already placed.)
-- =========================
week24_pts AS (
    SELECT
        wr.*,
        pwWS1.points AS ptsWS1, pwWS2.points AS ptsWS2,
        pwLS1.points AS ptsLS1, pwLS2.points AS ptsLS2
    FROM week23_results wr
    JOIN points_by_week pwWS1 ON pwWS1.league_id = wr.league_id AND pwWS1.week = 24 AND pwWS1.roster_id = wr.w_semi1
    JOIN points_by_week pwWS2 ON pwWS2.league_id = wr.league_id AND pwWS2.week = 24 AND pwWS2.roster_id = wr.w_semi2
    JOIN points_by_week pwLS1 ON pwLS1.league_id = wr.league_id AND pwLS1.week = 24 AND pwLS1.roster_id = wr.l_semi1
    JOIN points_by_week pwLS2 ON pwLS2.league_id = wr.league_id AND pwLS2.week = 24 AND pwLS2.roster_id = wr.l_semi2
),
week24_results AS (
    SELECT *,
        CASE WHEN ptsWS1 >= ptsWS2 THEN w_semi1 ELSE w_semi2 END AS place1,
        CASE WHEN ptsWS1 >= ptsWS2 THEN w_semi2 ELSE w_semi1 END AS place2,
        CASE WHEN ptsLS1 >= ptsLS2 THEN l_semi1 ELSE l_semi2 END AS place3,
        CASE WHEN ptsLS1 >= ptsLS2 THEN l_semi2 ELSE l_semi1 END AS place4
    FROM week24_pts
)

SELECT league_id, 1  AS final_place, place1  AS roster_id FROM week24_results
UNION ALL SELECT league_id, 2,  place2  FROM week24_results
UNION ALL SELECT league_id, 3,  place3  FROM week24_results
UNION ALL SELECT league_id, 4,  place4  FROM week24_results
UNION ALL SELECT league_id, 5,  place5  FROM week24_results
UNION ALL SELECT league_id, 6,  place6  FROM week24_results
UNION ALL SELECT league_id, 7,  place7  FROM week24_results
UNION ALL SELECT league_id, 8,  place8  FROM week24_results
UNION ALL SELECT league_id, 9,  place9  FROM week24_results
UNION ALL SELECT league_id, 10, place10 FROM week24_results;

-- =========================
-- Verification
-- =========================

-- Exactly 10 rows per league that has completed its playoffs. A league
-- still mid-season (e.g. current year before week 24 finishes) will
-- simply return 0 rows for itself -- the JOINs on future weeks find no
-- snapshot data yet, not an error.
SELECT league_id, COUNT(*) AS placements_found
FROM playoff_bracket_results
GROUP BY league_id;

-- Readable version, spot-check against what actually happened --
-- resolve names via the display layer, not stored in the view itself.
SELECT pbr.league_id, sl.season, pbr.final_place, rl.current_owner_name
FROM playoff_bracket_results pbr
JOIN sleeper_leagues sl ON sl.league_id = pbr.league_id
JOIN sleeper_roster_labels_current rl
    ON rl.league_id = pbr.league_id AND rl.roster_id = pbr.roster_id
ORDER BY sl.season DESC, pbr.final_place;

-- Sanity check: no roster_id should appear twice for the same league
-- (would mean a bracket-slot join went wrong somewhere upstream)
SELECT league_id, roster_id, COUNT(*)
FROM playoff_bracket_results
GROUP BY league_id, roster_id
HAVING COUNT(*) > 1;
