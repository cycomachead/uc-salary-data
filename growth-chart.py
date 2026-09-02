#!/usr/bin/env python3
"""Growth of lecturers, teaching professors, Senate faculty and admin roles.

Reads the categorized salary view (build-db.py / categorize-titles.py) and
plots indexed lines (start year = 100) for people, total gross pay and pay
per person, with California CPI-W dashed for reference.

  python3 growth-chart.py                         # UC systemwide + Berkeley, 2019-2025
  python3 growth-chart.py --start 2021            # same, 2021-2025
  python3 growth-chart.py --start 2021 --campuses # UC systemwide + all 10 campuses:
                                                  # one row per location, PNG + PDF

Outputs go to output/growth_<start>_<end>*.png / .csv.

Definitions
  * "All admin" = Senior Management Group + Academic Administration +
    Staff - Management.  Health-care titles (medical centers, clinics,
    health-system executives; is_health = 'Y') are always excluded from
    admin and lecturers - this is a best-effort exclusion by payroll title
    at the campuses with medical centers (Davis, Irvine, Los Angeles, San
    Diego, San Francisco), marked with * on the charts.
  * Senate faculty exclude HCOMP / clinical faculty (Professor-HCOMP,
    In Residence, Of Clinical X) by default; --clinical-faculty include
    keeps them (needed for a meaningful UCSF panel).
  * Local inflation: a dashed purple line is drawn when
    data/reference/cpi_local.csv has the campus's metro CPI-U annual
    averages (columns area,year,value; areas as in CPI_AREA below, e.g. BLS
    series CUURS49BSA0 San Francisco, CUURS49ASA0 Los Angeles, CUURS49ESA0
    San Diego, CUURS49CSA0 Riverside). BLS publishes no index for
    Sacramento (Davis), Santa Barbara or Merced.
"""

import argparse
import csv
import os
import textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import uc_titles as T  # noqa: E402

FACULTY_HEALTH_NOTE = {
    "exclude": "Senate faculty and teaching professors EXCLUDE HCOMP/clinical faculty "
               "(Professor-HCOMP, In Residence, Of Clinical X, HS Clinical).",
    "include": "Senate faculty and teaching professors INCLUDE HCOMP/clinical faculty "
               "(Professor-HCOMP, In Residence, Of Clinical X); HS Clinical and adjunct series are not Senate.",
}

# See module docstring for the exclusion rule and the full-time estimate.
FULLTIME_CSV = "data/reference/uc_fulltime_lecturers.csv"   # UCOP headcount dashboard, Full Time filter
# 2022 lecturer rows that look like retroactive contract payments to people who had
# already left: paid under $5k in 2022 and absent from the 2023 payroll.
JUNK_2022 = ("NOT (year = 2022 AND gross_pay < 5000 AND NOT EXISTS ("
             "SELECT 1 FROM salaries t WHERE t.year = 2023 AND t.first_name = s.first_name "
             "AND t.last_name = s.last_name AND t.location = s.location))")
EST_FT = "EST full-time U18 lecturers"
EST_FT_COLOR = "#1b4aa8"

GROUPS = [
    # label, SQL condition, colour (validated categorical palette), is_faculty
    ("Lecturers (Unit 18)", f"category = '{T.LECT}' AND {JUNK_2022}", "#2a78d6", False),
    ("Teaching professors", f"category = '{T.SEN}' AND secondary_category LIKE 'Teaching Professor%'", "#eb6834", True),
    ("Senate faculty", f"category = '{T.SEN}' AND secondary_category NOT LIKE 'Teaching Professor%' "
                       "AND secondary_category NOT LIKE 'Recall%' AND secondary_category NOT LIKE 'Emeritus%'", "#1baf7a", True),
    ("All admin", f"category IN ('{T.SMG}', '{T.ACADMIN}', '{T.MGMT}')", "#eda100", False),
]

# California CPI-W, calendar-year average (UC Accountability Report glossary, Table 5).
CPI_W_CA = {2010: 219.7, 2011: 226.4, 2012: 231.6, 2013: 234.9, 2014: 239.0, 2015: 241.6,
            2016: 246.2, 2017: 253.2, 2018: 263.0, 2019: 270.8, 2020: 275.6, 2021: 288.6,
            2022: 310.4, 2023: 321.2, 2024: 330.7, 2025: 341.0}

