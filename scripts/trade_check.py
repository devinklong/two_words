"""Price a trade by its effect on each team's best weekly lineup (per-game value, same math as the bid advisor).

Each player's value is his spike level (avg + 1.25*sd; --value avg for plain average), movers discounted like the advisor.
A team's lineup value = the best total over the 9 starting slots (positions respected, taxi excluded). A trade is
worth it for you when YOUR lineup value rises by more than you pay in picks/FAAB; it only gets accepted when the
other team's lineup value does not fall much. Picks and FAAB are NOT modeled: this gives the player-for-player part.

Usage (from ~/projects/two_words):
    python scripts/trade_check.py --partner 2 --list                              (both rosters with values + lineup totals)
    python scripts/trade_check.py --partner 2 --get Jokic --give "Zubac,Bane"     (one proposed trade)
    python scripts/trade_check.py --block Giannis                                 (trading block: every team's gain and best 1-for-1 return)
"""
import argparse
import unicodedata


def fold(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c)).lower()


def pick(players, text):
    """Players on a roster whose folded name contains each comma-separated fragment (exactly one each, else error)."""
    out = []
    for frag in [f.strip() for f in text.split(",") if f.strip()]:
        hits = [p for p in players if fold(frag) in fold(p["name"])]
        if len(hits) != 1:
            raise SystemExit(f"'{frag}' matched {len(hits)} players ({', '.join(p['name'] for p in hits) or 'none'}).")
        out.append(hits[0])
    return out


def evaluate(mine, theirs, get, give):
    """Lineup value before/after for both sides. mine/theirs: lists of {name, v, pos}; get from theirs, give from mine."""
    from lineup_fit import best_lineup_value
    L = lambda ps: best_lineup_value([(p["v"], p["pos"]) for p in ps])
    my_after = [p for p in mine if p not in give] + get
    th_after = [p for p in theirs if p not in get] + give
    return L(mine), L(my_after), L(theirs), L(th_after)


