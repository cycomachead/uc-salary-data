# UC payroll title categories

How every payroll title in `data/uc_salaries.db` is mapped to an analysis
category, where the category definitions come from, and how to keep the
mapping in step with the Google Sheet.

Files:

| File | What it is |
| --- | --- |
| `data/title_categories.csv` | One row per distinct payroll title (2010-2025), with primary/secondary category, the rule that fired, confidence, UCOP title-code info, family/level for promotions, rename successor, and counts. Sorted by number of person-years in the title. |
| `data/title_crosswalk.csv` | Data-derived title transitions: renames (old vocabulary -> new), variants (suffix-only differences), promotions (next level in the same family). |
| `data/reference/ucop_academic_titles.csv` | UCOP's academic title list (title code, title, Class Title Outline). Parsed from the PDFs at <https://www.ucop.edu/academic-personnel-programs/compensation/academic-ctos-titles-and-title-codes/> (active + frozen lists, revised July 2026). |
| `data/reference/uc_headcount_dashboard.csv` | April headcounts by personnel program / CTO group, exported from the UC employee headcount dashboard. |
| `data/sheet/*.csv` | Tabs pulled from the Google Sheet (`sync-title-sheet.py pull`). |
| `categorize-titles.py` | Rebuilds the two CSVs from the DB. Human columns are preserved. |
| `sync-title-sheet.py` | `pull` / `merge` / `export` / `push` / `load-db` for the sheet round trip. |
| `uc_titles.py` | Shared constants and the SQLite loader. |

## Commands

```sh
python3 build-db.py                    # rebuild salaries from data/json, then load title tables
python3 build-db.py --titles-only      # just reload title_categories / title_crosswalk + view
python3 categorize-titles.py --report  # re-derive categories + crosswalk from the DB
python3 sync-title-sheet.py pull       # download the sheet tabs and merge Category -> original_category
python3 sync-title-sheet.py merge      # merge a hand-downloaded data/sheet/all_uc_title_codes.csv
python3 sync-title-sheet.py export     # copies for File > Import into the sheet
python3 sync-title-sheet.py push --credentials svc.json   # gspread upload to a "claude categories" tab
```

In SQLite the view `salaries_categorized` joins `salaries` to
`title_categories`, so for example:

```sql
SELECT year, category, COUNT(*), SUM(gross_pay)
FROM salaries_categorized
WHERE location = 'Berkeley' AND is_health = 'N'
GROUP BY year, category;
```

Notebook code that used the sheet (`TITLE_CODE_MAP` with `Title` /
`Category` columns) can read the CSV instead:

```python
TITLE_CODE_MAP = pd.read_csv('data/title_categories.csv').rename(
    columns={'title': 'Title', 'category': 'Category'})
```

## Category scheme

Primary categories are deliberately coarse except for faculty, where the
question is lecturers vs. Senate faculty vs. clinical faculty. Secondary
categories carry the detail (rank/track for faculty, function family for
staff) so the analysis can drill in or roll up.

