
CREATE TABLE teams (
    team_id INTEGER PRIMARY KEY,
    abbreviation VARCHAR(3) NOT NULL,
    full_name VARCHAR(50) NOT NULL,
    city VARCHAR(50),
    state VARCHAR(50),
    year_founded INTEGER
);

CREATE TABLE players (
    player_id INTEGER PRIMARY KEY,
    full_name VARCHAR(100) NOT NULL,
    first_name VARCHAR(50),
    last_name VARCHAR(50),
    is_active BOOLEAN
);