def block(teams, mine, args):
    """Shop one of your players to every team: what each team's best lineup gains from him, and the best 1-for-1 return for you."""
    from lineup_fit import best_lineup_value
    L = lambda ps: best_lineup_value([(p["v"], p["pos"]) for p in ps])
    star = pick(mine, args.block)[0]
    m0 = L(mine)
    m_without = L([p for p in mine if p is not star])
    print(f"\n{star['name']}: value {star['v']:.1f}; your best lineup {m0:.1f} -> {m_without:.1f} without him ({m_without - m0:+.1f}). Reserve: a return must lift you back above {m_without:.1f} by at least {m0 - m_without:.1f}.")
    print(f"\n{'team':<4} {'owner':<16} {'their gain':>10}   best 1-for-1 return (they must not lose lineup value)")
    rows = []
    for rid, t in teams.items():
        if rid == args.roster_id:
            continue
        theirs = t["players"]
        t0 = L(theirs)
        gain = L(theirs + [star]) - t0
        best = None
        for q in theirs:
            my1 = L([p for p in mine if p is not star] + [q])
            th1 = L([p for p in theirs if p is not q] + [star])
            if th1 - t0 >= 0 and (best is None or my1 > best[0]):
                best = (my1, q, th1 - t0)
        rows.append((gain, rid, t["owner"], best))
    for gain, rid, owner, best in sorted(rows, key=lambda r: -r[0]):
        ret = f"{best[1]['name']} ({best[1]['v']:.1f}): you {best[0] - m0:+.1f} vs now, they {best[2]:+.1f}" if best else "none that leaves them at least even"
        print(f"{rid:<4} {str(owner)[:16]:<16} {gain:>+10.1f}   {ret}")
    print("\nOpen at the top team's best return, then add a second player or pick. Picks and FAAB are not modeled.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--partner", type=int, help="roster_id you are trading with (found from --get names if omitted)")
    ap.add_argument("--block", help="name fragment of one of your players to shop to every team")
    ap.add_argument("--roster-id", type=int, default=8)
    ap.add_argument("--get", help="comma-separated name fragments from the partner's roster")
    ap.add_argument("--give", help="comma-separated name fragments from your roster")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--value", choices=["avg", "spike"], default="spike")
    ap.add_argument("--mover-factor", type=float, default=0.90)
    ap.add_argument("--season-id", default="22026")
    args = ap.parse_args()

    from db_connection import get_connection
    from waiver_bid_advisor import fetch_fit_inputs, fetch_movers, fetch_taxi, ROSTERS_SQL
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT league_id FROM sleeper_current_league")
    league = cur.fetchone()[0]
    movers = fetch_movers(conn, args.season_id)
    _rosters, positions, values = fetch_fit_inputs(conn, league, args.season_id, args.value, movers, args.mover_factor)
    taxi = fetch_taxi(conn, league)
    cur.execute(ROSTERS_SQL, {"league": league})
    teams = {}
    for rid, owner, sid, nba, name in cur.fetchall():
        if sid in taxi.get(rid, ()):
            continue
        teams.setdefault(rid, {"owner": owner, "players": []})["players"].append(
            {"name": name or f"(unmatched, sleeper id {sid})", "v": values.get(nba, 0.0), "pos": positions.get(sid, [])})
    conn.close()
    mine = teams[args.roster_id]["players"]
    if args.block:
        block(teams, mine, args)
        return
    if not args.partner:
        if not args.get:
            raise SystemExit("give --partner, or --get with player names so the owner can be found")
        frags = [f.strip() for f in args.get.split(",") if f.strip()]
        owners = {rid: [f for f in frags if any(fold(f) in fold(p["name"]) for p in t["players"])]
                  for rid, t in teams.items() if rid != args.roster_id}
        full = [rid for rid, hit in owners.items() if len(hit) == len(frags)]
        if len(full) != 1:
            for rid, hit in sorted(owners.items()):
                if hit:
                    print(f"roster {rid} ({teams[rid]['owner']}) has: {', '.join(hit)}")
            raise SystemExit("no single team holds all of those players; pass --partner and ask for one team's players at a time")
        args.partner = full[0]
        print(f"found them on roster {args.partner} ({teams[args.partner]['owner']})")
    theirs = teams[args.partner]["players"]

    if args.list or not (args.get and args.give):
        from lineup_fit import best_lineup_value
        for rid in (args.roster_id, args.partner):
            ps = sorted(teams[rid]["players"], key=lambda p: -p["v"])
            tot = best_lineup_value([(p["v"], p["pos"]) for p in ps])
            print(f"\nRoster {rid} ({teams[rid]['owner']}): best lineup {tot:.1f} per game ({args.value} basis)")
            for p in ps:
                print(f"  {p['v']:>6.1f}  {p['name']:<24} {'/'.join(p['pos']) or '-'}")
        return

    get, give = pick(theirs, args.get), pick(mine, args.give)
    m0, m1, t0, t1 = evaluate(mine, theirs, get, give)
    print(f"\nYou get : {', '.join(p['name'] for p in get)}")
    print(f"You give: {', '.join(p['name'] for p in give)}")
    print(f"Your best lineup   : {m0:.1f} -> {m1:.1f}  ({m1 - m0:+.1f} per game, {args.value} basis)")
    print(f"Their best lineup  : {t0:.1f} -> {t1:.1f}  ({t1 - t0:+.1f} per game)")
    print(f"Net swing for you vs them: {(m1 - m0) - (t1 - t0):+.1f}")
    if t1 - t0 < -2:
        print("They lose lineup value in this trade, so expect to add picks/FAAB or give a better fit.")
    if m1 - m0 <= 0:
        print("This does not improve your best lineup; only worth it if the extra bench depth or injuries matter to you.")
    print("Picks and FAAB are not modeled here.")


if __name__ == "__main__":
    main()