| Primary | Secondary values | Notes |
| --- | --- | --- |
| Senate Faculty | Ladder Rank (Professor series); Professor In Residence; Professor of Clinical X; Teaching Professor (SOE/PSOE; formerly LSOE); Acting Professor (associate/full); Agronomist / Astronomer; Supervisor of Physical Education; Clinical Professor of Dentistry; Recall; Emeritus | Follows Standing Order 105.1(a) and the original sheet: In-Residence and "of Clinical X" series are Senate members. UC's accountability reporting instead groups those two with clinical faculty; that grouping is kept in `uc_academic_group`. |
| Non-Senate Faculty | Health Sciences Clinical Professor / Instructor; Adjunct Professor / Instructor; Visiting Professor; Acting Assistant Professor; Clinical Professor - Volunteer; Clinical Professor (law school); Recall (non-senate) | Acting assistant professors are CTO 124 "non-senate" but sit in UC's ladder-rank-and-equivalent count. |
| Lecturers (Unit 18) | Lecturer (Pre-Six); Lecturer (Continuing); Senior Lecturer (Continuing); Summer Session Lecturer; Instructional Assistant (Supv Teacher Ed / Field Work); Other Unit 18 instructional title; Lecturer (misc / part-time / WOS); Lecturer (UC Law SF) | Pre-Six -> Continuing -> Senior Continuing is the promotion ladder. Continuing Educator / Demo Teacher / Teacher-Special Programs are medium confidence: verify against the current UC-AFT MOU recognition clause. |
| Academic Research | Postdoctoral Scholar; Project Scientist; Professional Researcher; Specialist (research series); Other Research | |
| Other Academic | Librarian; Cooperative Extension; Academic Coordinator; Academic Administrator; University Extension (UNEX) teacher / staff; K-12 lab school instructor; Miscellaneous academic title; Recall / Emeritus (non-faculty) | |
| Academic Administration | Dean; Associate / Assistant Dean; Provost / Vice Provost; Department Chair; Director (academic unit); Faculty / Academic Assistant to executive | The original sheet's "Admin" (deans, directors). Chairs/deans usually also hold a faculty row, so payroll shows the stipend title separately in some years. |
| Senior Management Group | President / Chancellor; Executive Vice Chancellor / Provost; Vice Chancellor; Vice President; Associate / Assistant VC or VP; General Counsel / Campus Counsel; Chief Officer / Regents & UCOP Officers; Health System Executive; Executive (legacy SMG title, pre-2014); Unclassified senior manager | Per Accountability Report ch. 6: President, Chancellors, VPs, VCs, campus counsels. AVC/AVP are a mix of SMG and MSP, so they are a separate secondary. 2021 payroll count (178) matches the dashboard's April 2021 SMG headcount (169). |
| Staff - Management | function family | MSP managers: Career Tracks `* MGR n`, `DIR`, `MGR`, staff directors, assistant deans (staff), executive directors. |
| Staff - Professional & Supervisory | function family | Analysts, specialists, officers, SRAs, IT, supervisors (rule `P-SUPV`). |
| Staff - Support & Operations | function family | Assistants, technicians, clerks, custodial/food, trades, police officers. |
| Health Care (Med Center & Clinical) | Medical Intern / Resident; Nursing; Physician / Advanced Practice Provider; Allied Health & Clinical Technical; Behavioral Health & Counseling; Clinical Research Staff; Health IT & Informatics; Health Administration, Billing & Records; Hospital Support & Patient Services | Everything clinical regardless of campus (incl. student health, `MED CTR *`, pre-2012 `, MC` titles). Use `is_health = 'Y'` to also drop HCOMP/health-sciences faculty. |
| Athletics | Head Coach; Assistant Coach / Coach; Recreation Coach; Athletics Staff | Original "Coach" = Head Coach. |
| Student Instruction | Teaching Assistant / Fellow; Reader / Tutor; Associate In | |
| Student Research | Graduate Student Researcher | |
| Students | Student Assistant; Student Employee (other) | `STDT 1-4`, work-study, interns, RAs. `STDT AFFAIRS OFCR` etc. are staff, not students. |
| Other / Uncategorized | Supplemental pay code; Unclassified; Invalid / blank title; Non-employee | Pay codes (stipends, `ADDL COMP`) are not jobs; exclude from headcounts. |

Staff function families: Administration, Analysis & Clerical; Finance, HR,
Purchasing & Business Services; Information Technology; Research Support
(staff); Student Services; Development, Communications & Events; Library &
Museum Staff; Legal & Compliance; Police, Security & Safety; Facilities,
Trades, Grounds & Logistics; Custodial, Food, Housing & Hospitality;
Recreation, Child Care & Camps; General / Unclassified staff.

## The original sheet's categories

Definitions from the sheet's CATEGORIES tab, and where those titles landed:

| Original | Sheet definition | New categories it maps to |
| --- | --- | --- |
| Senate | Senate faculty, but not LPSOE series | Senate Faculty (excluding the Teaching Professor secondary) |
| Teaching Track | LPSOE series | Senate Faculty / Teaching Professor |
| Non-Senate | Teaching faculty (adjunct, visiting, ...), not Unit 18 | Non-Senate Faculty; also Other Academic (UNEX, recall non-faculty) and misc lecturers |
| Unit 18 | All Unit 18 titles | Lecturers (Unit 18) |
| Research | Research unionized roles | Academic Research; the sheet also put SRAs here |
| Research Staff | Research support, incl. non-unionized roles | Staff (Research Support family) |
| Acad Staff | Advising etc.; "not a clear boundary" | Staff (Student Services family), Other Academic |
| Admin | Execs, deans, assistant deans, directors | Senior Management Group, Academic Administration, Staff - Management |
| Admin Staff | Staff directly supporting administration | Staff tiers (Administration family) |
| Staff | "All staff roles ... that I can't classify" | Staff tiers, Health Care |
| Med Center | Roles clearly applied to med schools and hospitals | Health Care |
| Athletics / Coach | Athletics staff / head coaches | Athletics |
| Students / Student Instruction / Student Reasearch | Student jobs / TAs etc. / GSRs | Students / Student Instruction / Student Research |