CAMPUSES = ["Berkeley", "Davis", "Irvine", "Los Angeles", "Merced", "Riverside",
            "San Diego", "San Francisco", "Santa Barbara", "Santa Cruz"]
MED_CENTER_CAMPUSES = {"Davis", "Irvine", "Los Angeles", "San Diego", "San Francisco"}
CPI_AREA = {  # campus -> BLS metro CPI-U area (nearest published index)
    "Berkeley": "San Francisco-Oakland-Hayward", "San Francisco": "San Francisco-Oakland-Hayward",
    "Santa Cruz": "San Francisco-Oakland-Hayward", "Los Angeles": "Los Angeles-Long Beach-Anaheim",
    "Irvine": "Los Angeles-Long Beach-Anaheim", "San Diego": "San Diego-Carlsbad",
    "Riverside": "Riverside-San Bernardino-Ontario",
}
LOCAL_CPI_CSV = "data/reference/cpi_local.csv"
UC = "UC systemwide"

SURF, TXT, TXT2, GRID, LOCAL = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e2", "#7b3fbf"
MIN_BASE = 25  # series with fewer people than this in the start year are not drawn


def load_local_cpi():
    out = {}
    if os.path.exists(LOCAL_CPI_CSV):
        for r in T.read_csv(LOCAL_CPI_CSV):
            try:
                out.setdefault(r["area"], {})[int(r["year"])] = float(r["value"])
            except (KeyError, ValueError):
                continue
    return out


def fetch(conn, years, locations, clinical_faculty="exclude", fulltime=None):
    """{location: {group: {'People': {year: n}, 'Pay': {...}, 'PerPerson': {...}}}}"""
    fulltime = fulltime or {}
    out = {}
    for loc in locations:
        where_loc = "" if loc == UC else "AND location = ?"
        out[loc] = {}
        for name, cond, _, is_faculty in GROUPS:
            health = "" if (is_faculty and clinical_faculty == "include") else "AND is_health = 'N'"
            params = (years[0], years[-1]) + (() if loc == UC else (loc,))
            rows = conn.execute(
                f"SELECT year, COUNT(*), SUM(gross_pay) FROM salaries_categorized s "
                f"WHERE year BETWEEN ? AND ? {health} AND ({cond}) {where_loc} GROUP BY year",
                params).fetchall()
            n = {y: a for y, a, b in rows}
            p = {y: b for y, a, b in rows}
            out[loc][name] = {"People": n, "Pay": p,
                              "PerPerson": {y: p[y] / n[y] for y in years if n.get(y)}}
        if loc in fulltime and all(y in fulltime[loc] for y in years):
            out[loc][EST_FT] = estimate_fulltime(conn, years, loc, fulltime[loc])
    return out


def count_junk_2022(conn, locations):
    """How many 2022 lecturer rows the JUNK_2022 rule removes, per location."""
    out = {}
    for loc in locations:
        where_loc = "" if loc == UC else "AND location = ?"
        params = () if loc == UC else (loc,)
        out[loc] = conn.execute(
            f"SELECT COUNT(*) FROM salaries_categorized s WHERE year = 2022 AND is_health = 'N' "
            f"AND category = '{T.LECT}' AND NOT ({JUNK_2022}) {where_loc}", params).fetchone()[0]
    return out


def load_fulltime():
    """{location: {year: full-time lecturer headcount}} from the UCOP dashboard export."""
    out = {}
    if os.path.exists(FULLTIME_CSV):
        for r in T.read_csv(FULLTIME_CSV):
            out.setdefault(r["location"], {})[int(r["year"])] = int(r["fulltime_lecturers"])
    return out


def estimate_fulltime(conn, years, loc, headcounts):
    """Proxy for full-time lecturers: the N highest REGULAR-pay lecturer rows, where N is
    UCOP's full-time lecturer headcount for that year and location.  Pay is their gross pay."""
    where_loc = "" if loc == UC else "AND location = ?"
    n, p, cutoff = {}, {}, {}
    for y in years:
        params = (y,) + (() if loc == UC else (loc,))
        rows = conn.execute(
            f"SELECT regular_pay, gross_pay FROM salaries_categorized s WHERE year = ? AND is_health = 'N' "
            f"AND category = '{T.LECT}' AND {JUNK_2022} {where_loc} ORDER BY regular_pay DESC LIMIT {headcounts[y]}",
            params).fetchall()
        n[y] = len(rows)
        p[y] = sum(g for r, g in rows)
        cutoff[y] = rows[-1][0] if rows else None
    return {"People": n, "Pay": p, "PerPerson": {y: p[y] / n[y] for y in years if n.get(y)}, "Cutoff": cutoff}


