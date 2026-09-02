#!/usr/bin/env python3
"""Round-trip data/title_categories.csv with the Google Sheet and SQLite.

The sheet (see uc_titles.SHEET_ID) is still the easiest place for a human to
edit categories, so this keeps the committed CSV and the sheet in step:

    python3 sync-title-sheet.py pull      # download README / CATEGORIES /
                                          # "All UC Title Codes" tabs to data/sheet/
                                          # and merge the sheet's Category column
                                          # into title_categories.csv as original_category
    python3 sync-title-sheet.py merge     # merge an already-downloaded tab (no network)
    python3 sync-title-sheet.py export    # write a sheet-friendly copy to data/sheet/export/
    python3 sync-title-sheet.py push      # upload title_categories.csv to a tab
                                          # (needs gspread + a service-account JSON)
    python3 sync-title-sheet.py load-db   # (re)load both CSVs into data/uc_salaries.db

`pull` uses the public CSV export
(https://docs.google.com/spreadsheets/d/<id>/gviz/tq?tqx=out:csv&sheet=<tab>)
so it only needs the sheet to be link-readable.  `push` needs write access,
which Google only grants through OAuth / a service account, hence gspread.

Merge rules (nothing the human wrote is ever overwritten):
  * sheet Category  -> original_category on the matching title row
  * a sheet title that is not in the CSV is appended with source=original
  * needs_review = Y when the sheet category and the claude category disagree
    (per uc_titles.ORIGINAL_TO_NEW)
  * rows with source=manual are left alone entirely
"""

import argparse
import os
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request

import uc_titles as T


def tab_path(tab):
    slug = tab.lower().replace(" ", "_")
    return os.path.join(T.SHEET_DIR, f"{slug}.csv")


def gviz_url(sheet_id, tab):
    return (f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?"
            f"tqx=out:csv&sheet={urllib.parse.quote(tab)}")


def pull(sheet_id, tabs):
    os.makedirs(T.SHEET_DIR, exist_ok=True)
    ok = True
    for tab in tabs:
        url = gviz_url(sheet_id, tab)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "uc-salary-data sync"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")[:200]
            if e.code == 403 and "network policy" in text.lower():
                print(f"  {tab}: blocked by network policy (403). Allow docs.google.com "
                      "in the project's network settings and re-run, or download the tab "
                      f"as CSV by hand to {tab_path(tab)} and run `merge`.")
            else:
                print(f"  {tab}: HTTP {e.code} {text}")
            ok = False
            continue
        except urllib.error.URLError as e:
            print(f"  {tab}: {e}")
            ok = False
            continue
        if body.lstrip().startswith(b"<"):
            print(f"  {tab}: got HTML instead of CSV - is the sheet shared as 'anyone with the link'?")
            ok = False
            continue
        with open(tab_path(tab), "wb") as f:
            f.write(body)
        print(f"  {tab}: saved {len(body)} bytes to {tab_path(tab)}")
    return ok


def find_col(header, *names):
    lower = {h.lower().strip(): h for h in header}
    for n in names:
        if n.lower() in lower:
            return lower[n.lower()]
    return None


