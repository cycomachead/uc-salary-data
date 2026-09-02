#!/usr/bin/env python3
"""Growth of lecturers, teaching professors, Senate faculty and admin roles.

Reads the categorized salary view (build-db.py / categorize-titles.py) and
writes, for UC systemwide and one campus:

  output/growth_<start>_<end>.png   indexed lines (start year = 100) for
                                    people, total gross pay and pay per
                                    person, with California CPI-W dashed
  output/growth_<start>_<end>.csv   the underlying yearly numbers

Health-care titles and health-sciences faculty (is_health = 'Y') are
excluded. "All admin" = Senior Management Group + Academic Administration
+ Staff - Management.

    python3 growth-chart.py                      # 2019-2025, Berkeley
    python3 growth-chart.py --start 2020 --campus "Los Angeles"
"""

import argparse
import csv
import os
import sqlite3

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import uc_titles as T  # noqa: E402

GROUPS = [
    # label, SQL condition on salaries_categorized, colour (validated categorical palette)
    ("Lecturers (Unit 18)", f"category = '{T.LECT}'", "#2a78d6"),
    ("Teaching professors", f"category = '{T.SEN}' AND secondary_category LIKE 'Teaching Professor%'", "#eb6834"),
    ("Senate faculty", f"category = '{T.SEN}' AND secondary_category NOT LIKE 'Teaching Professor%' "
                       "AND secondary_category NOT LIKE 'Recall%' AND secondary_category NOT LIKE 'Emeritus%'", "#1baf7a"),
    ("All admin", f"category IN ('{T.SMG}', '{T.ACADMIN}', '{T.MGMT}')", "#eda100"),
]

# California CPI-W, annual average (from the UC Accountability Report glossary).
CPI_W_CA = {2010: 224.7, 2011: 230.5, 2012: 235.6, 2013: 238.9, 2014: 243.4, 2015: 246.2,
            2016: 251.3, 2017: 258.4, 2018: 267.5, 2019: 270.8, 2020: 275.6, 2021: 288.6,
            2022: 310.4, 2023: 321.2, 2024: 330.7, 2025: 341.0}

SURF, TXT, TXT2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e2"


def fetch(conn, years, campus=None):
    """{location: {group: {'People': {year: n}, 'Pay': {...}, 'PerPerson': {...}}}}"""
    out = {}
    for loc in ["UC systemwide", campus]:
        where_loc = "" if loc == "UC systemwide" else "AND location = ?"
        out[loc] = {}
        for name, cond, _ in GROUPS:
            params = (years[0], years[-1]) + (() if loc == "UC systemwide" else (loc,))
            rows = conn.execute(
                f"SELECT year, COUNT(*), SUM(gross_pay) FROM salaries_categorized "
                f"WHERE year BETWEEN ? AND ? AND is_health = 'N' AND ({cond}) {where_loc} "
                f"GROUP BY year", params).fetchall()
            n = {y: a for y, a, b in rows}
            p = {y: b for y, a, b in rows}
            out[loc][name] = {"People": n, "Pay": p,
                              "PerPerson": {y: p[y] / n[y] for y in years if n.get(y)}}
    return out


def fmt_people(v):
    return f"{v:,.0f}"


def fmt_money(v):
    return f"${v / 1e6:,.0f}M" if v >= 1e6 else f"${v / 1e3:,.0f}k"


