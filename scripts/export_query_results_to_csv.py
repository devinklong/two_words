"""
Runs every numbered query section in a .sql file (sections delimited by
the project's existing `-- =========================` / `-- N. Title`
comment convention, the same pattern used throughout schema/*.sql's
"Verification" blocks) and saves each section's result set to its own
CSV file — so results can be shared by uploading CSV files instead of
screenshotting every result panel in the GUI.

Run from the project root:
    python scripts/export_query_results_to_csv.py <path/to/file.sql> [--outdir DIR]

Example:
    python scripts/export_query_results_to_csv.py tests/lock_signal_validation.sql

Each section becomes <outdir>/<sql_filename>_section<N>_<title>.csv.
outdir defaults to <sql file's directory>/output (e.g.
tests/output/ for a file in tests/).

LIMITATION: expects the file to follow the section-divider convention
already used across schema/*.sql — a `-- ===...===` divider line, a
`-- N. Title` line, another `-- ===...===` divider line, then one SQL
statement. Files without that structure won't split into sections; run
the query manually or add the divider comments first.
"""

import argparse
import re
import sys
from pathlib import Path

import pandas as pd

from db_connection import get_connection

SECTION_PATTERN = re.compile(
    r'--\s*=+\s*\n'                # opening divider line
    r'--\s*(\d+)\.\s*([^\n]+)\n'   # "-- N. Title" (first line of the title)
    r'(?:--[^\n]*\n)*'              # any wrapped continuation comment lines
    r'--\s*=+\s*\n'                 # closing divider line
    r'\n*'
    r'(.*?)'                        # the SQL body (non-greedy)
    r'(?=\n--\s*=+\s*\n--\s*\d+\.|\Z)',  # up to the next section or EOF
    re.DOTALL,
)


def split_sections(sql_text: str):
    matches = SECTION_PATTERN.findall(sql_text)
    return [(int(num), title.strip(), body.strip()) for num, title, body in matches if body.strip()]


def export_sections(sql_path: Path, outdir: Path):
    sql_text = sql_path.read_text()
    sections = split_sections(sql_text)

    if not sections:
        print(f"No numbered `-- N. Title` sections found in {sql_path} — nothing to export.")
        print("(Expected the project's `-- =========================` / `-- N. Title` convention.)")
        return

    outdir.mkdir(parents=True, exist_ok=True)
    conn = get_connection()

    for num, title, body in sections:
        safe_title = re.sub(r'[^a-zA-Z0-9]+', '_', title).strip('_').lower()[:50]
        out_path = outdir / f"{sql_path.stem}_section{num}_{safe_title}.csv"
        try:
            df = pd.read_sql(body, conn)
            df.to_csv(out_path, index=False)
            print(f"Section {num} ({title}): {len(df)} rows -> {out_path}")
        except Exception as e:
            print(f"Section {num} ({title}): FAILED — {e}")

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Export each numbered section of a .sql file's results to CSV.")
    parser.add_argument("sql_file", help="Path to the .sql file, e.g. tests/lock_signal_validation.sql")
    parser.add_argument("--outdir", default=None, help="Output directory (default: <sql file's dir>/output)")
    args = parser.parse_args()

    sql_path = Path(args.sql_file)
    if not sql_path.exists():
        print(f"File not found: {sql_path}")
        sys.exit(1)

    outdir = Path(args.outdir) if args.outdir else sql_path.parent / "output"
    export_sections(sql_path, outdir)


if __name__ == "__main__":
    main()
