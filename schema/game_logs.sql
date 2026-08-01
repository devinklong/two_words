DROP TABLE IF EXISTS game_logs;
CREATE TABLE game_logs (
    game_id          VARCHAR(20)  NOT NULL,
    player_id        INTEGER      NOT NULL REFERENCES players(player_id),
    team_id          INTEGER      NOT NULL REFERENCES teams(team_id),
    opponent_team_id INTEGER      NOT NULL REFERENCES teams(team_id),
    season_id        VARCHAR(10),
    game_date        DATE         NOT NULL,
    is_home           BOOLEAN,
    wl                CHAR(1),
    minutes           INTEGER,        -- was `min`; renamed to avoid confusion with MIN() — verified int64, no MM:SS strings
    fgm               INTEGER,
    fga               INTEGER,
    fg3m              INTEGER,
    fg3a              INTEGER,
    ftm               INTEGER,
    fta               INTEGER,
    oreb              INTEGER,
    dreb              INTEGER,
    ast               INTEGER,
    stl               INTEGER,
    blk               INTEGER,
    tov               INTEGER,
    pf                INTEGER,
    pts               INTEGER,
    plus_minus        NUMERIC,
    PRIMARY KEY (game_id, player_id)
);

SELECT * FROM game_logs;

SELECT COUNT(*) FROM game_logs;

SELECT COUNT(DISTINCT player_id) FROM game_logs;