def draw(data, years, png):
    panels = [("People", "People (payroll rows)", fmt_people),
              ("Pay", "Total gross pay", fmt_money),
              ("PerPerson", "Pay per person (total pay / people)", fmt_money)]
    y0, y1 = years[0], years[-1]
    fig, axes = plt.subplots(2, 3, figsize=(16, 9), facecolor=SURF)
    fig.subplots_adjust(hspace=0.42, wspace=0.32, left=0.05, right=0.945, top=0.86, bottom=0.1)
    for i, (loc, groups) in enumerate(data.items()):
        for j, (key, label, fmt) in enumerate(panels):
            ax = axes[i][j]
            ax.set_facecolor(SURF)
            ends = []
            if key != "People":
                cpi = [100 * CPI_W_CA[y] / CPI_W_CA[y0] for y in years]
                ax.plot(years, cpi, color=TXT2, lw=1.5, ls=(0, (4, 3)), label="Inflation (CA CPI-W)")
                ends.append([cpi[-1], f"CPI +{cpi[-1] - 100:.0f}%"])
            for name, _, col in GROUPS:
                d = groups[name][key]
                idx = [100 * d[y] / d[y0] for y in years]
                ax.plot(years, idx, color=col, lw=2, solid_joinstyle="round", solid_capstyle="round", label=name)
                ax.plot(years[-1], idx[-1], "o", ms=8, color=col, mec=SURF, mew=2)
                ends.append([idx[-1], f"{fmt(d[y1])} ({idx[-1] - 100:+.0f}%)"])
            # push overlapping end labels apart
            ymin, ymax = ax.get_ylim()
            gap = (ymax - ymin) * 0.06
            ends.sort(key=lambda e: e[0])
            pos = [e[0] for e in ends]
            for k in range(1, len(pos)):
                if pos[k] - pos[k - 1] < gap:
                    pos[k] = pos[k - 1] + gap
            for (y, txt), p in zip(ends, pos):
                ax.annotate(txt, (years[-1], y), xytext=(9, (p - y) / (ymax - ymin) * ax.bbox.height * 0.99),
                            textcoords="offset points", va="center", fontsize=8.5, color=TXT)
            ax.axhline(100, color=GRID, lw=1, zorder=0)
            ax.set_title(f"{loc} - {label}", loc="left", fontsize=10.5, color=TXT, pad=8)
            ax.set_xticks(years)
            ax.tick_params(colors=TXT2, labelsize=8.5, length=0)
            for s in ("top", "right", "left"):
                ax.spines[s].set_visible(False)
            ax.spines["bottom"].set_color(GRID)
            ax.grid(axis="y", color=GRID, lw=1)
            ax.set_axisbelow(True)
            ax.set_ylabel(f"Index, {y0} = 100", color=TXT2, fontsize=8.5)
            ax.set_xlim(y0 - 0.2, y1 + 1.0)
    fig.suptitle(f"Growth since {y0}: lecturers, teaching professors, Senate faculty, admin (excluding health care)",
                 x=0.05, ha="left", fontsize=13, color=TXT, y=0.965)
    fig.text(0.05, 0.915, "Source: ucannualwage.ucop.edu payroll rows, gross pay; categories from "
             "data/title_categories.csv. Admin = SMG + academic admin + staff management. "
             f"End labels: {y1} value and change since {y0}.", fontsize=8.5, color=TXT2)
    h, l = axes[0][1].get_legend_handles_labels()
    fig.legend(h, l, loc="lower center", ncol=5, frameon=False, fontsize=9.5,
               bbox_to_anchor=(0.5, 0.005), labelcolor=TXT)
    fig.savefig(png, dpi=150, facecolor=SURF)


def write_table(data, years, path):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["location", "group", "year", "people", "total_gross_pay", "pay_per_person",
                    "people_index", "pay_index", "pay_per_person_index", "cpi_w_ca_index"])
        y0 = years[0]
        for loc, groups in data.items():
            for name, _, _ in GROUPS:
                g = groups[name]
                for y in years:
                    w.writerow([loc, name, y, g["People"][y], round(g["Pay"][y]), round(g["PerPerson"][y]),
                                round(100 * g["People"][y] / g["People"][y0], 1),
                                round(100 * g["Pay"][y] / g["Pay"][y0], 1),
                                round(100 * g["PerPerson"][y] / g["PerPerson"][y0], 1),
                                round(100 * CPI_W_CA[y] / CPI_W_CA[y0], 1)])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=int, default=2019)
    ap.add_argument("--end", type=int, default=2025)
    ap.add_argument("--campus", default="Berkeley")
    ap.add_argument("--db", default=T.DATABASE)
    ap.add_argument("--out-dir", default="output")
    args = ap.parse_args()
    years = list(range(args.start, args.end + 1))
    conn = T.connect(args.db)
    data = fetch(conn, years, args.campus)
    os.makedirs(args.out_dir, exist_ok=True)
    stem = os.path.join(args.out_dir, f"growth_{args.start}_{args.end}")
    draw(data, years, stem + ".png")
    write_table(data, years, stem + ".csv")
    for loc, groups in data.items():
        print(loc)
        for name, _, _ in GROUPS:
            g = groups[name]
            print(f"  {name:<22} people {g['People'][years[0]]:>6,} -> {g['People'][years[-1]]:>6,}"
                  f"  pay ${g['Pay'][years[0]] / 1e6:,.0f}M -> ${g['Pay'][years[-1]] / 1e6:,.0f}M")
    print(f"wrote {stem}.png and {stem}.csv")


if __name__ == "__main__":
    main()
