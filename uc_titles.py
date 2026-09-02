"""Shared constants and helpers for the title-category files.

Used by categorize-titles.py (builds the categories), sync-title-sheet.py
(round-trips the Google Sheet) and build-db.py (loads the CSVs into SQLite).
"""

import csv
import os
import sqlite3

DATABASE = "data/uc_salaries.db"
TITLE_CATEGORIES_CSV = "data/title_categories.csv"
TITLE_CROSSWALK_CSV = "data/title_crosswalk.csv"
UCOP_ACADEMIC_CSV = "data/reference/ucop_academic_titles.csv"
SHEET_DIR = "data/sheet"

# The Google Sheet referenced in notebook.ipynb.
SHEET_ID = "19MG1bKoQh6o6PAAYHAmdFyNpNspQ3tfQ6jpMbou_rw8"
SHEET_TABS = ["README", "CATEGORIES", "All UC Title Codes"]

# ---------------------------------------------------------------------------
# Category names.  Primary categories are deliberately coarse except for the
# faculty groups, which are what the higher-ed cost analysis is about.
# ---------------------------------------------------------------------------
SMG = "Senior Management Group"
SEN = "Senate Faculty"                 # UC "Ladder-rank and equivalent"
NONSEN = "Non-Senate Faculty"          # UC "Clinical / In-Residence / Adjunct"
LECT = "Lecturers (Unit 18)"           # UC "Faculty - Lecturers"
ACRES = "Academic Research"            # postdocs, project scientists, researchers, specialists
OTHACAD = "Other Academic"             # librarians, coop extension, coordinators, UNEX ...
ACADMIN = "Academic Administration"    # deans, provosts, chairs, academic directors
MGMT = "Staff - Management"            # MSP managers, directors, AVC/AVP-level staff
PROF = "Staff - Professional & Supervisory"
SUPPORT = "Staff - Support & Operations"
MED = "Health Care (Med Center & Clinical)"
ATH = "Athletics"
STINS = "Student Instruction"          # TAs, readers, tutors, associates-in
STRES = "Student Research"             # GSRs
STU = "Students"                       # student assistants and other student jobs
OTHER = "Other / Uncategorized"

PRIMARY_CATEGORIES = [SEN, NONSEN, LECT, ACRES, OTHACAD, ACADMIN, SMG, MGMT,
                      PROF, SUPPORT, MED, ATH, STINS, STRES, STU, OTHER]

# Categories used in the original Google Sheet (see notebook.ipynb cell 4)
# and which new primary categories they are allowed to map to.  A row whose
# original category is set but whose new category is outside this list is
# flagged needs_review = Y.
ORIGINAL_TO_NEW = {
    "Senate": [SEN],
    "Teaching Track": [SEN],          # LSOE / PSOE are Senate members
    "Non-Senate": [NONSEN, OTHACAD, LECT],
    "Unit 18": [LECT],
    "Research": [ACRES, PROF],        # sheet counted SRAs as Research
    "Research Staff": [PROF, SUPPORT, MGMT, ACRES],
    "Acad Staff": [OTHACAD, ACADMIN, ACRES, PROF, MGMT, SUPPORT, STINS],  # "not a clear boundary" per the sheet README
    "Admin": [ACADMIN, SMG, MGMT, OTHACAD],
    "Admin Staff": [MGMT, PROF, SUPPORT, ACADMIN],
    "Staff": [MGMT, PROF, SUPPORT, MED, OTHER],  # "all staff roles ... I can't classify" per the README
    "Med Center": [MED, SMG],
    "Athletics": [ATH],
    "Coach": [ATH],
    "Students": [STU, STINS, STRES],
    "Student Instruction": [STINS],
    "Student Reasearch": [STRES],     # sic - spelled this way in the sheet
    "Student Research": [STRES],
}

# Column order of data/title_categories.csv.
CATEGORY_COLUMNS = [
    "title", "category", "secondary_category", "source", "rule_id", "confidence",
    "original_category", "needs_review", "review_notes", "notes",
    "is_health", "uc_cto", "uc_cto_name", "uc_academic_group",
    "family", "level", "successor_title", "next_title",
    "n_all", "n_2025", "n_2020", "n_2010", "first_year", "last_year",
    "avg_gross_all", "avg_gross_2025",
]

CROSSWALK_COLUMNS = [
    "from_title", "to_title", "relation", "confidence", "n_moves", "share_of_moves",
    "years", "from_last_year", "to_first_year", "from_family", "from_level",
    "to_family", "to_level", "source",
]

NUMERIC_COLUMNS = {"n_all", "n_2025", "n_2020", "n_2010", "first_year", "last_year",
                   "level", "avg_gross_all", "avg_gross_2025", "n_moves",
                   "share_of_moves", "from_last_year", "to_first_year",
                   "from_level", "to_level"}


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, columns):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in columns})


def _sql_type(col):
    if col in ("share_of_moves", "avg_gross_all", "avg_gross_2025"):
        return "REAL"
    return "INTEGER" if col in NUMERIC_COLUMNS else "TEXT"


def load_csv_table(conn, path, table, columns, key=None):
    """(Re)create `table` from the CSV at `path`. Returns row count."""
    rows = read_csv(path)
    cur = conn.cursor()
    cur.execute(f"DROP TABLE IF EXISTS {table}")
    cols = ", ".join(f'"{c}" {_sql_type(c)}' for c in columns)
    cur.execute(f"CREATE TABLE {table} ({cols})")
    placeholders = ", ".join("?" for _ in columns)
    quoted = ", ".join(f'"{c}"' for c in columns)

    def val(r, c):
        v = r.get(c, "")
        if v == "" or v is None:
            return None
        if c in NUMERIC_COLUMNS:
            try:
                return float(v) if _sql_type(c) == "REAL" else int(float(v))
            except ValueError:
                return None
        return v

    cur.executemany(f"INSERT INTO {table} ({quoted}) VALUES ({placeholders})",
                    [[val(r, c) for c in columns] for r in rows])
    if key:
        cur.execute(f'CREATE INDEX IF NOT EXISTS idx_{table}_{key} ON {table}("{key}")')
    conn.commit()
    return len(rows)


def load_title_tables(conn, categories_csv=TITLE_CATEGORIES_CSV,
                      crosswalk_csv=TITLE_CROSSWALK_CSV, quiet=False):
    """Load the two title CSVs into SQLite and create the joined view."""
    loaded = {}
    if os.path.exists(categories_csv):
        loaded["title_categories"] = load_csv_table(
            conn, categories_csv, "title_categories", CATEGORY_COLUMNS, key="title")
    if os.path.exists(crosswalk_csv):
        loaded["title_crosswalk"] = load_csv_table(
            conn, crosswalk_csv, "title_crosswalk", CROSSWALK_COLUMNS, key="from_title")
    if "title_categories" in loaded:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_salaries_title ON salaries(title)")
        conn.execute("DROP VIEW IF EXISTS salaries_categorized")
        conn.execute("""
            CREATE VIEW salaries_categorized AS
            SELECT s.*, c.category, c.secondary_category, c.is_health,
                   c.uc_academic_group, c.family, c.level, c.successor_title
            FROM salaries s LEFT JOIN title_categories c ON c.title = s.title""")
        conn.commit()
    if not quiet:
        for table, n in loaded.items():
            print(f"  loaded {n} rows into {table}")
        if "title_categories" in loaded:
            print("  created view salaries_categorized (salaries JOIN title_categories)")
    return loaded


def connect(path=DATABASE):
    if not os.path.exists(path):
        raise SystemExit(f"{path} not found - run `python3 build-db.py` first")
    return sqlite3.connect(path)