def fmt_people(v):
    return f"{v:,.0f}"


def fmt_money(v):
    return f"${v / 1e6:,.0f}M" if v >= 1e6 else f"${v / 1e3:,.0f}k"


PANELS = [("People", "People (payroll rows)", fmt_people),
          ("Pay", "Total gross pay", fmt_money),
          ("PerPerson", "Average gross pay per row (total pay / people)", fmt_money)]


def panel_title(loc):
    return f"{loc}*" if loc in MED_CENTER_CAMPUSES else loc


def draw_panel(ax, groups, years, key, fmt, title, local_cpi=None, fontsize=8.5):
    y0, y1 = years[0], years[-1]
    ax.set_facecolor(SURF)
    ends = []
    if key != "People":
        cpi = [100 * CPI_W_CA[y] / CPI_W_CA[y0] for y in years]
        ax.plot(years, cpi, color=TXT2, lw=1.5, ls=(0, (4, 3)), label="Inflation (CA CPI-W)")
        ends.append([cpi[-1], f"CA CPI +{cpi[-1] - 100:.0f}%"])
        if local_cpi and all(y in local_cpi for y in years):
            lc = [100 * local_cpi[y] / local_cpi[y0] for y in years]
            ax.plot(years, lc, color=LOCAL, lw=1.5, ls=(0, (4, 3)), label="Local inflation (metro CPI-U)")
            ends.append([lc[-1], f"local CPI +{lc[-1] - 100:.0f}%"])
    small = []
    series = [(name, col) for name, _, col, _ in GROUPS]
    if EST_FT in groups:
        series.append((EST_FT, EST_FT_COLOR))
    for name, col in series:
        d = groups[name][key]
        if not all(d.get(y) for y in years) or groups[name]["People"][y0] < MIN_BASE:
            small.append(name)
            continue
        idx = [100 * d[y] / d[y0] for y in years]
        style = dict(ls=(0, (6, 2)), lw=2.2) if name == EST_FT else dict(lw=2)
        ax.plot(years, idx, color=col, solid_joinstyle="round", solid_capstyle="round", label=name, **style)
        ax.plot(years[-1], idx[-1], "o", ms=7, color=col, mec=SURF, mew=2)
        ends.append([idx[-1], f"{fmt(d[y1])} ({idx[-1] - 100:+.0f}%)"])
    if small:
        ax.text(0.0, -0.2, "not shown (fewer than %d people in %d): %s" % (MIN_BASE, y0, ", ".join(small)),
                transform=ax.transAxes, fontsize=fontsize - 1, color=TXT2, va="top")
    ymin, ymax = ax.get_ylim()
    gap = (ymax - ymin) * 0.07
    ends.sort(key=lambda e: e[0])
    pos = [e[0] for e in ends]
    for k in range(1, len(pos)):
        if pos[k] - pos[k - 1] < gap:
            pos[k] = pos[k - 1] + gap
    # keep the label stack inside the axes
    if pos and pos[-1] > ymax - 0.3 * gap:
        shift = pos[-1] - (ymax - 0.3 * gap)
        pos = [p - shift for p in pos]
    for (y, txt), p in zip(ends, pos):
        ax.annotate(txt, (years[-1], y), xytext=(8, (p - y) / (ymax - ymin) * ax.bbox.height * 0.99),
                    textcoords="offset points", va="center", fontsize=fontsize, color=TXT)
    ax.axhline(100, color=GRID, lw=1, zorder=0)
    ax.set_title(title, loc="left", fontsize=fontsize + 2, color=TXT, pad=6)
    ax.set_xticks(years)
    ax.tick_params(colors=TXT2, labelsize=fontsize, length=0)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.grid(axis="y", color=GRID, lw=1)
    ax.set_axisbelow(True)
    ax.set_ylabel(f"Index, {y0} = 100", color=TXT2, fontsize=fontsize)
    ax.set_xlim(y0 - 0.2, y1 + 1.0 + 0.25 * (y1 - y0 < 5))


