"""Record every NBA player's Sleeper injury status, with how long each spell lasts.

Sleeper's public players file (https://api.sleeper.app/v1/players/nba) carries injury_status (Out, Doubtful, Questionable, GTD...),
the body part, a note, and the player's status. This script reads it once per run and keeps a history instead of a snapshot:
  sleeper_injury_spells   one row per continuous spell: sleeper_player_id, injury_status, body_part, notes, status, team,
                          first_seen_at, last_seen_at, ended_at (NULL while the spell is still open)
  sleeper_injury_runs     one row per successful read (for the freshness check in health_summary.py)
Open spells are the current injury list. Closed spells are the evidence for how often "Questionable" actually misses a game and how long
"Out" lasts, which is what the analysis tools should be calibrated on rather than guesses.

Sleeper asks for at most one read of that file per day, so this runs in the morning run only, not the hourly one. A reply with fewer than
300 players is treated as a bad read and nothing is written (otherwise every spell would look finished).

Usage (from ~/projects/two_words):  python scripts/sleeper/ingest_player_status.py [--dry-run]
"""
import argparse
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.append(str(Path(__file__).resolve().parents[1]))

URL = "https://api.sleeper.app/v1/players/nba"
MIN_PLAYERS = 300
DDL = [
    """CREATE TABLE IF NOT EXISTS sleeper_injury_spells (
        spell_id bigserial PRIMARY KEY, sleeper_player_id text NOT NULL, player_name text, injury_status text, body_part text, notes text,
        status text, team text, first_seen_at timestamptz NOT NULL DEFAULT now(), last_seen_at timestamptz NOT NULL DEFAULT now(),
        ended_at timestamptz)""",
    "CREATE INDEX IF NOT EXISTS sleeper_injury_spells_open ON sleeper_injury_spells (sleeper_player_id) WHERE ended_at IS NULL",
    "CREATE TABLE IF NOT EXISTS sleeper_injury_runs (fetched_at timestamptz PRIMARY KEY DEFAULT now(), players integer, flagged integer)",
]


def flagged(p):
    """(injury_status, body_part, notes, status) when Sleeper lists an injury status for the player, else None.
    The status field is a code (ACT, FA, RET...) that says nothing about health, so only injury_status counts."""
    inj = (p.get("injury_status") or "").strip() or None
    if not inj:
        return None
    return inj, (p.get("injury_body_part") or None), (p.get("injury_notes") or None), ((p.get("status") or "").strip() or None)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    req = Request(URL, headers={"User-Agent": "two_words/1.0"})
    with urlopen(req, timeout=60) as resp:
        players = json.load(resp)
    if len(players) < MIN_PLAYERS:
        raise SystemExit(f"only {len(players)} players in Sleeper's reply; treating it as a bad read, nothing written")
    now_hurt = {}
    for pid, p in players.items():
        f = flagged(p)
        if f:
            name = p.get("full_name") or " ".join(x for x in (p.get("first_name"), p.get("last_name")) if x)
            now_hurt[str(pid)] = (name, *f, p.get("team"))
    print(f"{len(players)} players read, {len(now_hurt)} with an injury status")
    if args.dry_run:
        for pid, (name, inj, part, _n, status, team) in sorted(now_hurt.items(), key=lambda x: x[1][0] or "")[:40]:
            print(f"  {name} ({team}): {inj or '-'} {part or ''} [{status}]")
        print("(dry run, nothing written)")
        return

    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    for ddl in DDL:
        cur.execute(ddl)
    cur.execute("SELECT spell_id, sleeper_player_id, injury_status, body_part, status FROM sleeper_injury_spells WHERE ended_at IS NULL")
    open_spells = {r[1]: r for r in cur.fetchall()}
    opened = closed = 0
    for pid, row in open_spells.items():
        sid, inj, part, status = row[0], row[2], row[3], row[4]
        cur_state = now_hurt.get(pid)
        if cur_state is None or (cur_state[1], cur_state[2], cur_state[4]) != (inj, part, status):
            cur.execute("UPDATE sleeper_injury_spells SET ended_at = now() WHERE spell_id = %s", (sid,))
            closed += 1
        else:
            cur.execute("UPDATE sleeper_injury_spells SET last_seen_at = now(), notes = %s, team = %s WHERE spell_id = %s",
                        (cur_state[3], cur_state[5], sid))
    for pid, (name, inj, part, notes, status, team) in now_hurt.items():
        cur.execute("""SELECT 1 FROM sleeper_injury_spells WHERE sleeper_player_id = %s AND ended_at IS NULL
                       AND injury_status IS NOT DISTINCT FROM %s AND body_part IS NOT DISTINCT FROM %s AND status IS NOT DISTINCT FROM %s""",
                    (pid, inj, part, status))
        if cur.fetchone():
            continue
        cur.execute("""INSERT INTO sleeper_injury_spells (sleeper_player_id, player_name, injury_status, body_part, notes, status, team)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""", (pid, name, inj, part, notes, status, team))
        opened += 1
    cur.execute("INSERT INTO sleeper_injury_runs (players, flagged) VALUES (%s, %s)", (len(players), len(now_hurt)))
    conn.commit()
    print(f"{opened} spell(s) opened, {closed} closed, {len(now_hurt)} currently listed")
    cur.close(); conn.close()


if __name__ == "__main__":
    main()
