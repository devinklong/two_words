DROP TABLE IF EXISTS team_schedule;

CREATE TABLE team_schedule (
    game_id VARCHAR(20) PRIMARY KEY,
    season_id VARCHAR(10),
    team_id INTEGER REFERENCES teams(team_id),
    opponent_team_id INTEGER REFERENCES teams(team_id),
    game_date DATE NOT NULL,
    is_home BOOLEAN,
    wl CHAR(1),
    pts INTEGER,
    plus_minus NUMERIC
    -- fgm, fga, ftm, fta, oreb, dreb, ast, stl, blk, tov, pf, etc.
);

SELECT COUNT(*) FROM team_schedule WHERE season_id = '22024';

SELECT COUNT(*) FROM team_schedule WHERE season_id = '22023';

SELECT COUNT(*) FROM team_schedule WHERE season_id = '22022';

SELECT COUNT(*) FROM team_schedule WHERE season_id = '22021';