def footnote(clinical_faculty, local_cpi, with_asterisk, junk=None):
    lines = ["Source: ucannualwage.ucop.edu payroll rows, gross pay; categories from data/title_categories.csv. "
             "Admin = SMG + academic administration + staff management; health-care titles excluded. "
             "End labels: the LEVEL in the last year (headcount, $ total, or $ average per row), with % change since the start year in parentheses.",
             "Lecturers: 2022 rows paid under $5k by people absent from the 2023 payroll are excluded (retroactive contract payments"
             + (f"; {junk[UC]:,} rows systemwide" if junk and UC in junk else "") + "). "
             "EST full-time U18 lecturers = the N highest REGULAR-pay lecturer rows, N = UCOP's October full-time lecturer headcount; their gross pay is plotted."]
    if with_asterisk:
        lines.append("* Campus with a medical center: medical-center staff and health-system executives are "
                     "excluded by payroll title (best effort; see docs/title-categories.md).")
    lines.append(FACULTY_HEALTH_NOTE[clinical_faculty])
    if with_asterisk:
        lines.append("CA CPI-W (Dept. of Finance) is dashed gray. Local metro CPI-U is dashed purple when "
                     "data/reference/cpi_local.csv has it" + (" (not loaded)." if not local_cpi else "."))
    return "\n".join(textwrap.fill(line, 175) for line in lines)


def best_legend(axes):
    """Handles/labels from the panel with the most series (so local CPI is included when drawn)."""
    best = ([], [])
    for ax in axes.flat:
        h, l = ax.get_legend_handles_labels()
        if len(l) > len(best[1]):
            best = (h, l)
    return best


def draw_two_locations(data, years, png, clinical_faculty, local_cpis, junk=None):
    y0 = years[0]
    fig, axes = plt.subplots(2, 3, figsize=(16, 10.6), facecolor=SURF)
    fig.subplots_adjust(hspace=0.45, wspace=0.32, left=0.05, right=0.945, top=0.81, bottom=0.11)
    for i, (loc, groups) in enumerate(data.items()):
        for j, (key, label, fmt) in enumerate(PANELS):
            draw_panel(axes[i][j], groups, years, key, fmt, f"{panel_title(loc)} - {label}",
                       local_cpis.get(CPI_AREA.get(loc)))
    fig.suptitle(f"Growth since {y0}: lecturers, teaching professors, Senate faculty, admin (excluding health care)",
                 x=0.05, ha="left", fontsize=13, color=TXT, y=0.985)
    fig.text(0.05, 0.855, footnote(clinical_faculty, local_cpis, any(l in MED_CENTER_CAMPUSES for l in data), junk),
             fontsize=8.5, color=TXT2, va="bottom")
    h, l = best_legend(axes)
    for anchor in ((0.5, 0.01), (0.5, 0.94)):
        fig.legend(h, l, loc="lower center", ncol=7, frameon=False, fontsize=9.5,
                   bbox_to_anchor=anchor, labelcolor=TXT)
    fig.savefig(png, dpi=150, facecolor=SURF)
    plt.close(fig)


def draw_campus_matrix(data, years, stem, clinical_faculty, local_cpis, junk=None):
    """One figure: a row per location (UC systemwide + ten campuses), a column per metric."""
    y0 = years[0]
    locs = list(data.keys())
    nrow, ncol = len(locs), len(PANELS)
    height = 3.4 * nrow + 3.8
    fig, axes = plt.subplots(nrow, ncol, figsize=(17, height), facecolor=SURF)
    top_frac = 1 - 3.2 / height
    fig.subplots_adjust(hspace=0.55, wspace=0.34, left=0.05, right=0.95, top=top_frac, bottom=0.9 / height)
    for i, loc in enumerate(locs):
        for j, (key, label, fmt) in enumerate(PANELS):
            draw_panel(axes[i][j], data[loc], years, key, fmt, f"{panel_title(loc)} - {label}",
                       local_cpis.get(CPI_AREA.get(loc)), fontsize=8)
    fig.suptitle(f"Growth {y0}-{years[-1]}: UC systemwide and the ten campuses (excluding health care)",
                 x=0.05, ha="left", fontsize=13, color=TXT, y=1 - 0.4 / height)
    fig.text(0.05, top_frac + 0.75 / height, footnote(clinical_faculty, local_cpis, True, junk),
             fontsize=8.5, color=TXT2, va="bottom")
    handles, labels = best_legend(axes)
    for anchor in ((0.5, 0.15 / height), (0.5, top_frac + 0.3 / height)):
        fig.legend(handles, labels, loc="lower center", ncol=7, frameon=False, fontsize=9.5,
                   bbox_to_anchor=anchor, labelcolor=TXT)
    fig.savefig(stem + ".png", dpi=110, facecolor=SURF)
    fig.savefig(stem + ".pdf", facecolor=SURF)
    plt.close(fig)


