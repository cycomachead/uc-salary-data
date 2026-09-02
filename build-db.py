#!/usr/bin/env python3
"""Build/refresh the SQLite salaries DB from the committed data/json files.

The DB itself is gitignored (too large to commit), so this rebuilds it from
the year files that are in the repo. Loading a year that is already present
replaces it, so the script is safe to re-run and can top up a single year:

    python3 build-db.py                # every year found in data/json
    python3 build-db.py --years 2024 2025

Schema matches the one in database.ipynb: the per-year `id` from the source
data is dropped (it is only a row number) and SQLite assigns the primary key.
"""

import argparse
import glob
import gzip
import json
import os
import re
import sqlite3
import sys

DATABASE = "data/uc_salaries.db"
JSON_DIR = "data/json"

SCHEMA = """CREATE TABLE IF NOT EXISTS salaries
             (id INTEGER PRIMARY KEY, year INTEGER, location TEXT,
              first_name TEXT, last_name TEXT, title TEXT,
              gross_pay REAL, regular_pay REAL, overtime_pay REAL, other_pay REAL)"""


def available_years():
    years = []
    for path in glob.glob(os.path.join(JSON_DIR, "all-records-*.json.gz")):
        match = re.search(r"all-records-(\d{4})\.json\.gz$", path)
        if match:
            years.append(int(match.group(1)))
    return sorted(years)


def load_rows(year):
    """Return the raw `cell` lists for a year, across both file formats."""
    path = os.path.join(JSON_DIR, f"all-records-{year}.json.gz")
    raw = gzip.open(path).read().decode("utf-8", "replace")
    # Years before 2022 were saved straight from curl: single-quoted and with
    # a newline after every row, so they are not valid JSON as-is.
    if year < 2022:
        raw = raw.replace("'", '"')
    raw = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", raw)
    parsed = json.loads(raw)
    # Pre-2022 files keep the whole API response; later ones store only `rows`.
    if isinstance(parsed, dict):
        parsed = parsed["rows"]
    return [row["cell"] for row in parsed]


def to_number(value):
    """Pay fields are strings; blank/garbage becomes NULL rather than 0."""
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def to_record(cell):
    # cell = [id, year, location, first, last, title, gross, reg, ot, other]
    _, year, location, first, last, title = cell[:6]
    return (int(year), location, first, last, title,
            *[to_number(v) for v in cell[6:10]])


def load_year(conn, year):
    rows = load_rows(year)
    records = [to_record(cell) for cell in rows]
    cursor = conn.cursor()
    # Replace rather than append so re-running does not duplicate a year.
    existing = cursor.execute(
        "SELECT COUNT(*) FROM salaries WHERE year = ?", (year,)).fetchone()[0]
    if existing:
        print(f"  replacing {existing} existing rows for {year}")
        cursor.execute("DELETE FROM salaries WHERE year = ?", (year,))
    cursor.executemany(
        """INSERT INTO salaries
           (year, location, first_name, last_name, title,
            gross_pay, regular_pay, overtime_pay, other_pay)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", records)
    conn.commit()
    return len(records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, nargs="+", default=None)
    parser.add_argument("--db", default=DATABASE)
    args = parser.parse_args()

    years = args.years or available_years()
    missing = [y for y in years
               if not os.path.exists(os.path.join(JSON_DIR, f"all-records-{y}.json.gz"))]
    if missing:
        print(f"No data file for: {missing}", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(args.db) or ".", exist_ok=True)
    conn = sqlite3.connect(args.db)
    conn.execute(SCHEMA)

    for year in years:
        print(f"Loading {year}...")
        print(f"  inserted {load_year(conn, year)} rows")

    print("\nRows per year:")
    for year, count, gross in conn.execute(
            "SELECT year, COUNT(*), SUM(gross_pay) FROM salaries "
            "GROUP BY year ORDER BY year"):
        print(f"  {year}  {count:>7}  ${gross:>15,.0f}")
    total = conn.execute("SELECT COUNT(*) FROM salaries").fetchone()[0]
    conn.close()
    print(f"\n{total} total rows in {args.db} "
          f"({os.path.getsize(args.db) / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
