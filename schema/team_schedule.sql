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