def write_table(data, years, path, local_cpis):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["location", "group", "year", "people", "total_gross_pay", "pay_per_person",
                    "people_index", "pay_index", "pay_per_person_index", "cpi_w_ca_index", "local_cpi_index",
                    "est_fulltime_regular_pay_cutoff"])
        y0 = years[0]
        for loc, groups in data.items():
            lc = local_cpis.get(CPI_AREA.get(loc), {})
            for name in [g for g, _, _, _ in GROUPS] + ([EST_FT] if EST_FT in groups else []):
                g = groups[name]
                for y in years:
                    if not g["People"].get(y):
                        continue
                    w.writerow([loc, name, y, g["People"][y], round(g["Pay"][y]), round(g["PerPerson"][y]),
                                round(100 * g["People"][y] / g["People"][y0], 1),
                                round(100 * g["Pay"][y] / g["Pay"][y0], 1),
                                round(100 * g["PerPerson"][y] / g["PerPerson"][y0], 1),
                                round(100 * CPI_W_CA[y] / CPI_W_CA[y0], 1),
                                round(100 * lc[y] / lc[y0], 1) if y in lc and y0 in lc else "",
                                g.get("Cutoff", {}).get(y, "")])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=int, default=2019)
    ap.add_argument("--end", type=int, default=2025)
    ap.add_argument("--campus", default="Berkeley")
    ap.add_argument("--campuses", action="store_true", help="UC systemwide + all ten campuses, one figure per metric")
    ap.add_argument("--clinical-faculty", choices=["exclude", "include"], default="exclude")
    ap.add_argument("--db", default=T.DATABASE)
    ap.add_argument("--out-dir", default="output")
    args = ap.parse_args()
    years = list(range(args.start, args.end + 1))
    conn = T.connect(args.db)
    local_cpis = load_local_cpi()
    fulltime = load_fulltime()
    os.makedirs(args.out_dir, exist_ok=True)
    suffix = "_incl_clinical" if args.clinical_faculty == "include" else ""
    if args.campuses:
        data = fetch(conn, years, [UC] + CAMPUSES, args.clinical_faculty, fulltime)
        junk = count_junk_2022(conn, list(data)) if years[0] <= 2022 <= years[-1] else None
        stem = os.path.join(args.out_dir, f"growth_campuses_{args.start}_{args.end}{suffix}")
        draw_campus_matrix(data, years, stem, args.clinical_faculty, local_cpis, junk)
        print("wrote", stem + ".png and .pdf")
    else:
        data = fetch(conn, years, [UC, args.campus], args.clinical_faculty, fulltime)
        junk = count_junk_2022(conn, list(data)) if years[0] <= 2022 <= years[-1] else None
        stem = os.path.join(args.out_dir, f"growth_{args.start}_{args.end}{suffix}")
        draw_two_locations(data, years, stem + ".png", args.clinical_faculty, local_cpis, junk)
        print("wrote", stem + ".png")
    write_table(data, years, stem + ".csv", local_cpis)
    print("wrote", stem + ".csv")
    if junk:
        print("2022 lecturer rows excluded (<$5k, not on 2023 payroll):", ", ".join(f"{k}: {v:,}" for k, v in junk.items()))
    for loc, groups in data.items():
        print(loc)
        for name in [g for g, _, _, _ in GROUPS] + ([EST_FT] if EST_FT in groups else []):
            g = groups[name]
            if g["People"].get(years[0]) and g["People"].get(years[-1]):
                extra = (f"  regular-pay cutoff ${g['Cutoff'][years[0]]:,.0f} -> ${g['Cutoff'][years[-1]]:,.0f}"
                         if "Cutoff" in g else "")
                print(f"  {name:<28} people {g['People'][years[0]]:>6,} -> {g['People'][years[-1]]:>6,}"
                      f"  pay ${g['Pay'][years[0]] / 1e6:,.0f}M -> ${g['Pay'][years[-1]] / 1e6:,.0f}M{extra}")


if __name__ == "__main__":
    main()
