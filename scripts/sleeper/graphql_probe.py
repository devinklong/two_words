"""Ask Sleeper's own GraphQL endpoint (the one the website and app use) whether it exposes lineup locks.

The public REST matchups endpoint has no lock flag, and its points flip between the locked value, the last-game value and
old stale values. The app shows padlocks, so the server knows. This script is read-only and needs no login:

  1. introspects the schema (anonymous introspection is allowed) and lists every query that mentions matchups, plus every
     field of the types they return, flagging names that look like locks (lock, start, game, status, eligib, bank, frozen)
  2. builds a query for the matchup read automatically from that schema and runs it for your league and week
  3. prints the real data for your roster and every key anywhere in the response whose name looks like a lock

Usage (from ~/projects/two_words):  python scripts/sleeper/graphql_probe.py [--league-id ID] [--week N] [--roster-id 8]
Paste the whole output back. Nothing is written to the database.
"""
import argparse
import json
import re
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

URL = "https://sleeper.com/graphql"
SUSPECT = re.compile(r"lock|start|game|status|eligib|bank|frozen|final|live", re.I)

TYPE_REF = "kind name of_type { kind name of_type { kind name of_type { kind name of_type { kind name } } } }"   # Sleeper's server uses snake_case


def post(query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = Request(URL, data=body, headers={"Content-Type": "application/json", "Origin": "https://sleeper.com",
                                           "User-Agent": "Mozilla/5.0 (Macintosh) two_words-probe/1.0"})
    try:
        with urlopen(req, timeout=40) as resp:
            return json.load(resp)
    except HTTPError as e:   # Sleeper answers errors with a JSON body that says why
        return {"http_error": e.code, "body": e.read().decode(errors="replace")[:1500]}


def named(t):
    """Unwrap NON_NULL / LIST wrappers -> (name, kind, is_list)."""
    is_list = False
    while t and t.get("name") is None:
        is_list = is_list or t["kind"] == "LIST"
        t = t.get("of_type")
    return (t["name"], t["kind"], is_list) if t else (None, None, is_list)


def type_fields(name):
    r = post('query($n:String!){ __type(name:$n){ kind fields { name args { name type { %s } } type { %s } } input_fields { name type { %s } } } }'
             % (TYPE_REF, TYPE_REF, TYPE_REF), {"n": name})
    return (r.get("data") or {}).get("__type") or {}


def selection(type_name, depth, seen=()):
    """GraphQL selection set with every scalar field, descending into object fields up to `depth` levels."""
    info = type_fields(type_name)
    parts = []
    for f in info.get("fields") or []:
        if f["args"] and any(named(a["type"])[1] == "NON_NULL" or (a["type"]["kind"] == "NON_NULL") for a in f["args"]):
            continue   # fields that need arguments cannot be selected blindly
        n, kind, _ = named(f["type"])
        if kind in ("SCALAR", "ENUM"):
            parts.append(f["name"])
        elif kind in ("OBJECT", "INTERFACE") and depth > 0 and n not in seen:
            sub = selection(n, depth - 1, seen + (type_name,))
            if sub:
                parts.append(f"{f['name']} {{ {sub} }}")
    return " ".join(parts)


def walk(node, path=""):
    """Every key path in a JSON response whose name looks like a lock."""
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{path}.{k}" if path else k
            if SUSPECT.search(k):
                yield p, v
            yield from walk(v, p)
    elif isinstance(node, list):
        for i, v in enumerate(node[:3]):
            yield from walk(v, f"{path}[{i}]")


def direct_probe(league, week, roster_id):
    """Introspection is off: ask for a few likely shapes of matchup_legs and report what the server says."""
    tries = [
        ("matchup_legs(league_id, round)", 'query($l:String!,$r:Int!){ matchup_legs(league_id:$l, round:$r){ roster_id points starters players_points starters_points } }', {"l": str(league), "r": week}),
        ("matchup_legs(league_id, leg)", 'query($l:String!,$r:Int!){ matchup_legs(league_id:$l, leg:$r){ roster_id points starters players_points starters_points } }', {"l": str(league), "r": week}),
    ]
    for label, q, v in tries:
        res = post(q, v)
        print(f"\n{label}:")
        print(json.dumps(res, indent=1, default=str)[:2500])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--league-id")
    ap.add_argument("--week", type=int)
    ap.add_argument("--roster-id", type=int, default=8)
    ap.add_argument("--find", help="regex: list queries whose name matches, with their arguments and the fields of what they return, then stop")
    args = ap.parse_args()

    league, week = args.league_id, args.week
    if not league or not week:
        sys.path.append(str(Path(__file__).resolve().parents[1]))
        from db_connection import get_connection
        conn = get_connection()
        cur = conn.cursor()
        if not league:
            cur.execute("SELECT league_id FROM sleeper_current_league")
            league = cur.fetchone()[0]
        if not week:
            cur.execute("SELECT week_number FROM fantasy_weeks WHERE week_start_date <= CURRENT_DATE AND week_end_date >= CURRENT_DATE "
                        "ORDER BY season_id DESC LIMIT 1")
            week = cur.fetchone()[0]
        conn.close()
    print(f"league {league}, week {week}\n")

    roots = post("{ __schema { query_type { fields { name args { name type { %s } } type { %s } } } } }" % (TYPE_REF, TYPE_REF))
    if not (roots.get("data") or {}).get("__schema"):
        print("The server did not return its schema. Raw reply:")
        print(json.dumps(roots, indent=1)[:2000])
        print("\nTrying the matchup read directly instead.")
        return direct_probe(league, week, args.roster_id)
    fields = roots["data"]["__schema"]["query_type"]["fields"]
    if args.find:
        for f in fields:
            if re.search(args.find, f["name"], re.I):
                argtxt = ", ".join(f"{a['name']}:{named(a['type'])[0]}" for a in f["args"])
                tname = named(f["type"])[0]
                print(f"\n{f['name']}({argtxt}) -> {tname}")
                for sub in type_fields(tname).get("fields") or []:
                    print(f"      {sub['name']}: {named(sub['type'])[0]}")
        return
    print(f"{len(fields)} query fields. Ones mentioning matchup / lineup / lock / roster:")
    for f in fields:
        if re.search(r"matchup|lineup|lock|roster|leg", f["name"], re.I):
            argtxt = ", ".join(f"{a['name']}:{named(a['type'])[0]}" for a in f["args"])
            print(f"  {f['name']}({argtxt}) -> {named(f['type'])[0]}")

    print("\nFields of the types those queries return (lock-looking names marked *):")
    shown = set()
    for f in fields:
        if not re.search(r"matchup|lineup|lock", f["name"], re.I):
            continue
        tname = named(f["type"])[0]
        if not tname or tname in shown:
            continue
        shown.add(tname)
        print(f"  type {tname}")
        for sub in type_fields(tname).get("fields") or []:
            mark = "*" if SUSPECT.search(sub["name"]) else " "
            print(f"    {mark} {sub['name']}: {named(sub['type'])[0]}")

    target = next((f for f in fields if f["name"] == "matchup_legs"), None)
    if not target:
        print("\nNo matchup_legs query found; send this output back and I will pick the right read.")
        return
    argnames = {a["name"]: a for a in target["args"]}
    variables = {}
    if "league_id" in argnames:
        variables["league_id"] = str(league)
    for key in ("round", "leg", "week"):
        if key in argnames:
            variables[key] = week
    decl = ", ".join(f"${k}: {'String!' if k == 'league_id' else 'Int!'}" for k in variables)
    call = ", ".join(f"{k}: ${k}" for k in variables)
    sel = selection(named(target["type"])[0], depth=2)
    query = f"query probe({decl}) {{ matchup_legs({call}) {{ {sel} }} }}"
    print(f"\nrunning matchup_legs with {variables}")
    result = post(query, variables)
    if result.get("errors"):
        print("errors:", json.dumps(result["errors"], indent=1)[:1500])
    data = (result.get("data") or {}).get("matchup_legs")
    if not data:
        print("no data returned")
        return
    rows = data if isinstance(data, list) else [data]
    mine = [r for r in rows if r.get("roster_id") == args.roster_id] or rows[:1]
    print(f"\n{len(rows)} matchup row(s). Your roster ({args.roster_id}):")
    print(json.dumps(mine[0], indent=1, default=str)[:6000])
    print("\nEvery key in the response whose name looks like a lock/start/game/status:")
    for p, v in walk(rows):
        print(f"  {p} = {json.dumps(v, default=str)[:160]}")


if __name__ == "__main__":
    main()