def merge(tab="All UC Title Codes"):
    path = tab_path(tab)
    if not os.path.exists(path):
        print(f"{path} not found - run `pull` first (or download the tab there by hand)")
        return False
    sheet_rows = T.read_csv(path)
    if not sheet_rows:
        print(f"{path} is empty")
        return False
    header = list(sheet_rows[0].keys())
    title_col = find_col(header, "title", "Title")
    cat_col = find_col(header, "category", "Category")
    if not title_col or not cat_col:
        print(f"could not find Title/Category columns in {path}: {header}")
        return False

    rows = T.read_csv(T.TITLE_CATEGORIES_CSV) if os.path.exists(T.TITLE_CATEGORIES_CSV) else []
    by_title = {r["title"]: r for r in rows}
    updated = added = flagged = 0
    for s in sheet_rows:
        title = (s.get(title_col) or "").strip()
        cat = (s.get(cat_col) or "").strip()
        if not title:
            continue
        row = by_title.get(title)
        if row is None:
            row = {c: "" for c in T.CATEGORY_COLUMNS}
            row.update(title=title, source="original", rule_id="ORIGINAL", confidence="high",
                       n_all=0, n_2025=0, n_2020=0, n_2010=0)
            allowed = T.ORIGINAL_TO_NEW.get(cat)
            row["category"] = allowed[0] if allowed else T.OTHER
            row["notes"] = "title from Google Sheet, not present in the salary DB"
            rows.append(row)
            by_title[title] = row
            added += 1
        if row.get("source") == "manual":
            continue
        if row.get("original_category") != cat:
            row["original_category"] = cat
            updated += 1
        allowed = T.ORIGINAL_TO_NEW.get(cat)
        needs = "Y" if (cat and allowed is not None and row.get("category") not in allowed) else ""
        if needs and row.get("needs_review") != "Y":
            flagged += 1
        row["needs_review"] = needs
    rows.sort(key=lambda r: (-int(float(r.get("n_all") or 0)), r["title"]))
    T.write_csv(T.TITLE_CATEGORIES_CSV, rows, T.CATEGORY_COLUMNS)
    print(f"  merged {len(sheet_rows)} sheet rows: {updated} original_category updates, "
          f"{added} titles added, {flagged} newly flagged needs_review")
    return True


def export():
    out_dir = os.path.join(T.SHEET_DIR, "export")
    os.makedirs(out_dir, exist_ok=True)
    for src in (T.TITLE_CATEGORIES_CSV, T.TITLE_CROSSWALK_CSV):
        if os.path.exists(src):
            dst = os.path.join(out_dir, os.path.basename(src))
            shutil.copyfile(src, dst)
            print(f"  {dst}")
    print("Import into the sheet with File > Import > Upload > 'Insert new sheet(s)'.")


def push(sheet_id, tab, credentials):
    try:
        import gspread  # noqa: F401
    except ImportError:
        print("push needs the gspread package: pip install gspread")
        return False
    import gspread
    if credentials:
        gc = gspread.service_account(filename=credentials)
    else:
        gc = gspread.oauth()
    sh = gc.open_by_key(sheet_id)
    rows = T.read_csv(T.TITLE_CATEGORIES_CSV)
    values = [T.CATEGORY_COLUMNS] + [[r.get(c, "") for c in T.CATEGORY_COLUMNS] for r in rows]
    try:
        ws = sh.worksheet(tab)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=tab, rows=len(values) + 10, cols=len(T.CATEGORY_COLUMNS))
    ws.update(values, "A1")
    print(f"  wrote {len(rows)} rows to tab '{tab}'")
    return True


def load_db(db):
    conn = T.connect(db)
    T.load_title_tables(conn)
    conn.close()
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["pull", "merge", "export", "push", "load-db"])
    ap.add_argument("--sheet-id", default=T.SHEET_ID)
    ap.add_argument("--tab", default="All UC Title Codes", help="tab to merge from / push to")
    ap.add_argument("--credentials", help="service-account JSON for push")
    ap.add_argument("--db", default=T.DATABASE)
    args = ap.parse_args()

    if args.command == "pull":
        ok = pull(args.sheet_id, T.SHEET_TABS)
        if os.path.exists(tab_path(args.tab)):
            ok = merge(args.tab) and ok
        return 0 if ok else 1
    if args.command == "merge":
        return 0 if merge(args.tab) else 1
    if args.command == "export":
        export()
        return 0
    if args.command == "push":
        return 0 if push(args.sheet_id, args.tab if args.tab != "All UC Title Codes"
                         else "claude categories", args.credentials) else 1
    if args.command == "load-db":
        return 0 if load_db(args.db) else 1


if __name__ == "__main__":
    sys.exit(main())
