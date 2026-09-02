#!/usr/bin/env python3
"""Download a full year of UC salary data from ucannualwage.ucop.edu.

The site replaced the old GET `search.do` endpoint (which accepted an
arbitrary `rows` value) with a JSON POST to `/wage/search`. The new endpoint
only honors the page sizes offered by the UI dropdown -- 20, 40 and 60 --
and silently falls back to 20 for anything else, so a full year has to be
depaginated 60 rows at a time. `location=ALL` now returns 0 records, so each
campus must be queried separately.

Output is written as data/json/all-records-<year>.json.gz in the same shape
as the existing files, so database.ipynb's load_data_for_year() keeps working:
a JSON list of {"id": n, "cell": [n, year, location, first, last, title,
gross, regular, overtime, other]}.
"""

import argparse
import gzip
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# DANR is in the location dropdown but has had no records since 2013.
CAMPUSES = [
    "ASUCLA", "Berkeley", "Davis", "UC SF Law", "Irvine",
    "Los Angeles", "Merced", "Riverside", "San Diego",
    "San Francisco", "Santa Barbara", "Santa Cruz", "UCOP"
]

SEARCH_URL = "https://ucannualwage.ucop.edu/wage/search"
# The endpoint caps out at 60; anything larger silently returns 20.
PAGE_SIZE = 60
MAX_RETRIES = 5
# Column order used by every previously committed year.
COLUMNS = ["grosspay", "basepay", "overtimepay", "adjustpay"]

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
    "Referer": "https://ucannualwage.ucop.edu/wage/",
    "Origin": "https://ucannualwage.ucop.edu",
    "X-Requested-With": "XMLHttpRequest",
}

_print_lock = threading.Lock()


def log(message):
    with _print_lock:
        print(message, flush=True)


def fetch_page(year, campus, page, delay):
    payload = {
        "op": "search", "page": page, "rows": PAGE_SIZE,
        "sidx": "lastname", "sord": "asc", "count": 0,
        "year": str(year), "firstname": "", "location": campus,
        "lastname": "", "title": "", "startSal": "", "endSal": "",
    }
    data = json.dumps(payload).encode("utf-8")
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(SEARCH_URL, data=data,
                                         headers=HEADERS, method="POST")
            with urllib.request.urlopen(req, timeout=120) as response:
                result = json.loads(response.read().decode("utf-8"))
            time.sleep(delay)
            return result
        except Exception as err:  # noqa: BLE001 - retry anything transient
            if attempt == MAX_RETRIES:
                raise RuntimeError(
                    f"{campus} page {page} failed after {MAX_RETRIES} attempts: {err}")
            wait = 2 * attempt
            log(f"  ! {campus} page {page} attempt {attempt} failed ({err});"
                f" retrying in {wait}s")
            time.sleep(wait)


def parse_pay(value):
    """New API returns '32,706.00'; every stored year uses '32706.00'."""
    return (value or "0.00").replace(",", "").strip()


def to_cell(row):
    return [
        row["year"], row["location"], row["firstname"], row["lastname"],
        row["title"], *[parse_pay(row[c]) for c in COLUMNS],
    ]


def download_year(year, delay, workers):
    # Page 1 of each campus tells us how many pages to expect.
    first_pages = {}
    for campus in CAMPUSES:
        data = fetch_page(year, campus, 1, delay)
        first_pages[campus] = data
        log(f"{campus:16s} records={int(data['records']):>7}"
            f" pages={int(data['total'])}")
        if int(data.get("pageSize", 0)) != PAGE_SIZE and int(data["records"]):
            log(f"  ! {campus}: server used pageSize={data.get('pageSize')}")

    tasks = [(campus, page)
             for campus in CAMPUSES
             for page in range(2, int(first_pages[campus]["total"]) + 1)]
    total_requests = len(tasks) + len(CAMPUSES)
    log(f"\nFetching {total_requests} pages with {workers} workers...")

    pages = {(campus, 1): first_pages[campus]["rows"] for campus in CAMPUSES}
    done = [0]

    def work(task):
        campus, page = task
        rows = fetch_page(year, campus, page, delay)["rows"]
        with _print_lock:
            done[0] += 1
            if done[0] % 250 == 0 or done[0] == len(tasks):
                print(f"  {done[0]}/{len(tasks)} pages", flush=True)
        return task, rows

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for task, rows in pool.map(work, tasks):
            pages[task] = rows

    records = []
    ok = True
    for campus in CAMPUSES:
        expected = int(first_pages[campus]["records"])
        campus_rows = []
        for page in range(1, int(first_pages[campus]["total"]) + 1):
            campus_rows.extend(pages[(campus, page)])
        if len(campus_rows) != expected:
            log(f"  ! {campus}: got {len(campus_rows)} rows,"
                f" server reported {expected}")
            ok = False
        records.extend(campus_rows)

    return records, ok


def write_gzipped_json(records, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # `id` is just a row number, renumbered 1..N as in every prior year.
    out = [{"id": i, "cell": [i, *to_cell(row)]}
           for i, row in enumerate(records, start=1)]
    # mtime=0 keeps output byte-identical across runs of the same data.
    with gzip.GzipFile(path, "wb", compresslevel=9, mtime=0) as gz:
        gz.write(json.dumps(out, indent=4).encode("utf-8"))
    return len(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--delay", type=float, default=0.25,
                        help="seconds to wait after each request, per worker")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    records, ok = download_year(args.year, args.delay, args.workers)
    if not records:
        print(f"No records returned for {args.year}", file=sys.stderr)
        return 1

    out = args.out or f"data/json/all-records-{args.year}.json.gz"
    count = write_gzipped_json(records, out)
    print(f"\nWrote {count} records for {args.year} to {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
