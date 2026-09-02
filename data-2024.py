#!/usr/bin/env python3
import urllib.request
import urllib.parse
import json
import time
import os
import sqlite3
from pathlib import Path

DATABASE = 'data/uc_salaries.db'

CAMPUSES = [
    "ASUCLA", "Berkeley", "Davis", "UC SF Law", "Irvine",
    "Los Angeles", "Merced", "Riverside", "San Diego",
    "San Francisco", "Santa Barbara", "Santa Cruz", "UCOP"
]

YEAR = "2024"
PAGE_SIZE = 60
WAIT_TIME = 0.5
PROGRESS_FILE = "download_progress.json"

def init_database():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS salaries
                 (id INTEGER PRIMARY KEY, year INTEGER, location TEXT,
                  first_name TEXT, last_name TEXT, title TEXT,
                  gross_pay REAL, regular_pay REAL, overtime_pay REAL, other_pay REAL)''')
    conn.commit()
    return conn

def parse_pay_value(pay_str):
    """Convert pay string like '35,385.00' to float"""
    if not pay_str or pay_str == "":
        return 0.0
    return float(pay_str.replace(',', ''))

def insert_records(conn, records):
    cursor = conn.cursor()
    for record in records:
        cursor.execute('''INSERT INTO salaries
                         (year, location, first_name, last_name, title,
                          gross_pay, regular_pay, overtime_pay, other_pay)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                      (int(record['year']),
                       record['location'],
                       record['firstname'],
                       record['lastname'],
                       record['title'],
                       parse_pay_value(record['grosspay']),
                       parse_pay_value(record['basepay']),
                       parse_pay_value(record['overtimepay']),
                       parse_pay_value(record['adjustpay'])))
    conn.commit()

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_progress(progress):
    with open(PROGRESS_FILE, 'w') as f:
        json.dump(progress, f, indent=2)

def fetch_page(campus, page):
    url = "https://ucannualwage.ucop.edu/wage/search"

    payload = {
        "op": "search",
        "page": page,
        "rows": PAGE_SIZE,
        "sidx": "lastname",
        "sord": "asc",
        "count": 0,
        "year": YEAR,
        "firstname": "",
        "location": campus,
        "lastname": "",
        "title": "",
        "startSal": "",
        "endSal": ""
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
        "Referer": "https://ucannualwage.ucop.edu/wage/",
        "Origin": "https://ucannualwage.ucop.edu",
        "X-Requested-With": "XMLHttpRequest"
    }

    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method='POST')

    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode('utf-8'))

def download_campus_data(campus, conn):
    print(f"Downloading {campus}...")
    all_records = []
    page = 1

    while True:
        print(f"  Page {page}...", end=' ')
        data = fetch_page(campus, page)

        records = data.get('rows', [])
        total_records = int(data.get('records', 0))

        if not records:
            print("done (no records)")
            break

        all_records.extend(records)
        print(f"{len(records)} records ({len(all_records)}/{total_records})")

        if len(all_records) >= total_records:
            break

        page += 1
        time.sleep(WAIT_TIME)

    return all_records

def main():
    conn = init_database()
    progress = load_progress()
    all_data = {}

    for campus in CAMPUSES:
        if campus in progress:
            print(f"Skipping {campus} (already downloaded)")
            with open(f"{YEAR}-{campus}.json", 'r') as f:
                all_data[campus] = json.load(f)
            continue

        try:
            campus_data = download_campus_data(campus, conn)

            # Save individual campus file
            filename = f"{YEAR}-{campus}.json"
            with open(filename, 'w') as f:
                json.dump(campus_data, f, indent=2)
            print(f"Saved {filename}")

            # Insert into database
            insert_records(conn, campus_data)
            print(f"Inserted {len(campus_data)} records into database\n")

            all_data[campus] = campus_data

            # Update progress
            progress[campus] = True
            save_progress(progress)

        except Exception as e:
            print(f"Error downloading {campus}: {e}\n")
            continue

    # Save combined file
    with open(f"{YEAR}-all.json", 'w') as f:
        json.dump(all_data, f, indent=2)
    print(f"Saved {YEAR}-all.json")

    conn.close()

    # Clean up progress file
    if os.path.exists(PROGRESS_FILE):
        os.remove(PROGRESS_FILE)
    print("Download complete!")

if __name__ == "__main__":
    main()
