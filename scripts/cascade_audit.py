"""Audit every DROP ... CASCADE in the repo against the live database. Read-only.

For each DROP VIEW / MATERIALIZED VIEW / TABLE / FUNCTION statement that ends in CASCADE (in models/, schema/ and scripts/ .sql files, including
EXECUTE 'DROP ...' strings), this finds the object in the database and lists everything that would be dropped with it: views and materialized
views built on it, transitively (depth 1 = built directly on it), and tables whose foreign keys point at it (only the constraint is dropped;
no data goes).
  DATA     a TABLE is dropped: its rows are gone unless the same file recreates them from somewhere else
  SILENT   other views would vanish with it; the file does not rebuild them
  clean    nothing depends on it, CASCADE is not doing anything and can be removed
  absent   object not in the database (an old name or never created)
Fix order: DATA first, then SILENT objects, then clean.

Usage (from ~/projects/two_words):  python scripts/cascade_audit.py [--only <text in the path>]
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[1]
DROP_RE = re.compile(r"DROP\s+(MATERIALIZED\s+VIEW|VIEW|TABLE|FUNCTION)\s+(?:IF\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_.]*)", re.I)

REL_DEPENDENTS = """
WITH RECURSIVE dep(oid, name, kind, depth) AS (
    SELECT c.oid, c.relname::text, c.relkind::text, 0 FROM pg_class c WHERE c.relname = %(n)s AND c.relnamespace = 'public'::regnamespace
    UNION
    SELECT dc.oid, dc.relname::text, dc.relkind::text, dep.depth + 1 FROM dep
      JOIN pg_depend d ON d.refobjid = dep.oid AND d.classid = 'pg_rewrite'::regclass
      JOIN pg_rewrite r ON r.oid = d.objid
      JOIN pg_class dc ON dc.oid = r.ev_class
     WHERE dc.oid <> dep.oid)
SELECT name, kind, depth FROM dep WHERE depth > 0 ORDER BY depth, name"""
FK_REFERENCERS = """
SELECT DISTINCT fc.relname::text FROM pg_constraint k JOIN pg_class fc ON fc.oid = k.conrelid
WHERE k.contype = 'f' AND k.confrelid = (SELECT oid FROM pg_class WHERE relname = %(n)s AND relnamespace = 'public'::regnamespace)"""
FUNC_DEPENDENTS = """
SELECT DISTINCT dc.relname::text, dc.relkind::text FROM pg_proc p
  JOIN pg_depend d ON d.refobjid = p.oid AND d.classid = 'pg_rewrite'::regclass
  JOIN pg_rewrite r ON r.oid = d.objid JOIN pg_class dc ON dc.oid = r.ev_class
WHERE p.proname = %(n)s AND p.pronamespace = 'public'::regnamespace"""
KIND = {"v": "view", "m": "matview", "r": "table", "p": "table"}


def find_drops(only):
    hits = []
    for base in ("models", "schema", "scripts"):
        for path in sorted((ROOT / base).rglob("*.sql")):
            rel = str(path.relative_to(ROOT))
            if only and only not in rel:
                continue
            for i, line in enumerate(path.read_text().splitlines(), 1):
                if line.lstrip().startswith("--") or "CASCADE" not in line.upper():
                    continue
                m = DROP_RE.search(line)
                if m:
                    hits.append((rel, i, m.group(1).upper().split()[0] if "MATERIALIZED" not in m.group(1).upper() else "MATVIEW", m.group(2)))
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only")
    args = ap.parse_args()
    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SET TRANSACTION READ ONLY")
    results = []
    for rel, line, kind, name in find_drops(args.only):
        name = name.split(".")[-1]
        deps, fks, exists = [], [], True
        if kind == "FUNCTION":
            cur.execute("SELECT 1 FROM pg_proc WHERE proname = %(n)s AND pronamespace = 'public'::regnamespace", {"n": name})
            exists = cur.fetchone() is not None
            if exists:
                cur.execute(FUNC_DEPENDENTS, {"n": name})
                deps = [(n, KIND.get(k, k), 1) for n, k in cur.fetchall()]
        else:
            cur.execute("SELECT 1 FROM pg_class WHERE relname = %(n)s AND relnamespace = 'public'::regnamespace", {"n": name})
            exists = cur.fetchone() is not None
            if exists:
                cur.execute(REL_DEPENDENTS, {"n": name})
                deps = [(n, KIND.get(k, k), d) for n, k, d in cur.fetchall()]
                if kind == "TABLE":
                    cur.execute(FK_REFERENCERS, {"n": name})
                    fks = [r[0] for r in cur.fetchall()]
        label = "absent" if not exists else "DATA" if kind == "TABLE" else "SILENT" if deps else "clean"
        results.append((label, rel, line, kind, name, deps, fks))
    cur.close(); conn.close()

    order = {"DATA": 0, "SILENT": 1, "clean": 2, "absent": 3}
    for label, rel, line, kind, name, deps, fks in sorted(results, key=lambda r: (order[r[0]], r[1], r[2])):
        print(f"{label:<7} {rel}:{line}  DROP {kind} {name}")
        for n, k, d in deps:
            print(f"          takes down ({'directly' if d == 1 else 'via depth ' + str(d)}): {k} {n}")
        for f in fks:
            print(f"          foreign key from table {f} (constraint only)")
    counts = {k: sum(1 for r in results if r[0] == k) for k in order}
    print(f"\n{len(results)} CASCADE drops: " + ", ".join(f"{v} {k}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
