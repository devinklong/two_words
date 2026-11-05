"""Rebuild every NBA player's games-missed history from game_logs and team_schedule (all seasons loaded).

For each player, season and team he played for, every completed team game inside his span with that team that has no game log row for him
(or a row with 0 / NULL minutes) is an absence. Rows go into player_absences, one per missed team game:
  kind 'missed'           no log, between his first and last game with that team (a gap in his season)
  kind 'dnp'              a log row exists but minutes are 0 or NULL
  kind 'after_last_game'  team games after his last game, only for his LAST team of the season: a season-ending injury, a release, or
                          (this season) a current absence. The logs cannot tell these apart, so analyses keep them in their own column.
Games before his first game with a team are never counted (the logs cannot tell a late arrival from an early injury).
Views built on it: player_absence_runs (each continuous run of missed team games) and player_season_availability (per player, season, team:
games played, missed, dnp, longest run, trailing games, average minutes, is_rotation).
CAUTION: a 'missed' game is an injury, rest or a coach's decision; the logs cannot tell them apart. Fringe players (a few games, low minutes) 'miss'
most of the schedule for plain roster reasons, so injury analyses must filter on is_rotation (average minutes >= 18 over at least 10 games played; a few big-minute games by a two-way player must not make him a rotation player).

Why a table and not a view: other projects (eredivisie_scout and the dynasty value work) read it by player id; the Sleeper injury spells
(sleeper_injury_spells) add the cause and status from the day the feed started, this adds the five seasons before it.

Usage (from ~/projects/two_words):  python scripts/build_absence_history.py [--season-id 22025] [--dry-run]
Safe to rerun: each season is deleted and rebuilt.
"""
import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))

DDL = [
    """CREATE TABLE IF NOT EXISTS player_absences (
        player_id integer NOT NULL, season_id text NOT NULL, team_id integer NOT NULL, game_id text NOT NULL,
        game_date date NOT NULL, team_game_no integer NOT NULL, kind text NOT NULL,
        PRIMARY KEY (player_id, game_id))""",
    "CREATE INDEX IF NOT EXISTS player_absences_season ON player_absences (season_id, player_id)",
    """CREATE OR REPLACE VIEW player_absence_runs AS
       WITH x AS (
           SELECT *, team_game_no - row_number() OVER (PARTITION BY player_id, season_id, team_id ORDER BY team_game_no) AS grp
           FROM player_absences)
       SELECT player_id, season_id, team_id, min(game_date) AS first_missed, max(game_date) AS last_missed, count(*) AS games,
              bool_or(kind = 'after_last_game') AS open_ended
       FROM x GROUP BY player_id, season_id, team_id, grp""",
    """CREATE OR REPLACE VIEW player_season_availability AS
       SELECT l.player_id, l.season_id, l.team_id, l.games_played,
              COALESCE(a.missed, 0) AS missed, COALESCE(a.dnp, 0) AS dnp, COALESCE(a.trailing, 0) AS trailing,
              COALESCE(r.longest_run, 0) AS longest_run,
              l.avg_minutes, (l.avg_minutes >= 18 AND l.games_played >= 10) AS is_rotation
       FROM (SELECT player_id, season_id, team_id, count(*) AS games_played, round(avg(minutes), 1) AS avg_minutes
             FROM game_logs WHERE minutes > 0 GROUP BY 1,2,3) l
       LEFT JOIN (SELECT player_id, season_id, team_id,
                         count(*) FILTER (WHERE kind = 'missed') AS missed, count(*) FILTER (WHERE kind = 'dnp') AS dnp,
                         count(*) FILTER (WHERE kind = 'after_last_game') AS trailing
                  FROM player_absences GROUP BY 1,2,3) a USING (player_id, season_id, team_id)
       LEFT JOIN (SELECT player_id, season_id, team_id, max(games) AS longest_run FROM player_absence_runs GROUP BY 1,2,3) r
              USING (player_id, season_id, team_id)""",
]

BUILD = """
WITH sched AS (
    SELECT game_id, team_id, game_date,
           row_number() OVER (PARTITION BY team_id ORDER BY game_date, game_id) AS team_game_no
    FROM team_schedule WHERE season_id = %(s)s AND wl IS NOT NULL),
span AS (
    SELECT player_id, team_id, min(game_date) AS first_d, max(game_date) AS last_d,
           bool_or(true) AS any_row
    FROM game_logs WHERE season_id = %(s)s GROUP BY player_id, team_id),
last_team AS (
    SELECT DISTINCT ON (player_id) player_id, team_id FROM span ORDER BY player_id, last_d DESC)
INSERT INTO player_absences (player_id, season_id, team_id, game_id, game_date, team_game_no, kind)
SELECT sp.player_id, %(s)s, sp.team_id, s.game_id, s.game_date, s.team_game_no,
       CASE WHEN g.player_id IS NOT NULL THEN 'dnp'
            WHEN s.game_date > sp.last_d THEN 'after_last_game'
            ELSE 'missed' END
FROM span sp
JOIN sched s ON s.team_id = sp.team_id AND s.game_date >= sp.first_d
LEFT JOIN game_logs g ON g.game_id = s.game_id AND g.player_id = sp.player_id
LEFT JOIN last_team lt ON lt.player_id = sp.player_id AND lt.team_id = sp.team_id
WHERE (g.player_id IS NULL OR COALESCE(g.minutes, 0) = 0)
  AND (s.game_date <= sp.last_d OR lt.team_id IS NOT NULL)
ON CONFLICT DO NOTHING
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season-id")
    ap.add_argument("--dry-run", action="store_true", help="build, report, then roll back")
    args = ap.parse_args()

    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    for ddl in DDL:
        cur.execute(ddl)
    cur.execute("SELECT DISTINCT season_id FROM game_logs WHERE season_id IS NOT NULL ORDER BY 1")
    seasons = [r[0] for r in cur.fetchall() if not args.season_id or r[0] == args.season_id]
    for s in seasons:
        cur.execute("DELETE FROM player_absences WHERE season_id = %s", (s,))
        cur.execute(BUILD, {"s": s})
        cur.execute("""SELECT kind, count(*), count(DISTINCT player_id) FROM player_absences WHERE season_id = %s GROUP BY kind ORDER BY kind""", (s,))
        parts = ", ".join(f"{k}: {n} games ({p} players)" for k, n, p in cur.fetchall())
        print(f"season {s}: {parts or 'no absences'}")
    if args.dry_run:
        conn.rollback()
        print("(dry run, rolled back)")
    else:
        conn.commit()
    cur.close(); conn.close()


if __name__ == "__main__":
    main()