`needs_review` is `Y` only when the new category is outside the list
allowed for the original one (`uc_titles.ORIGINAL_TO_NEW`). Expected
definitional differences (nurses filed under "Staff", advisers under "Acad
Staff") are not flagged. The remaining flags are the real disputes:
librarians (sheet: Senate; here: Other Academic, they are not Senate
members), agronomists and Cooperative Extension specialists (sheet:
Research; here: Senate-equivalent / Other Academic), Career Tracks
`TRAINER n` (sheet: Athletics; here: HR training staff, athletic trainers
are `ATH TRAINER`), `ADMIN STIPEND` (sheet: Admin; here: pay code),
student-health physicians (sheet: Acad Staff; here: Health Care), and
Continuing Educators (sheet: Acad Staff; here: Unit 18, medium confidence).

## Columns in `title_categories.csv`

| Column | Meaning |
| --- | --- |
| `category`, `secondary_category` | The analysis categories above. |
| `source` | Who set `category`: `claude` (this script), `original` (the sheet), `manual` (edited by hand; never overwritten on rebuild). |
| `rule_id` | Which rule fired. `Fnn` faculty, `Lnn` lecturers, `Rnn` research, `Ann` other academic, `Dnn` academic admin, `Snn` SMG, `Hnn` health, `Tnn` athletics, `Gnn` students, `Mnn/Pnn/Onn` explicit staff rules, `CTO-xxx` UCOP title list, `M-ROLE/P-ROLE/O-ROLE/P-SUPV` generic staff role-word rules, `X-SUCCESSOR` inherited from the title it was renamed to, `X-VARIANT` inherited from the suffix-stripped title, `Znn` pay codes, `ORIGINAL`/`MANUAL`. Rules are listed in order in `categorize-titles.py`. |
| `confidence` | high / medium / low. Review the medium/low rows first (`--report` lists the biggest). |
| `original_category` | Category from the Google Sheet (`All UC Title Codes` tab). |
| `needs_review` | `Y` when `original_category` and `category` disagree (mapping in `uc_titles.ORIGINAL_TO_NEW`). |
| `review_notes` | Free text for the reviewer; carried over on rebuild. |
| `is_health` | `Y` for health-care categories, HCOMP/health-sciences faculty, `MED CTR`, SOM titles. |
| `uc_cto`, `uc_cto_name`, `uc_academic_group` | UCOP Class Title Outline and the Accountability Report glossary Table 3 group (LRE, Clinical/In-Residence/Adjunct, Lecturers, Postdocs, Residents, Student TA/RA, Other academic). Only filled for titles on UCOP's academic list. |
| `family`, `level` | Promotion ladder key. `HR MGR 3` -> family `HR MGR`, level 3. Faculty: ASST 1 / ASSOC 2 / full 3 (instructor 0); lecturers: pre-six 1 / continuing 2 / senior 3; Teaching Professors share the `PROF OF TEACH-*` family with the old LSOE titles. Titles that were renamed take their successor's family. |
| `successor_title` | Title this one was renamed to (from the crosswalk). |
| `next_title` | Most common observed one-level promotion. |
| `n_all`, `n_2025`, `n_2020`, `n_2010`, `first_year`, `last_year`, `avg_gross_*` | Counts of payroll rows (person-years), not unique people. |

## How the crosswalk is derived

People are linked across consecutive years by (first name, last name,
location); names that are duplicated within a year/location are dropped.
Every change of title between two years is counted. A pair is a

* **variant** when the two titles differ only by a suffix code
  (`ADMIN OFCR 3` -> `ADMIN OFCR 3 CX`);
* **rename** when the old title is gone from the latest year, the moves
  happen in the year after it disappears, and at least 25% (medium) / 50%
  (high) of leavers went to the new title. The 2012 vocabulary change
  (`PROFESSOR - ACADEMIC YEAR` -> `PROF-AY`) and the 2024 LSOE ->
  Professor of Teaching rename are captured this way;
* **promotion** when both titles are in the same family and the level goes
  up (`LECT-AY` -> `LECT-AY-CONTINUING`, `ASST PROF-AY` -> `ASSOC PROF-AY`);
* **lateral** / **other** otherwise (kept when at least 5 people and 10% of
  leavers made the move, for reference).

## Data quality notes

* **2012 vocabulary change.** 2010-2011 use long titles (`NURSE, CLINICAL II`,
  `_____ASSISTANT II`); from 2012 the abbreviated UCPath/Career Tracks titles
  are used. About 2,500 titles retired in 2011 and 2,500 new ones appear in
  2012. `successor_title` links them, and old titles inherit family/level.
* **Suffix codes** appended to otherwise identical titles: `NEX`/`EX`
  (non-exempt/exempt), `PD` (per diem), `GF` (grandfathered), `NON REP`/`REP`
  (union representation), `WKSTY`/`WORK STUDY`, and from 2022 bargaining-unit
  codes `CX` (clerical), `RX` (research support), `TX` (technical), `HX`
  (health care professionals), `SV`, `RP`, plus site codes at UC Davis Health
  (`CEH`, `LAK`, `LOM`, `PLA`) and `ME` on some UCOP/UCSF executive titles.
  `base_title()` in `categorize-titles.py` strips these; the exact set is
  a best guess and is listed in `VARIANT_SUFFIX_WORDS`.
* **2024 rename of the teaching-professor series.** `LECT PSOE-AY` /
  `LECT SOE-AY` / `SR LECT SOE-AY` became `ASST/ASSOC/PROF OF TEACH-AY`.
  They are Senate faculty ("Teaching Track" in the original sheet), not
  Unit 18 lecturers.
* **Recall titles** (`RECALL FACULTY`, `RECALL TEACHING`, `RECALL HCOMP`)
  are retired faculty re-hired part time; they have their own secondary so
  they can be excluded from active-faculty pay comparisons.
* **Pay codes as titles.** `ADMIN STIPEND`, `ADDL COMP-HGH&OLIVE VIEW MCS`,
  `SALARY SUPPLEMENTATION`, `SUMMER DIFFERENTIAL` etc. are UCOP "CTO 999"
  supplemental pay codes reported as if they were titles.
* **Payroll counts vs. headcount.** The salary data counts anyone paid in
  the calendar year, so counts exceed the April headcount dashboard (2021:
  11,782 ladder-rank-equivalent rows vs. 11,704 headcount; 6,174 lecturer
  rows vs. 4,220 headcount because of turnover).
* **Blank / garbage titles**: `''`, `#MULTIVALUE`, `9964 - NO DESCRIPTION
  FOUND`.
* **Hastings / UC Law SF** uses its own vocabulary (`ADJ. PROF`,
  `LW&R INTRUCTOR I`, `CWSP-ON CAMPUS`, `DIRECTOR I-III`).

## Sources

* UC employee headcount dashboard:
  <https://www.universityofcalifornia.edu/about-uc/information-center/uc-employee-headcount>
  (Tableau `visualizedata.ucop.edu/t/Public/views/EmployeeFTE_0/Headcount`;
  the crosstab export is in `data/reference/uc_headcount_dashboard.csv`).
  The raw data are not shared publicly; only crosstabs of each view.
* Accountability Report glossary, Table 3 (academic categories and CTO
  codes): <https://accountability.universityofcalifornia.edu/glossary.html#table3>
* Accountability Report 2024, chapter 6 (Staff; personnel programs SMG /
  MSP / PSS): <https://accountability.universityofcalifornia.edu/2024/chapters/chapter-6.html>
* UCOP academic titles and CTOs (PDF lists, revised 7/1/2026):
  <https://www.ucop.edu/academic-personnel-programs/compensation/academic-ctos-titles-and-title-codes/>
* Google Sheet with the original categories (tabs README, CATEGORIES,
  All UC Title Codes): <https://docs.google.com/spreadsheets/d/19MG1bKoQh6o6PAAYHAmdFyNpNspQ3tfQ6jpMbou_rw8/>.
  Pulled on 2026-09-02 into `data/sheet/`; the sheet's `Category` for each
  of its 3,523 titles (2020 vocabulary, 1,821 categorized) is in
  `original_category`. Disagreements with the new scheme are flagged
  `needs_review = Y` (see below).
* UCOP's Title Code System (`tcs.ucop.edu`) would give CTOs for staff titles
  too, but its CloudFront front end returns "Request blocked" (403) to this
  client, so staff categories rely on the regex rules.
