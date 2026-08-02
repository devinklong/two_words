-- team_schedule currently has game_id alone as PK. Since get_team_schedule()
-- pulls one team's perspective per call, looping over all 30 teams produces
-- two rows per game (one per team) sharing the same game_id. A solo game_id
-- PK collides on the second row of every game — this must run before
-- load_team_schedule.py, or you'll silently lose half your data to
-- ON CONFLICT DO NOTHING.

ALTER TABLE team_schedule DROP CONSTRAINT team_schedule_pkey;
ALTER TABLE team_schedule ADD PRIMARY KEY (game_id, team_id);

-- Verify
SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'team_schedule'::regclass AND contype = 'p';

SELECT COUNT(*) FROM team_schedule;

SELECT game_id, COUNT(*) FROM team_schedule GROUP BY game_id HAVING COUNT(*) != 2;

SELECT game_id, COUNT(*) FROM team_schedule GROUP BY game_id HAVING COUNT(*) = 2 LIMIT 5;
