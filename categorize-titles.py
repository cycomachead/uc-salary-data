#!/usr/bin/env python3
"""Build data/title_categories.csv and data/title_crosswalk.csv from the salary DB.

Every payroll title that has ever appeared in data/uc_salaries.db gets a
primary + secondary category, plus the audit trail needed to check the
call: the rule that fired (`rule_id`), a confidence, the UCOP Class Title
Outline (CTO) the title belongs to when it is an academic title, and the
family/level used to follow "promotions" (LECT-AY -> LECT-AY-CONTINUING ->
SR LECT-AY-CONTINUING, HR MGR 3 -> HR MGR 4, ...).

Classification order (first match wins):
  1. explicit regex rules (students, SMG, faculty, lecturers, research,
     other academic, academic administration, health care, athletics, staff)
  2. the UCOP academic title list (data/reference/ucop_academic_titles.csv)
     mapped through the Accountability Report glossary Table 3 groups
  3. inheritance: a retired title takes the category of the title it was
     renamed to (data-derived crosswalk), or of its suffix-stripped base title
  4. Other / Uncategorized

The crosswalk is derived from the data itself: people are linked across
consecutive years by (first name, last name, location) and the title
transitions are counted.  A title that disappears while nearly everyone in
it moves to one new title is a *rename*; a move to the next level of the
same family while both titles coexist is a *promotion*.

Rows never overwrite human work: `original_category` (from the Google
Sheet) and `review_notes` are carried over from the existing CSV, and rows
whose `source` is `manual` keep their categories.  Everything this script
sets is marked source = claude.

    python3 categorize-titles.py            # rebuild both CSVs
    python3 categorize-titles.py --no-crosswalk
    python3 categorize-titles.py --report   # print category totals
"""

import argparse
import collections
import csv
import os
import re
import sys

import uc_titles as T
from uc_titles import (SMG, SEN, NONSEN, LECT, ACRES, OTHACAD, ACADMIN, MGMT,
                       PROF, SUPPORT, MED, ATH, STINS, STRES, STU, OTHER)

# ---------------------------------------------------------------------------
# UCOP Class Title Outline (CTO) -> category.  Groups follow the UC
# Accountability Report glossary, Table 3 ("Academic Categories, Series and
# Class Title Outline Codes"):
#   Faculty - Ladder-rank and Equivalent (LRE): 010 011 012 114 124 210 211 214 224 520 530 531
#   Faculty - Clinical/In-Residence/Adjunct:    311 317 323 335 341
#   Faculty - Lecturers:                        225 357
#   Postdoctoral Scholars:                      575 577
#   Medical Interns/Residents:                  446
#   Student Teaching/Research Assistants:       426 436 456 467
#   Other academic employees:                   everything else
# ---------------------------------------------------------------------------
LRE = "Faculty - Ladder-rank and Equivalent"
CIA = "Faculty - Clinical/In-Residence/Adjunct"
FLECT = "Faculty - Lecturers"
POSTDOC = "Postdoctoral Scholars"
RESIDENTS = "Medical Interns/Residents"
STUDENT_ACAD = "Student Teaching/Research Assistants"
OTHER_ACAD = "Other Academic Employees"

CTO_MAP = {
    # cto: (uc_academic_group, category, secondary)
    "010": (LRE, SEN, "Ladder Rank (Professor series)"),
    "011": (LRE, SEN, "Ladder Rank (Professor series)"),
    "012": (LRE, SEN, "Recall (retired faculty)"),
    "016": (OTHER_ACAD, SEN, "Emeritus (without salary)"),
    "030": (OTHER_ACAD, SEN, "Clinical Professor of Dentistry (tenure series)"),
    "031": (OTHER_ACAD, SEN, "Clinical Professor of Dentistry (tenure series)"),
    "040": (OTHER_ACAD, SEN, "Supervisor of Physical Education"),
    "041": (OTHER_ACAD, SEN, "Supervisor of Physical Education"),
    "042": (OTHER_ACAD, SEN, "Recall (retired faculty)"),
    "114": (LRE, SEN, "Acting Professor"),
    "124": (LRE, NONSEN, "Acting Assistant Professor (non-senate)"),
    "210": (LRE, SEN, "Teaching Professor (SOE/PSOE; formerly LSOE)"),
    "211": (LRE, SEN, "Teaching Professor (SOE/PSOE; formerly LSOE)"),
    "212": (OTHER_ACAD, SEN, "Recall (retired faculty)"),
    "214": (LRE, SEN, "Teaching Professor (SOE/PSOE; formerly LSOE)"),
    "216": (OTHER_ACAD, SEN, "Emeritus (without salary)"),
    "221": (OTHER_ACAD, SEN, "Teaching Professor (SOE/PSOE; formerly LSOE)"),
    "224": (LRE, SEN, "Teaching Professor (SOE/PSOE; formerly LSOE)"),
    "225": (FLECT, LECT, "Lecturer"),
    "311": (CIA, SEN, "Professor In Residence (Senate; health sciences)"),
    "316": (OTHER_ACAD, SEN, "Emeritus (without salary)"),
    "317": (CIA, SEN, "Professor of Clinical X (Senate; health sciences)"),
    "323": (CIA, NONSEN, "Visiting Professor"),
    "335": (CIA, NONSEN, "Adjunct Professor / Instructor"),
    "341": (CIA, NONSEN, "Health Sciences Clinical Professor / Instructor"),
    "346": (OTHER_ACAD, NONSEN, "Clinical Professor - Volunteer (without salary)"),
    "357": (FLECT, LECT, "Instructional Assistant (Supv Teacher Ed / Field Work / Summer Session)"),
    "426": (STUDENT_ACAD, STINS, "Teaching Assistant / Fellow"),
    "436": (STUDENT_ACAD, STRES, "Graduate Student Researcher"),
    "446": (RESIDENTS, MED, "Medical Intern / Resident"),
    "456": (STUDENT_ACAD, STINS, "Reader / Tutor"),
    "467": (STUDENT_ACAD, STINS, "Associate In (graduate student instructor)"),
    "520": (LRE, SEN, "Agronomist / Astronomer (AES & observatories)"),
    "521": (OTHER_ACAD, SEN, "Agronomist / Astronomer (AES & observatories)"),
    "522": (OTHER_ACAD, SEN, "Recall (retired faculty)"),
    "523": (OTHER_ACAD, NONSEN, "Visiting Professor"),
    "524": (OTHER_ACAD, SEN, "Agronomist / Astronomer (AES & observatories)"),
    "530": (LRE, SEN, "Agronomist / Astronomer (AES & observatories)"),
    "531": (LRE, SEN, "Agronomist / Astronomer (AES & observatories)"),
    "532": (OTHER_ACAD, SEN, "Recall (retired faculty)"),
    "533": (OTHER_ACAD, NONSEN, "Visiting Professor"),
    "534": (OTHER_ACAD, SEN, "Agronomist / Astronomer (AES & observatories)"),
    "541": (OTHER_ACAD, ACRES, "Professional Researcher"),
    "542": (OTHER_ACAD, ACRES, "Professional Researcher"),
    "543": (OTHER_ACAD, ACRES, "Professional Researcher"),
    "551": (OTHER_ACAD, ACRES, "Specialist (research series)"),
    "553": (OTHER_ACAD, ACRES, "Specialist (research series)"),
    "557": (OTHER_ACAD, ACRES, "Specialist (research series)"),
    "566": (OTHER_ACAD, ACRES, "Other Research (fellows, investigators, visiting scholars)"),
    "575": (POSTDOC, ACRES, "Postdoctoral Scholar"),
    "577": (POSTDOC, ACRES, "Postdoctoral Scholar"),
    "581": (OTHER_ACAD, ACRES, "Project Scientist"),
    "583": (OTHER_ACAD, ACRES, "Project Scientist"),
    "621": (OTHER_ACAD, OTHACAD, "Librarian"),
    "623": (OTHER_ACAD, OTHACAD, "Librarian"),
    "627": (OTHER_ACAD, OTHACAD, "Librarian"),
    "723": (OTHER_ACAD, OTHACAD, "Cooperative Extension Advisor / Specialist"),
    "728": (OTHER_ACAD, OTHACAD, "Cooperative Extension Advisor / Specialist"),
    "729": (OTHER_ACAD, OTHACAD, "Cooperative Extension Advisor / Specialist"),
    "825": (OTHER_ACAD, LECT, "Continuing Educator (UNEX; Unit 18)"),
    "828": (OTHER_ACAD, OTHACAD, "University Extension (UNEX) teacher / staff"),
    "927": (OTHER_ACAD, OTHACAD, "Miscellaneous academic title"),
    "928": (OTHER_ACAD, OTHACAD, "Miscellaneous academic title"),
    "999": (OTHER_ACAD, OTHER, "Supplemental pay code"),
    "S21": (OTHER_ACAD, ACADMIN, "Dean"),
    "S24": (OTHER_ACAD, ACADMIN, "Dean"),
    "S26": (OTHER_ACAD, ACADMIN, "Dean"),
    "S27": (OTHER_ACAD, ACADMIN, "Provost / Vice Provost (college or campus)"),
    "S31": (OTHER_ACAD, ACADMIN, "Director (academic unit)"),
    "S34": (OTHER_ACAD, ACADMIN, "Director (academic unit)"),
    "S44": (OTHER_ACAD, OTHACAD, "Academic Coordinator"),
    "S46": (OTHER_ACAD, OTHACAD, "Academic Coordinator"),
    "S56": (OTHER_ACAD, OTHACAD, "Academic Administrator"),
    "S61": (OTHER_ACAD, ACADMIN, "Department Chair"),
    "S64": (OTHER_ACAD, ACADMIN, "Department Chair"),
}
# CTOs that are grab-bags; the regex rules decide those titles, the CTO is
# only recorded for reference.
CTO_NO_CLASSIFY = {"927", "928", "999"}

# ---------------------------------------------------------------------------
# Title normalisation.  UCPath/Career Tracks append bargaining-unit,
# grandfathering, per-diem and site codes to otherwise identical titles
# (ADMIN OFCR 2 -> ADMIN OFCR 2 CX).  These are stripped to get base_title.
# ---------------------------------------------------------------------------
VARIANT_SUFFIX_WORDS = [
    "NON REP", "NON-REP", "NONREP", "WORK STUDY", "NON UC", "PAC12", "ANR", "MSP",
    "NCT", "GF", "CX", "HX", "RX", "TX", "SX", "KX", "SV", "SU", "RP", "HC", "OP",
    "CEH", "LAK", "LOM", "BYA", "PLA", "PD", "NEX", "EX", "WKSTY", "(WOS)",
]
_SUFFIX_RE = re.compile(
    r"( (?:" + "|".join(re.escape(w) for w in VARIANT_SUFFIX_WORDS) + r"))+$")


def norm(title):
    return re.sub(r"\s+", " ", (title or "").strip().upper())


def base_title(title):
    t = norm(title)
    t = _SUFFIX_RE.sub("", t)
    t = re.sub(r"[-/](NON REP|NON-REP|NONREP|REP|OV|FNO|CMTY)$", "", t)
    return t.strip()


# ---------------------------------------------------------------------------
# Family / level, used to find promotions.
# ---------------------------------------------------------------------------
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9}
RANK_WORDS = [  # (regex, level)
    (r"JR|JUNIOR", 0), (r"ASST|ASSISTANT|AST", 1), (r"ASSOC|ASSOCIATE|ASC", 2),
]


def family_level(title):
    """Return (family, level) or (None, None).

    Levels are only comparable within a family:
      Career Tracks / numbered titles:  HR MGR 3 -> ('HR MGR', 3)
      Roman numerals:                   ANALYST III -> ('ANALYST', 3)
      Professor-style ranks:            ASST PROF-AY -> ('PROF-AY', 1), PROF-AY -> 3
      Lecturers:                        LECT-AY -> ('LECT-AY', 1), -CONTINUING 2, SR 3
      Teaching professors:              LECT PSOE-AY 1, LECT SOE-AY 2, SR LECT SOE-AY 3
                                        (same family as ASST/ASSOC/PROF OF TEACH-AY)
      SR / PRN / LD suffixes:           COOK AST 0, COOK 1, COOK SR 2, COOK PRN|LD 3
    """
    t = base_title(title)
    if not t:
        return None, None
    # Lecturer series (Unit 18)
    m = re.match(r"^(SR )?LECT(-AY|-FY)?(-1/9|-1/10)?(-CONTINUING)?$", t)
    if m:
        fam = "LECT" + (m.group(2) or "")
        return fam, (3 if m.group(1) else 2 if m.group(4) else 1)
    m = re.match(r"^(PRE-SIX YR APPT|CONTINUING APPT-TEMP AUG|CONTINUING APPT-TEMP-AUG)", t)
    if m:
        return "LECT", (1 if m.group(1).startswith("PRE") else 2)
    # Teaching professor series (LSOE -> Professor of Teaching, renamed 2024)
    m = re.match(r"^(ACT )?(SR )?LECT (P?SOE)(-AY|-FY|-HCOMP)?(?:-1/9|-1/10|-100%|-PART TIME)?(-B/E/E|-LAW)?$", t)
    if m:
        basis = (m.group(4) or "-AY") + (m.group(5) or "")
        lvl = 3 if m.group(2) else (2 if m.group(3) == "SOE" else 1)
        return "PROF OF TEACH" + basis, lvl
    m = re.match(r"^(ACT )?(ASST |ASSOC )?PROF (OF )?TEACH(-AY|-FY|-HCOMP)?(?:-1/9|-1/10)?(-B/E/E|-LAW)?$", t)
    if m:
        basis = (m.group(4) or "-AY") + (m.group(5) or "")
        lvl = 1 if m.group(2) == "ASST " else 2 if m.group(2) == "ASSOC " else 3
        return "PROF OF TEACH" + basis, lvl
    # Numbered (Career Tracks) titles: "... 3", possibly followed by SUPV etc.
    m = re.match(r"^(?P<fam>.+?)[ ,]+(?P<lvl>\d)(?P<rest>( (SUPV|EX|NEX))*)$", t)
    if m:
        return (m.group("fam") + m.group("rest")).strip(), int(m.group("lvl"))
    # Roman numerals (pre-2012 vocabulary): "ANALYST III", "NURSE, CLINICAL II"
    m = re.match(r"^(?P<fam>.+?)[ ,-]+(?P<lvl>I{1,3}|IV|V|VI{0,3}|IX)"
                 r"(?P<rest>(-| - |, |/)(SUPERVISOR|SUPVR|SUPERV\.?|SUP|PER DIEM|NON-REP|NON UC|MC))?$", t)
    if m and m.group("lvl") in ROMAN:
        return (m.group("fam") + (m.group("rest") or "")).strip(", -"), ROMAN[m.group("lvl")]
    # Professor-style ranks, e.g. HS ASST CLIN PROF-HCOMP, ASSOC RES-FY, JR SPECIALIST
    m = re.match(r"^(?P<pre>(ACT |ACTING |VIS |VST |VISITING |HS |ACT/INTERIM )*)"
                 r"(?P<rank>(JR|JUNIOR|ASST|ASSISTANT|ASSOC|ASSOCIATE) )?(?P<rest>.+)$", t)
    if m:
        rest, rank = m.group("rest"), (m.group("rank") or "").strip()
        ranked_family = re.match(r"^(PROF|PROFESSOR|CLIN PROF|CLIN INSTR|CLINICAL PROF|RES|RESEARCH|"
                                 r"PROJ|PROJECT|SPECIALIST|LIBRARIAN|COOP EXT|ADJ|ADJUNCT|AGRON|ASTRON|"
                                 r"DEAN|VICE|VP|UNIV LIBRARIAN|LAW LIBRARIAN|CURATOR|COORD PUB)", rest)
        if ranked_family and (rank or " " not in rest or re.match(r"^(PROF|RES|SPECIALIST|LIBRARIAN|"
                                                                 r"CLIN|PROJ|ADJ|AGRON|ASTRON|COOP)", rest)):
            fam = (m.group("pre") + rest).strip()
            # Instructors sit below assistant professors in the same series.
            if re.search(r"\bINSTR\b", fam) and rank == "":
                fam = re.sub(r"\bINSTR\b", "PROF", fam)
                return fam, 0
            lvl = 3
            for pat, l in RANK_WORDS:
                if rank and re.fullmatch(pat, rank):
                    lvl = l
            return fam, lvl
    # SR / PRN / LD / AST suffixes (PSS titles)
    m = re.match(r"^(?P<fam>.+?)(?: |, )(?P<rank>AST|ASST|SR|SENIOR|PRN|PRIN|PRINCIPAL|LD|LEAD)(?:, MC)?$", t)
    if m:
        r = m.group("rank")
        lvl = 0 if r in ("AST", "ASST") else 2 if r in ("SR", "SENIOR") else 3
        return m.group("fam"), lvl
    return t, 1


# ---------------------------------------------------------------------------
# Explicit rules.  (rule_id, regex, category, secondary, confidence, note)
# Matched with re.search against the normalised (upper-case) title, in order.
# A secondary of None means "work it out from the title" (see secondary_for).
# ---------------------------------------------------------------------------
H = "high"
M = "medium"
L = "low"

RULES = [
    # --- students -----------------------------------------------------------
    ("G01", r"^(TEACHG|TEACHING) (ASST|ASSISTANT|ASST STPD|FELLOW)", STINS, "Teaching Assistant / Fellow", H, ""),
    ("G02", r"^(SPECIAL )?READER|^(REMD? |REM )?TUT(OR)?\b|^TUT-|^REM TUTOR", STINS, "Reader / Tutor", H, ""),
    ("G03", r"^ASSOC IN\s*_|^ASSOC IN _|^ACT INSTR-GRAD STDNT|^K-12 ASST-NON GSHIP", STINS,
     "Associate In (graduate student instructor)", H, ""),
    ("G04", r"^GSR\b|^GRAD STDNT RES|^GRADUATE FELLOW|^GRAD(UATE)? RESEARCH FELLOW|^GRADUATE STUDENT RESEARCH|^STDT RESEARCHER|^STUDENT RESEARCH ASS",
     STRES, "Graduate Student Researcher", H, ""),
    ("G05", r"^STDT [1-4]\b|^STUDENT ASSISTANT\b|^SPC STDT|^SPECIAL STUDENT ASSISTANT|^STDT AST \d",
     STU, "Student Assistant", H, ""),
    ("G06", r"^STDT (VOLUNTEER|AID OUTSIDE|ACTIVITIES APPT|INTERN|RECR\b|RECREATION|RSDNC HALLS|EVENTS|"
            r"PEER CNSLR|CAMP PRG|IT\b|INTRA SPORTS|CLIN\b|ARTIST|PHARMACY INTERN)|"
            r"^STUDENT (AID|INTERN|\(MISC|VOLUNTEER|NURSE|LIBRARY)|^STDT\. LIBR|^MISC \(STUDENT\)|"
            r"^APPOINTED OFFICIAL,STU|^ELECTED OFCR STDT|^RESIDENT ASST$|^RESIDENT ASSISTANT$|^CWSP",
     STU, "Student Employee (other)", H, ""),

    # --- senior management group ----------------------------------------------
    ("S01", r"^(PRESIDENT OF THE UNIV|PRESIDENT|PRES|CHANCELLOR|CHAN|CHANCELLOR AND DEAN)$", SMG, "President / Chancellor", H, ""),
    ("S02", r"^(CEO|COO|CFO|CIO|CMO|CNO) (MED CTR|PHYSCN)|^(CHF|CHIEF) [A-Z /&]*EXEC ME$|^PRES SVP|^SVP (CHILD|ADULT|CSTO|HS)|"
            r"^UCSF EVP|^EXEC VP (PHYSCN|UC HEALTH)|^CFO HEALTH|^VC (CFO HEALTH|AND DEAN SOM)|^EXEC DIR MED|"
            r"^VP (HS|HEALTH)\b|^ASC VP (HS|CHF)\b|^AST VP .*HEALTH",
     SMG, "Health System Executive", H, "UC Health / medical center executive"),
    ("S03", r"^(ASC|ASSOC|ASSOCIATE|AST|ASST|ASSISTANT|ACT/INTERIM ASSOC|ACT/INTERIM ASST) "
            r"(VP|VICE PRES|VICE CHAN|V CHAN)|^VICE CHAN (AST|ASC)$|^PROVOST AST$|^AVP\b|^ASSO V CHAN",
     SMG, "Associate / Assistant Vice Chancellor or Vice President", M,
     "Campus AVC/AVPs are often MSP rather than SMG; UCOP Associate VPs are usually SMG. Split on secondary if needed."),
    ("S04", r"^EXEC (VC|VICE CHANC|VICE PROVOST)|^PROVOST EXEC VP|^PROVOST( FUNC AREA| \(FUNCTIONAL AREA\))$",
     SMG, "Executive Vice Chancellor / Provost", H, ""),
    ("S05", r"^(SR |SENIOR )?VICE CHAN(C|CELLOR)?\b|^VC\b|^SR VICE CHAN", SMG, "Vice Chancellor", H, ""),
    ("S06", r"^(SVP|EVP|EXEC VP|SR VP|SENIOR VICE PRES|EXECUTIVE VICE PRES|VICE PRES|VP)\b", SMG, "Vice President", H, ""),
    ("S07a", r"GEN(ERAL)? COUNSEL.*SECR|^SECRETARY,EXECUTIVE", SUPPORT, "Administration, Analysis & Clerical", H,
     "executive secretary in the General Counsel's office"),
    ("S07", r"^(GEN COUNSEL|GENERAL COUNSEL|CHF CAMPUS COUNSEL|CAMPUS COUNSEL|DEPUTY GEN COUNSEL|"
            r"CHF DEPUTY GENERAL COUNSEL|HS COUNSEL CHF|CHIEF CAMPUS COUNSEL)",
     SMG, "General Counsel / Campus Counsel", H, "Chapter 6 lists campus counsels as SMG"),
    ("S08", r"^(SECR OF THE REGENTS|TREASURER OF THE REGENTS|AST TREASURER OF THE REGENTS|CHIEF INVESTMENT|"
            r"COO INV SVC OCIO|SR MGN DIR|SR MANAGING DIR|ADVISOR TO PRESIDENT|SPC AST TO PRESIDENT|"
            r"DEPUTY TO (VICE PRES|SVP)|CHIEF FINANCIAL OFFICER|CHIEF INFORMATION OFFICER|CFO|CIO|COO|CEO)\b",
     SMG, "Chief Officer / Regents & UCOP Officers", M, ""),
    ("S09", r"-EXEC$|\)-EXEC|^EXEC (OFCR|DIR|ASC DIR|DEAN|ASC DEAN|ASC VC|SPC AST) ?FUNC AREA|^FUNC AREA EXEC|"
            r"^DIR EXEC$|^EXEC DIR EXEC$|^SVP DESIGNATE|^EXEC DEAN",
     SMG, "Executive (legacy SMG title, pre-2014)", M,
     "Pre-2014 payroll used generic '(FUNCTL AREA) ...-EXEC' titles for SMG members"),
    ("S10", r"^UNCLASSIFIED SR MGR CT$", SMG, "Unclassified senior manager", L, "UCOP; verify"),

    # --- faculty --------------------------------------------------------------
    ("F00", r"NON-SENATE ACAD EMERITUS|^RECALL NON-FACULTY", OTHACAD, "Recall / Emeritus (non-faculty academic)", H, ""),
    ("F00b", r"^STAFF EMERITUS", OTHER, "Emeritus (staff, without salary)", M, ""),
    ("F00c", r"^(VST |VIS )?(ASST |ASSOC )?RES(EARCH)? ?-+.*RECALL", ACRES, "Professional Researcher", H, "recalled researcher"),
    ("F00d", r"SPECIALIST IN (THE )?A\.?E\.?S", ACRES, "Specialist (research series)", H, "Specialist in the AES (CTO 557)"),
    ("F01", r"EMERITUS", SEN, "Emeritus (without salary)", H, "WOS = without salary"),
    ("F02", r"^RECALL TEACHING NON-SENATE|^HS .*RECALL", NONSEN, "Recall (retired non-senate faculty)", H, ""),
    ("F03", r"^RECALL (FACULTY|TEACHING|HCOMP)$|^RECALL \(WOS\)|RECALLED|^_+ RECALL|-RECALL\b|RECALL-VERIP|RECALL PGM|^RECALL$",
     SEN, "Recall (retired faculty)", H, ""),
    ("F04", r"^(ACT )?(SR )?LECT[- ](P)?SOE|^ACT LECT PSOE|^(ACT )?(ASST |ASSOC )?PROF (OF )?TEACH|^ACT (ASST|ASSOC) PROF TEACH|"
            r"^(SR )?LECT W/SEC|LECTURER.*(SOE|SECURITY OF EMPL|W/SEC)",
     SEN, "Teaching Professor (SOE/PSOE; formerly LSOE)", H,
     "Lecturer with (Potential) Security of Employment; renamed Professor of Teaching series in 2024. Senate members."),
    ("F04b", r"CLIN(ICAL)? PROF(ESSOR)? OF LAW", NONSEN, "Clinical Professor (law school)", M, "UC Law SF clinical faculty"),
    ("F05", r"^HS (ASST |ASSOC |ASSISTANT |ASSOCIATE )?CLIN(ICAL)? (PROF|INSTR)", NONSEN,
     "Health Sciences Clinical Professor / Instructor", H, ""),
    ("F06", r"^(ASST |ASSOC |ASSISTANT |ASSOCIATE )?PROF(ESSOR)? OF CLIN", SEN, "Professor of Clinical X (Senate; health sciences)", H,
     "Senate members per Standing Order 105.1(a); UC reporting groups them with clinical faculty (uc_academic_group)"),
    ("F07", r"^(ASST |ASSOC |ASSISTANT |ASSOCIATE )?(PROF(ESSOR)?|INSTR) IN RES", SEN, "Professor In Residence (Senate; health sciences)", H,
     "Senate members per Standing Order 105.1(a); UC reporting groups them with clinical faculty (uc_academic_group)"),
    ("F08", r"^(ASST |ASSOC |ASSISTANT |ASSOCIATE )?ADJ(UNCT)?\.? (PROF|INSTR)", NONSEN, "Adjunct Professor / Instructor", H, ""),
    ("F09", r"^(VIS|VST|VSTG|VTG|VISITING) .*(PROF|INSTR)|^REGENTS.? (PROF|LECT)", NONSEN, "Visiting Professor", H, ""),
    ("F10", r"^(ASST |ASSOC )?CLIN (PROF|INSTR)-VOL|^CLIN INSTR-VOL|CLIN(ICAL)? PROF.*VOL", NONSEN,
     "Clinical Professor - Volunteer (without salary)", H, ""),
    ("F11", r"^CLIN PROF-DENT|CLINICAL PROF.*DENT", SEN, "Clinical Professor of Dentistry (tenure series)", M,
     "CTO 030/031; tenure-track clinical dentistry series"),
    ("F12", r"AGRON|ASTRON|IN THE (AES|A\.E\.S)", SEN, "Agronomist / Astronomer (AES & observatories)", H,
     "Agricultural Experiment Station / observatory equivalents of the professor series"),
    ("F13", r"^SUPV (OF )?PHYS(ICAL)? ED|SUPERVISOR OF P\.?E", SEN, "Supervisor of Physical Education", M, ""),
    ("F14a", r"^(ACT |ACTING )(ASST |ASSISTANT )PROF(ESSOR)?(-| - |$)", NONSEN, "Acting Assistant Professor (non-senate)", H,
     "CTO 124 'Acting Professor - Non-Senate'; counted in UC's ladder-rank-and-equivalent group"),
    ("F14", r"^(ACT |ACTING )(ASSOC |ASSOCIATE )?PROF(ESSOR)?(-| - |$)", SEN, "Acting Professor", H,
     "Acting professors are in UC's ladder-rank-and-equivalent group (CTO 114/124)"),
    ("F15", r"^(ASST |ASSOC |ASSISTANT |ASSOCIATE )?PROF(ESSOR)?(-| - |$)|^UNIV(ERSITY)? PROF|"
            r"^INSTR(UCTOR)?(-| - )(AY|FY|HCOMP|SFT|ACAD)|^PROF-10 MONTHS|^PROFESSOR|^(ASSOCIATE |ASSISTANT )?PROF(ESSOR)? OF LAW",
     SEN, "Ladder Rank (Professor series)", H, ""),
    ("F16", r"^(RESEARCH PROFESSOR|RES PROF)", ACRES, "Other Research (fellows, investigators, visiting scholars)", M,
     "Research Professor titles (e.g. Miller Institute) are not ladder rank"),

    # --- lecturers (Unit 18) ----------------------------------------------------
    ("L01", r"^SR LECT", LECT, "Senior Lecturer (Continuing)", H, ""),
    ("L02", r"^LECT(URER)?[- /].*CONTINUING|^LECTURER-.*CONTINUING", LECT, "Lecturer (Continuing)", H, ""),
    ("L03", r"^LECT(URER)? IN SUMMER|^LECTURER IN SUMMER", LECT, "Summer Session Lecturer", H,
     "CTO 357; Unit 18 since the 2022 MOU"),
    ("L04", r"^LECT(URER)?[- /].*(MISC|PART[ -]TIME|WOS|NON-REP)|^LECT/SR LECT|^LECTR/|^LECTURER/SR", LECT,
     "Lecturer (misc / part-time / without salary)", M, "CTO 928 miscellaneous; small or zero pay"),
    ("L05", r"^LECT(URER)?(-| - |$)|^LECTURER$", LECT, "Lecturer (Pre-Six)", H, ""),
    ("L06", r"^PRE-SIX YR APPT", LECT, "Lecturer (Pre-Six)", H, "Temporary augmentation title in the lecturer series"),
    ("L07", r"^CONTINUING APPT-TEMP", LECT, "Lecturer (Continuing)", H, "Temporary augmentation title in the lecturer series"),
    ("L08", r"^(SUPV TEACHER ED|COORD FLD WK|FLD WK (SUPV|CONSULT)|SUPERVISOR OF TEACHER ED|COORD(INATOR)?,? (OF )?FIELD|"
            r"FIELD WORK)", LECT, "Instructional Assistant (Supv Teacher Ed / Field Work / Summer Session)", H,
     "CTO 357; covered by the UC-AFT Unit 18 MOU"),
    ("L10", r"^LW&R|^LEGAL (WRITING|RSCH)|^BAR PREP", LECT, "Lecturer (UC Law SF / Hastings; not Unit 18)", M, ""),
    ("L09", r"^(TEACHER-SPEC PROG|TEACHER - SPECIAL PROG|CHILD DEV DEMO LECT|CHILD DEVELOP.*DEMO|DEMO TEACHER|"
            r"DEMONSTRATION TEACHER|TEACHER-LHS|TEACHER - LHS|CONTINUING EDUCATOR|SUBSTITUTE TEACHER)",
     LECT, "Other Unit 18 instructional title (Teacher-Special Programs, Demo Teacher, Continuing Educator)", M,
     "Listed in the UC-AFT Unit 18 recognition clause; verify against the current MOU"),

    # --- academic research ------------------------------------------------------
    ("R01", r"POSTDOC|POST-DOC|POSTDOCTORAL|^INTRM POSTDOC", ACRES, "Postdoctoral Scholar", H, ""),
    ("R02", r"\bPROJ(ECT)?[ _]*(SCIENTIST|SCNTST|_{2,})|^PROJECT_+|^(AST|ASST|ASSOC) PROJECT_+", ACRES, "Project Scientist", H, ""),
    ("R03", r"^(VIS |VST |VISITING )?(ASST |ASSOC |ASSISTANT |ASSOCIATE |AST )?(RES|RESEARCH)( PROF)?"
            r"(-|\s*[_-]{2,}|\s*\(WOS\)| NEX| SCRIPPS|$)|^RES ASSOC|^RES FELLOW|^RESEARCH _",
     ACRES, "Professional Researcher", H, "Professional Research series (CTO 541-543)"),
    ("R04", r"^(JR |JUNIOR |ASST |ASSISTANT |ASSOC |ASSOCIATE )?SPECIALIST( NEX| \(WOS\)| IN A\.?E\.?S\.?|$)",
     ACRES, "Specialist (research series)", H, "Academic Specialist series (CTO 551/557), not Career Tracks 'SPEC'"),
    ("R05", r"FACULTY FELLOW RES|CHI GREEN SCHOLAR|HHMI INVESTIGATOR|LUDWIG INVESTIGATOR|^VIS(ITING)? SCHOLAR|"
            r"^VIS SCIENTIST|^VISITOR|^VIS (ASST |ASSOC )?RES|^VIS .*SCIENTIST",
     ACRES, "Other Research (fellows, investigators, visiting scholars)", M, ""),

    # --- other academic ----------------------------------------------------------
    ("A01", r"LIBRARIAN", OTHACAD, "Librarian", H, ""),
    ("A02", r"COOP EXT|COOPERATIVE EXT|COOP\. EXT", OTHACAD, "Cooperative Extension Advisor / Specialist", H, ""),
    ("A03", r"^(ACT )?ACAD(EMIC)? COORD", OTHACAD, "Academic Coordinator", H, ""),
    ("A04", r"^ACADEMIC ADMINISTRATOR", OTHACAD, "Academic Administrator", H, ""),
    ("A05", r"TEACHER.*UN(IV\.? )?EX|TEACHER - UNIV|^SPEAKER-UNEX|^COURSE AUTHOR|^PROG(RAM)? COORD(INATOR)?( NEX)?$",
     OTHACAD, "University Extension (UNEX) teacher / staff", H, "CTO 828; by-agreement Extension instructors, not Unit 18"),
    ("A06", r"^(GEFFEN |PREUSS )?K-12 (INSTR|COUNSELOR|DAILY)|^UCDC EDUCATOR", OTHACAD, "K-12 lab school instructor", H, ""),
    ("A07", r"^(FACULTY|ACADEMIC) ASST TO", ACADMIN, "Faculty / Academic Assistant to executive", H, ""),
    ("A07b", r"CURATOR(IAL)? (\d|MGR|SUPV|MANAGER)|^CURATOR \d", PROF, "Library & Museum Staff", H, "Career Tracks curatorial staff, not the academic curator series"),
    ("A08", r"^(ASSOC |ASST |ASSISTANT |ASSOCIATE )?COORD(INATOR)? PUB(LIC)? PROG|^(ASSOC |ASST )?CURATOR\b|^(ASSOC )?FLD PROG SUPV|"
            r"^MILITARY/AIR SCI|^EDUCATOR \(WOS\)|^FACULTY (CONSULTANT|ADVISOR)|^GRADUATE ADVISOR|^OMBUDSMAN-ACAD|^CHAIR-SEN|"
            r"^(ASSOCIATE |ASSISTANT )?HEAD OF|^SENIOR PRECEPTOR|^ACADEMIC PROGRAMS ADVISOR|^PROG DIR--SCRIPPS|"
            r"^(ASSOC |ASST )?DIRECTOR-(INTERNATL|EAP)|^ACAD ASST TO T/DIR|^FACULTY ADMIN TRANSITION|^ACADEMIC APPT$",
     OTHACAD, "Miscellaneous academic title", M, "CTO 927/928"),

    # --- academic administration ---------------------------------------------------
    ("D01", r"^(ACT/INTERIM )?(DEAN|ACAD DEAN|ACADEMIC DEAN|DEAN UNIV EXT|DEAN-EXTENDED LEARNING|DIVISIONAL DEAN|"
            r"ASSOC DIVISIONAL DEAN|VICE DEAN.*|DEAN-.*)$|^DEAN \(|^9DIR ACAD", ACADMIN, "Dean", H, ""),
    ("D02", r"^DEAN (ASC|AST)$|^(ASST|ASSISTANT) DEAN \((STAFF|FUNCT)|^ASST DEAN \(FUNCTIONAL", MGMT,
     "Assistant / Associate Dean (staff)", M, "Staff (MSP) assistant/associate dean titles"),
    ("D03", r"^(ACT/INTERIM )?(ASSOC|ASSOCIATE|ASST|ASSISTANT) DEAN|^ASSOC\. ACAD DEAN|^ACT/INTERIM ASSISTANT DEAN",
     ACADMIN, "Associate / Assistant Dean", H, ""),
    ("D04", r"^(ACT/INTERIM )?(ASSOC |ASST |ASSOCIATE |ASSISTANT )?(COLLEGE PROVOST|VICE PROVOST)|^VICE PROVOST",
     ACADMIN, "Provost / Vice Provost (college or campus)", H, ""),
    ("D05", r"^(ACT/INTERIM )?DEPARTMENT (VICE )?CHAIR|^DEPT CHAIR", ACADMIN, "Department Chair", H, ""),
    ("D06", r"^(ACT/INTERIM )?(ASSOC |ASST )?DIRECTOR$", ACADMIN, "Director (academic unit)", M,
     "Academic director series (CTO S31); staff MSP directors use DIR / DIRECTOR I-III"),
    ("D07", r"^(AST|ASST) TO (DEAN|THE ____|CHAIR)|^ASSISTANT TO THE", SUPPORT, "Administration, Analysis & Clerical", H,
     "Assistant to dean/director/chair (staff)"),

    # --- health care ---------------------------------------------------------------
    ("H01", r"^(RESID|RESIDENT|RES) PHYS|^CHIEF RESID|^POST DDS|^POST-DDS|_+ POST DDS|^OTH(ER)? POST-MD|^OTH POST DDS|"
            r"^HEAL OTH POST-MD|^INTERN-VET|^RESID(ENT)?-VET|^NON-PHYS CLIN TRAIN|^INTERN-CLIN(ICAL)? PSYCH|"
            r"^PHYSICAL THERAPY RESID|^STIPEND-(RESID|OTH POST-MD|OTHER POST-MD)|^PHARMACY RESID|^DENTAL RESID|^INTERN-PHARM",
     MED, "Medical Intern / Resident", H, "CTO 446"),
    ("H02", r"^MED CTR |, MC$| MC$|^HOSP|HOSPITAL|NURSE|NURSING|PHYSCN|PHYSICIAN|PHARMAC|^CLIN(ICAL)?[ ,]|CLINIC\b|"
            r"RADLG|RADIOLOG|RAD THER|MRI TCHNO|CT TCHNO|NUC MED|ULTRASOUND|SONOGRAPH|HISTO|CYTO|PATHOLOG|PHLEBOT|"
            r"STERILE PROC|SURGICAL|SURGERY|ANESTHE|RESP THER|RESPIRATORY|OCCUPATIONAL THER|PHYS(ICAL)? THER|SPEECH PATH|"
            r"AUDIOLOG|DIETIT|DIETARY|NUTRITION|^PAT(IENT)? |^PAT(IENT)?,|PATIENT|ADMITTING|CARE PARTNER|CLIN PARTNER|"
            r"PARTNER, CLIN|PARTNER,? ?ADMIN CARE|^MED(ICAL)?\b|ASSISTANT, MEDICAL|AMBUL CARE|REVENUE CYCLE|QLTY IMPV HC|"
            r" HC( |$)|CASE MGR|MANAGER, CASE|SOCIAL WORK|PSYCHOLOG|PSYCHIATR|BEH HEALTH|COUNSELING PSYCH|CNSLNG PSYCH|"
            r"OPTOMETR|DENTIST|DENTAL|ORTHOPT|PERFUSION|\bEEG\b|\bEKG\b|ELECTROCARDIO|ECHOCARDIO|CARDIO|DIALYSIS|"
            r"EMERGENCY TRAUMA|TELEMETRY|ENDOSCOPY|TRANSPLANT|INFECTION|HEALTH INFO|HEALTH PROFNS|HEALTH TCHN|"
            r"HEALTH EDUCATOR|HEALTH PLAN|HEALTH CARE|HEALTHCARE|HOME HEALTH|HOSPICE|BIOMED EQUIP|MIDWIFE|GENETIC CNSLR|"
            r"CHAPLAIN|CHILD LIFE|ACUPUNCT|ART THERAP|MUSIC THERAP|RECREATION THER|ORTHOT|PROSTHET|^STDT HEALTH|"
            r"STUDENT HEALTH|LAB SCI|TECHNICIAN, HOSPITAL|SCIENTIST, CLINICAL LAB|THERAPIST|BILLER|COLL REPR|"
            r"COLLECTIONS? REPR|COLLECTIONS MGR|^COLL MGR|MED RCDS|MEDICAL RECORDS|CODER|HEALTH SYS|UTILIZATION|CREDENTIAL|"
            r"ADVANCED PRAC|ADV PRACTICE|PRACTICE CRD|^ACCESS (REPR|SUPV|MGR)|REPRESENTATIVE, ACCESS|COUNSELING (ATTORNEY|CTR|1 )|"
            r"^COUNSELING PSYCHOLOGY|MED INTERPRETER|INTERPRETER|ANATOMICAL|MORGUE|AUTOPSY|EMBALM|MENTAL HEALTH|OPTICIAN|"
            r"DOSIMETR|SERVICE PARTNER|SVC PARTNER|THER \d|^CARE (HOSP )?(SUPV|MGR|CRD)|^ANES\b|ANGIOGRAPH|^CLINICIAN",
     MED, None, H, ""),

    # --- athletics -------------------------------------------------------------------
    ("T00", r"(ASC|ASSOC) HEAD COACH", ATH, "Assistant Coach / Coach", H, "Associate head coach"),
    ("T01", r"HEAD COACH", ATH, "Head Coach", H, ""),
    ("T02", r"^RECR(EATION)? COACH", ATH, "Recreation Coach", H, ""),
    ("T03", r"COACH", ATH, "Assistant Coach / Coach", H, ""),
    ("T04", r"^ATH\b|^ATHLETIC|^INTERCOL|INTERN, ATHLETICS", ATH, "Athletics Staff (trainers, managers, professionals)", H, ""),

    # --- staff: fixed-primary families -------------------------------------------------
    ("M01", r"^(EXEC DIR|EXECUTIVE DIRECTOR|EXEC DIRECTOR|MANAGING DIRECTOR)", MGMT, "Executive Director", M,
     "Some UCOP Executive Directors are SMG; campus ones are MSP"),
    ("M02", r"^(DIR|DIRECTOR)( (ASC|AST|I|II|III))?$|^(ASSOC|ASSOCIATE|ASST|ASSISTANT|ACT/INTERIM) DIR(ECTOR)?"
            r"( \((PROF|MGMT|MGT)\))?( \(FUNCTIONAL AREA\))?( ADMIN.*|, .*)?$|^DIRECTOR (\(FUNCTIONAL AREA\)|OF |I$|II$|III$|,)|"
            r"^DIR (UNIV PRESS|CONTINUING|OF |INFO|ASC|AST|,)|^(ASSOC|ASST) DIR \(FUNCTIONAL|^ASSOCIATE DIRECTOR|"
            r"^ASSISTANT DIRECTOR|^ASST DIR |^ASSOC DIR |^DIRECTOR$",
     MGMT, "Director / Associate / Assistant Director (staff)", H, ""),
    ("M03", r"^EXEC ADVISOR MGR", MGMT, "Administration, Analysis & Clerical", H, "Chief-of-staff style manager"),
    ("M04", r"^EXEC ADVISOR", PROF, "Administration, Analysis & Clerical", H, "Executive advisor / chief of staff (MSP professional)"),
    ("M05", r"^(CHF|CHIEF) ", MGMT, None, M, "Chief officer below SMG level"),
    ("M06", r"^(MGR|MANAGER)( \(FUNCTIONAL AREA\)| \d| AST|$)|^MGR,|^MANAGER,", MGMT, "Management (generic title)", H, ""),
    ("M07", r"^ADMIN/COORD/OFFICER|^ADMINISTRATOR$|^ADMINSTRATOR$", PROF, "Administration, Analysis & Clerical", H, ""),
    ("P01", r"POLICE OFCR|POLICE OFFICER|SCRTY OFCR|SECURITY OFFICER|SCRTY GUARD|SECURITY GUARD|POLICE (CADET|DISPATCH)|DISPATCHER",
     SUPPORT, "Police, Security & Safety", H, ""),
    ("P02", r"^(SRA|STAFF RES(EARCH)? ASSOC)\b", PROF, "Research Support (staff)", H, "Staff Research Associate series"),
    ("P03", r"^(RECR|RECREATION) PRG (INSTR|LEADER)|^REC PROGRAM INSTR|^CAMP CNSLR|^COUNSELOR, CAMP|^LIFEGUARD",
     SUPPORT, "Recreation, Child Care & Camps", H, "Mostly part-time / seasonal"),
    ("P04", r"^AMERICORP", OTHER, "Non-employee / program member", M, ""),
    ("P05", r"^SPEC$", PROF, "Administration, Analysis & Clerical", M, "Generic Career Tracks 'SPEC' title"),
    ("P06", r"^HOUSE MGR", PROF, "Development, Communications & Events", H, "Theatre house manager (renamed EVENTS CRD 2); not MSP management"),

    # --- misc / pay codes --------------------------------------------------------------
    ("Z01", r"^$|^#MULTIVALUE$|^9964 - NO DESCRIPTION", OTHER, "Invalid / blank title", H, "data quality problem in the source"),
    ("Z02", r"^(UNCLASSIFIED|MISCELLANEOUS|MISC \(TEMP HELP\))$|^MISC ", OTHER, "Unclassified", M, ""),
    ("Z03", r"STIPEND|SALARY SUPPLEMENTATION|ADDL COMP|SUMMER DIFFERENTIAL|RECRUITMENT ALLOW|HOUSING ALLOW|"
            r"ACADEMIC UPGRADING|ADMIN STIPEND", OTHER, "Supplemental pay code", H,
     "CTO 999: pay codes, not jobs. Exclude from headcounts."),
]

COMPILED_RULES = [(rid, re.compile(rx), cat, sec, conf, note) for rid, rx, cat, sec, conf, note in RULES]

# --- staff function families (secondary category) ---------------------------------
STAFF_FAMILIES = [
    ("Legal & Compliance", r"COUNSEL\b|ATTORNEY|PARALEGAL|LEGAL|ETHICS|CMPLNC|COMPLIANCE|PRIVACY|OMBUD|TITLE IX|"
                           r"INVESTIGAT"),
    ("Police, Security & Safety", r"POLICE|SCRTY|SECURITY|GUARD|DISPATCH|^FIRE |FIRE (CHF|FIGHT|PREV|MARSH)|\bEHS\b|E\.H\.&S|"
                                  r"ENV(IRONMENTAL)? HEALTH|SAFETY|EMERGENCY (MGT|SVC|PREP)|HAZ"),
    ("Information Technology", r"PROGR\b|PROGRAMMER|SYS ADM|SYSTEMS? ADMIN|INFO SYS|BUS SYS|DATA SYS|DATABASE|\bIT\b|COMPUTER|"
                               r"COMPUTING|NETWORK|TCHL PROJECT|BUS TCHL|IT SCRTY|IT ARCHITECT|BUS INTEL|\bWEB\b|SOFTWARE|"
                               r"INFORMATICS|CMPTL AND DATA|BIOINFORMATICS|GEOSPATIAL|\bGIS\b|INFORMATION TECH|TECHNOLOGY|"
                               r"DATA (SCI|ANL|ENGR|MGT)|MULTIMEDIA|AUDIO VIS|AV TCHN|INSTRUCTIONAL DESIGN|EDUC(ATIONAL)? TECH|"
                               r"LRNG TCHL|HELP DESK|DESKTOP|TELECOM|TELEPHONE|SYS INTEGRATION|APPLICATIONS"),
    ("Library & Museum Staff", r"LIBRARY|LIBR\b|LIBR\.|MUSEUM|ARCHIV|CONSERVATOR|GALLERY|EXHIBIT"),
    ("Research Support (staff)", r"\bSRA\b|STAFF RES(EARCH)? ASSOC|\bLAB(ORATORY)?\b|RSCH|RESEARCH|CONTRACTS? (AND|&) GRANTS?|"
                                 r"INSTITUTIONAL RSCH|STATISTIC|SURVEY|ANIMAL|\bVET\b|VETERINAR|NURSERY|AGRICULT|FIELD|MARINE|"
                                 r"SEAMAN|BOAT|SHIP\b|VESSEL|DIVER|OCEAN|OILER|WIPER|DECKHAND|BOSUN|ENGINEER, DEVELOPMENT|DEV ENGR|RSCH AND DEV|SCIENTIST|"
                                 r"SCI \d|TELESCOPE|OBSERVAT|HERBARIUM|GREENHOUSE|CYCLOTRON|REACTOR|MACHINE SHOP|GLASSBLOW"),
    ("Student Services", r"STDT|STUDENT|ADMISSION|FINANCIAL AID|REGISTRAR|CAREER|ACAD ACHIEVE|ACADEMIC ACHIEVE|ACAD ADVIS|"
                         r"ACADEMIC ADVIS|LRNG SKLS|LEARNING SKILLS|CNSLR|COUNSELOR|RSDT|RESIDENCE|RESID DIR|HOUSING|ENROLL|"
                         r"OUTREACH|K TO 1[24]|K-12|CMTY EDUC|COMMUNITY EDUC|PUBL EDUC|PUBLIC EDUC|EDUCATOR|ACAD PRG|"
                         r"ACADEMIC PRG|ACAD PREP|DISABILITY|VETERAN|INTERNATIONAL|ORIENTATION|TESTING|TUTORIAL|EOP|"
                         r"UPWARD BOUND|MENTOR|ADVISOR|SCHOLARSHIP|CHILD DEV|CHILD CARE|CAMP\b|RECR|RECREATION|INTRA SPORTS|"
                         r"INTRAMURAL|FITNESS|AQUAT|SPORTS"),
    ("Development, Communications & Events", r"FUNDRAIS|DEV OFCR|DEVELOPMENT OFF|ALUMNI|EXTERNAL REL|\bCOMM\b|COMMUNICATION|"
                                            r"MEDIA|MARKETING|DIGITAL|PUBL INFO|PUBLIC INFO|PUBLICATION|WRITER|EDITOR|GRAPHIC|"
                                            r"PHOTOG|VIDEO|PRODUCER|BROADCAST|ARTIST|MUSICIAN|PERF ART|EVENTS?\b|PUBLIC PROG|"
                                            r"CONFERENCE|ARTS AND LECTURES|THEAT|SCENE|STAGE|COSTUME|WARDROBE|USHER|TICKET|"
                                            r"BOX OFFICE|DESIGNER|GOVT REL|GOVERNMENT REL|LEGISLAT|PUBLIC AFFAIRS|PUBL AFFAIRS|"
                                            r"ANNUAL GIV|GIFT|DONOR|STEWARDSHIP|PROSPECT|WEB|RADIO|\bTV\b|FILM|PRESS"),
    ("Finance, HR, Purchasing & Business Services", r"FINANC|ACCOUNT|ACCT\b|PAYROLL|BENEFIT|\bHR\b|HUMAN RES|PERSONNEL|"
                                                    r"COMPENSATION|LABOR REL|EMPLOYMENT|EMPLOYEE REL|RECRUIT|TALENT|PROCUREMENT|"
                                                    r"BUYER|PURCHAS|CONTRACT|AUDIT|BUDGET|RISK|SUPPLY CHAIN|MATERIEL|MATERIAL|"
                                                    r"TRAVEL|CASHIER|COLLECTION|BILLING|INVEST|\bINV\b|ABSOLUTE RETURNS|TREASUR|"
                                                    r"\bTAX\b|INSURANCE|BUS OPS|BUSINESS|ORGANIZATIONAL|TRAINER|TRAINING|"
                                                    r"LEARNING (AND|&) DEV|ORG DEV|EQUAL OPP|DIVERSITY|CONTROLLER|LOAN|"
                                                    r"BANK|FUND MGT|RESC MGT|RESOURCE MGT|BENEFITS"),
    ("Custodial, Food, Housing & Hospitality", r"CUSTOD|JANITOR|HOUSEKEEP|LAUNDRY|LINEN|COOK|CHEF|FOOD|DINING|BAKER|DISHWASH|"
                                              r"CATER|KITCHEN|BEVERAGE|BARISTA|CAFE|HOSPITALITY|HOTEL|GUEST|HOUSING|RSDNC HALLS|"
                                              r"CONFERENCE|HOUSE MGR|RESID HALL"),
    ("Facilities, Trades, Grounds & Logistics", r"\bFAC\b|FACILIT|BLDG|BUILDING|MAINT|MECH\b|MECHN|MECHANIC|PHYS PLT|"
                                                r"PHYSICAL PLANT|PLUMB|ELECTRN|ELECTRICIAN|ELECTR\b|ELECTR TCHN|ELECTNR|CARPENT|"
                                                r"PAINT|LOCKSMITH|HVAC|STEAM|BOILER|PLT OPR|PLANT OPR|POWER PLANT|CTRL (SVC|HEAT)|"
                                                r"CONTROLS? TCHN|GROUNDS|GARDEN|IRRIG|PEST|TREE|LANDSCAP|LABORER|AUTO (EQUIP|MECH)|"
                                                r"AUTOMOTIVE|FLEET|TRUCK|DRIVER|VEHICLE|TRANSPORT|PARKING|TRAFFIC|SHUTTLE|MOVER|"
                                                r"WAREHOUSE|STOREKEEP|STORE\b|STORES|MAIL|SHIPPING|RECEIVING|RECYCL|REFUSE|WASTE|"
                                                r"UTILIT|ENERGY|ENGR\b|ENGINEER|ARCHITECT|PLNG|PLANNER|PLANNING|CONSTRUCT|"
                                                r"PROJECT MGT|INSPECTOR|ESTIMATOR|SURVEYOR|\bCAD\b|DRAFT|SIGN|GLAZ|ROOF|SHEET METAL|"
                                                r"WELD|MACHINIST|INSTRUMENT|WINDOW|ELEVATOR|SPACE|CAMPUS PLAN|REAL ESTATE|COGEN|"
                                                r"OPR\b|OPERATOR|REPROGRAPH|PRINT|COPY|BINDERY|EQUIP|SUSTAIN|ENVIRON|CUSTODIAL"),
    ("Administration, Analysis & Clerical", r"ADMIN|ADMSTN|\bANL\b|ANALYST|BLANK|_+ASSISTANT|^-+ASSISTANT|^ASSISTANT\b|^AST\b|"
                                            r"CLERK|SECR|SECRETARY|EXEC AST|EXEC ASST|EXECUTIVE ASS|OFFICE|RECEPTION|TYPIST|"
                                            r"WORD PROC|DATA ENTRY|COORD|\bCRD\b|COORDINATOR|OFCR|OFFICER|\bSPEC\b|SPECIALIST|"
                                            r"PROFL|PROFESSIONAL|\bREPR\b|REPRESENTATIVE|GENERALIST|CNSLT|CONSULTANT|\bPRG\b|"
                                            r"PROGRAM|\bPROG\b|LIAISON|TCHN|TECHNICIAN|\bAID\b|\bAIDE\b|HELPER|WORKER|ATTENDANT|"
                                            r"INTERN|TRAINEE|APPR\b|APPRENTICE|TEMP|ASSISTANT|\bASST\b|\bAST\b|\bSR\b|\bMGR\b|SUPV"),
]
COMPILED_FAMILIES = [(name, re.compile(rx)) for name, rx in STAFF_FAMILIES]

# --- staff primary by role word ---------------------------------------------------------
ROLE_MGMT = re.compile(r"\b(MGR|MANAGER|DIR|DIRECTOR|CHF|CHIEF|SUPT|SUPERINTENDENT)\b")
ROLE_SUPV = re.compile(r"\b(SUPV|SUPVR|SUPERVISOR|SUPERVISING|SUPERV|SUPR|SUPERVISORY|SUP)\b|-SUPERV|SUPVR$|SERGEANT|LIEUTENANT|CAPTAIN|\bLT\b|\bCAPT\b")
ROLE_PROF = re.compile(
    r"\b(ANL|ANALYST|SPEC|SPECIALIST|PROFL|PROFESSIONAL|OFCR|OFFICER|ADVISOR|ADVISER|CNSLR|COUNSELOR|ENGR|ENGINEER|"
    r"ARCHITECT|ADM|ADMINISTRATOR|SCI|SCIENTIST|PROGR|PROGRAMMER|CRD|COORD|COORDINATOR|REPR|REPRESENTATIVE|EDITOR|"
    r"WRITER|DESIGNER|TRAINER|GENERALIST|ACCOUNTANT|AUDITOR|BUYER|PLANNER|ESTIMATOR|INSPECTOR|CURATOR|FUNDRAISER|"
    r"STATISTICIAN|ARTIST|MUSICIAN|PRODUCER|EDUCATOR|CNSLT|CONSULTANT|LIAISON|INVESTIGATOR|RECRUITER|SRA|DEVELOPMENT|"
    r"SURVEYOR|INTERPRETER|TRANSLATOR|PHOTOGRAPHER|MGT SVC|OMBUDS|ATTORNEY|COUNSEL|PARALEGAL|LIBRARIAN|ARCHIVIST|"
    r"CONSERVATOR|ACTUARY|ECONOMIST|GEOLOGIST|CHEMIST|PHYSICIST|BIOLOGIST|ENTOMOLOGIST|AGRONOMIST|VETERINARIAN|"
    r"PILOT|ENGINEER|PSYCHOLOGIST|DIETITIAN|THERAPIST|CONTROLLER|REGISTRAR|OMBUDSMAN|EVALUATOR|PLNR|INSP|"
    r"ADVOCATE|BIBLIOGRAPHER|SPECTROSCOPIST|PSYCHOMETRIST|CARTOGRAPHER|ILLUSTRATOR|MICROSCOPIST|HORTICULTURIST|"
    r"ASSOC|ASSOCIATE|MANAGEMENT|MGT|PLNG|OPS|LEADER|HEAD)\b")


def staff_family(t):
    for name, rx in COMPILED_FAMILIES:
        if rx.search(t):
            return name
    return "General / Unclassified staff"


def staff_primary(t):
    """(category, confidence) for a staff title based on its role word."""
    if ROLE_MGMT.search(t) and not re.search(r"\b(AST|ASST) TO\b", t):
        return MGMT, H
    if ROLE_SUPV.search(t):
        return PROF, H
    if ROLE_PROF.search(t):
        return PROF, H
    if re.search(r"\b(AST|ASST|ASSISTANT|TCHN|TECHNICIAN|WORKER|CLERK|SECR|SECRETARY|OPR|OPERATOR|MECH|MECHN|MECHANIC|"
                 r"HELPER|LABORER|CUSTODIAN|COOK|CHEF|BAKER|GUARD|DRIVER|ATTENDANT|USHER|LIFEGUARD|PAINTER|PLUMBER|"
                 r"ELECTRN|ELECTRICIAN|CARPENTER|LOCKSMITH|GROUNDSKEEPER|GARDENER|STOREKEEPER|MAIL|PRINTER|DISPATCH|"
                 r"CASHIER|RECEPTIONIST|AID|AIDE|ESCORT|TRANSPORT|INTERN|TRAINEE|APPR|APPRENTICE|TYPIST|SEAMAN|"
                 r"DECKHAND|BOATSWAIN|WIPER|OILER|CUSTODIAL|JANITOR|HOUSEKEEPER|WAITER|SERVER|STEWARD|PORTER|"
                 r"MESSENGER|MONITOR|MODEL|PERFORMER|DANCER|ACTOR|SEAMSTRESS|TAILOR|UPHOLSTER|GLAZIER|ROOFER|TEACHER|OILER|WIPER|"
                 r"PROCTOR|CAPTIONIST|NOTETAKER|REFEREE|UMPIRE|MASON|INSTALLER|CAPTAIN|MATE|BOSUN|"
                 r"WELDER|MACHINIST|MILLWRIGHT|PIPEFITTER|STEAMFITTER|SHEET METAL|INSTRUMENT|TCHNO|TECHNOLOGIST)\b", t):
        return SUPPORT, H
    return SUPPORT, M


HEALTH_SECONDARIES = [
    ("Physician / Advanced Practice Provider (staff)", r"PHYSCN|PHYSICIAN|NURSE PRACT|MIDWIFE|ANESTHETIST|DENTIST|OPTOMETRIST|PSYCHIATR"),
    ("Nursing", r"NURSE|NURSING|\bLVN\b|VOC NURSE"),
    ("Behavioral Health & Counseling", r"PSYCHOLOG|BEH HEALTH|COUNSELING|CNSLNG|SOCIAL WORK|CHAPLAIN|ART THERAP|MUSIC THERAP|MARRIAGE"),
    ("Clinical Research Staff", r"CLIN(ICAL)? RSCH|CLIN TRIAL|CLINICAL RESEARCH|CLIN RSCH"),
    ("Health IT & Informatics", r"CLIN APPLICATIONS|CLIN INFORMATICS|HEALTH INFO|CODER|CLIN ENGR|CLIN SYS"),
    ("Health Administration, Billing & Records", r"AMBUL CARE ADMSTN|REVENUE CYCLE|QLTY IMPV|BILL|COLL|RCDS|RECORDS|ABSTRACTOR|"
                                                 r"ADMITTING|ACCESS|HEALTH PLAN|HEALTH PROFNS|UTILIZATION|CREDENTIAL|MED OFC|"
                                                 r"MED STAFF|HEALTH SYS|CASE MGR|MANAGER, CASE|PAT NAVGTR|PAT COMM|PRACTICE CRD|"
                                                 r"MED CTR (MGR|ADMIN)"),
    ("Allied Health & Clinical Technical", r"PHARMAC|RADLG|RADIOLOG|RAD THER|MRI|CT TCHNO|NUC MED|ULTRASOUND|SONOGR|HISTO|CYTO|"
                                           r"PATHOLOG|PHLEBOT|STERILE|SURGICAL|SURGERY|ANESTHESIA|RESP THER|RESPIRATORY|"
                                           r"OCCUPATIONAL THER|PHYS THER|PHYSICAL THER|SPEECH|AUDIOLOG|DIETIT|DIETARY|NUTRITION|"
                                           r"LAB SCI|LAB TCHN|LAB, |PERFUSION|EEG|EKG|ELECTROCARDIO|ECHO|CARDIO|DIALYSIS|"
                                           r"EMERGENCY TRAUMA|TELEMETRY|ENDOSCOPY|TRANSPLANT|GENETIC|CHILD LIFE|ACUPUNCT|ORTHO|"
                                           r"PROSTHET|RECREATION THER|THERAPIST|BIOMED|HEALTH EDUCATOR|HEALTH TCHN|MED AST|"
                                           r"MED ASST|ASSISTANT, MEDICAL|INTERPRETER|CLIN LAB|CLIN SPEC|TECHNOLOGIST|TCHNO|"
                                           r"TCHN|TECHNICIAN|DENTAL|OPTOM|ANATOMICAL|MORGUE|AUTOPSY|EMBALM|SCIENTIST|OPTICIAN|"
                                           r"DOSIMETR|MENTAL HEALTH"),
    ("Health Administration, Billing & Records", r"\b(MGR|MANAGER|SUPV|ANL|ANALYST|SPEC|PROFL|CRD|COORD|OFCR|OFFICER|ADM|"
                                                 r"ADMINISTRATOR|EXEC|DIR|DIRECTOR|SUPT)\b"),
]
COMPILED_HEALTH = [(name, re.compile(rx)) for name, rx in HEALTH_SECONDARIES]


def health_secondary(t):
    for name, rx in COMPILED_HEALTH:
        if rx.search(t):
            return name
    return "Hospital Support & Patient Services"


def chief_secondary(t):
    return staff_family(t)


def classify_rules(title):
    """Apply the explicit rules. Returns dict or None."""
    t = norm(title)
    for rid, rx, cat, sec, conf, note in COMPILED_RULES:
        if rx.search(t):
            if sec is None:
                sec = health_secondary(t) if cat == MED else staff_family(t)
            return dict(category=cat, secondary_category=sec, rule_id=rid, confidence=conf, notes=note)
    return None


def classify_staff(title):
    t = norm(title)
    cat, conf = staff_primary(t)
    fam = staff_family(t)
    rid = {MGMT: "M-ROLE", PROF: "P-ROLE", SUPPORT: "O-ROLE"}[cat]
    if cat == PROF and ROLE_SUPV.search(t) and not ROLE_PROF.search(t):
        rid = "P-SUPV"
    if fam == "General / Unclassified staff":
        conf = L
    return dict(category=cat, secondary_category=fam, rule_id=rid, confidence=conf, notes="")


def classify(title, cto_lookup, allow_staff=True):
    """Full classification for one title (without inheritance)."""
    t = norm(title)
    res = classify_rules(t)
    if res:
        return res
    ref = cto_lookup.get(t)
    if ref and ref["cto"] in CTO_MAP and ref["cto"] not in CTO_NO_CLASSIFY:
        group, cat, sec = CTO_MAP[ref["cto"]]
        return dict(category=cat, secondary_category=sec, rule_id=f"CTO-{ref['cto']}", confidence=H,
                    notes=f"UCOP CTO {ref['cto']} {ref['cto_name']}")
    if ref and ref["cto"] in CTO_NO_CLASSIFY:
        group, cat, sec = CTO_MAP[ref["cto"]]
        return dict(category=cat, secondary_category=sec, rule_id=f"CTO-{ref['cto']}", confidence=M,
                    notes=f"UCOP CTO {ref['cto']} {ref['cto_name']} (grab-bag CTO)")
    if allow_staff:
        return classify_staff(t)
    return None


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------
def load_inventory(conn):
    conn.execute("CREATE INDEX IF NOT EXISTS idx_salaries_title ON salaries(title)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_salaries_year ON salaries(year)")
    rows = conn.execute("""
        SELECT title, COUNT(*), MIN(year), MAX(year),
               SUM(year = 2025), SUM(year = 2020), SUM(year = 2010),
               ROUND(AVG(gross_pay)), ROUND(AVG(CASE WHEN year = 2025 THEN gross_pay END))
        FROM salaries GROUP BY title""").fetchall()
    inv = {}
    for title, n, fy, ly, n25, n20, n10, avg_all, avg25 in rows:
        inv[title] = dict(title=title, n_all=n, first_year=fy, last_year=ly, n_2025=n25, n_2020=n20,
                          n_2010=n10, avg_gross_all=avg_all, avg_gross_2025=avg25)
    return inv


def load_cto_lookup(path=T.UCOP_ACADEMIC_CSV):
    lookup = {}
    if not os.path.exists(path):
        return lookup
    for r in T.read_csv(path):
        lookup.setdefault(norm(r["title"]), r)
    return lookup


def people_by_year(conn, year):
    """{(first, last, location): title} for one year, dropping ambiguous names."""
    d, dup = {}, set()
    for f, l, loc, t in conn.execute(
            "SELECT first_name, last_name, location, title FROM salaries "
            "WHERE year = ? AND first_name != '' AND last_name != ''", (year,)):
        k = (f, l, loc)
        if k in d:
            dup.add(k)
        else:
            d[k] = t
    for k in dup:
        d.pop(k, None)
    return d


def compute_transitions(conn):
    """Count title changes of people linked across consecutive years."""
    years = [y for (y,) in conn.execute("SELECT DISTINCT year FROM salaries ORDER BY year")]
    trans = collections.Counter()
    prev = people_by_year(conn, years[0])
    for y in years[1:]:
        cur = people_by_year(conn, y)
        for k, t in cur.items():
            pt = prev.get(k)
            if pt is not None and pt != t:
                trans[(y, pt, t)] += 1
        prev = cur
    return trans


def build_crosswalk(trans, inv):
    max_year = max(m["last_year"] for m in inv.values())
    pair = collections.Counter()
    out_total = collections.Counter()
    years = collections.defaultdict(set)
    for (y, a, b), n in trans.items():
        pair[(a, b)] += n
        out_total[a] += n
        years[(a, b)].add(y)
    rows = []
    for (a, b), n in pair.items():
        if n < 3:
            continue
        share = n / out_total[a]
        fa, fb = inv.get(a, {}), inv.get(b, {})
        a_last, b_first = fa.get("last_year"), fb.get("first_year")
        ys = sorted(years[(a, b)])
        fam_a, lvl_a = family_level(a)
        fam_b, lvl_b = family_level(b)
        same_family = fam_a is not None and fam_a == fam_b
        retired = a_last is not None and a_last < max_year and a_last + 1 in ys
        if base_title(a) == base_title(b):
            relation, conf = "variant", H
        elif retired and share >= 0.5:
            relation, conf = "rename", H
        elif retired and share >= 0.25:
            relation, conf = "rename", M
        elif same_family and lvl_b is not None and lvl_a is not None and lvl_b > lvl_a:
            relation, conf = "promotion", (H if share >= 0.2 else M)
        elif same_family and lvl_b == lvl_a:
            relation, conf = "lateral", M
        elif share >= 0.1 and n >= 5:
            relation, conf = "other", L
        else:
            continue
        rows.append(dict(from_title=a, to_title=b, relation=relation, confidence=conf, n_moves=n,
                         share_of_moves=round(share, 3), years="|".join(map(str, ys)),
                         from_last_year=a_last, to_first_year=b_first, from_family=fam_a, from_level=lvl_a,
                         to_family=fam_b, to_level=lvl_b, source="claude"))
    rows.sort(key=lambda r: (-r["n_moves"], r["from_title"], r["to_title"]))
    return rows


def successor_map(crosswalk):
    """from_title -> best rename successor (highest share)."""
    best = {}
    for r in crosswalk:
        if r["relation"] == "rename":
            cur = best.get(r["from_title"])
            if cur is None or r["share_of_moves"] > cur["share_of_moves"]:
                best[r["from_title"]] = r
    return {k: v["to_title"] for k, v in best.items()}


def next_title_map(crosswalk):
    """from_title -> most common promotion target one level up."""
    best = {}
    for r in crosswalk:
        if r["relation"] == "promotion" and r["to_level"] == (r["from_level"] or 0) + 1:
            cur = best.get(r["from_title"])
            if cur is None or r["n_moves"] > cur["n_moves"]:
                best[r["from_title"]] = r
    return {k: v["to_title"] for k, v in best.items()}


def is_health_flag(row):
    t = norm(row["title"])
    if row["category"] == MED:
        return "Y"
    if row["secondary_category"] == "Health System Executive":
        return "Y"
    if re.search(r"HCOMP|MEDCOMP|^HS |MED CTR|SOM$|CLIN", t):
        return "Y"
    return "N"


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=T.DATABASE)
    ap.add_argument("--out", default=T.TITLE_CATEGORIES_CSV)
    ap.add_argument("--crosswalk-out", default=T.TITLE_CROSSWALK_CSV)
    ap.add_argument("--no-crosswalk", action="store_true", help="reuse the existing crosswalk CSV")
    ap.add_argument("--report", action="store_true", help="print totals by category when done")
    args = ap.parse_args()

    conn = T.connect(args.db)
    print("Loading title inventory...")
    inv = load_inventory(conn)
    print(f"  {len(inv)} distinct titles")
    cto_lookup = load_cto_lookup()
    print(f"  {len(cto_lookup)} UCOP academic reference titles")

    if args.no_crosswalk and os.path.exists(args.crosswalk_out):
        crosswalk = T.read_csv(args.crosswalk_out)
        for r in crosswalk:
            for c in ("n_moves", "from_last_year", "to_first_year", "from_level", "to_level"):
                r[c] = int(r[c]) if r.get(c) not in ("", None) else None
            r["share_of_moves"] = float(r["share_of_moves"])
        print(f"  reusing {len(crosswalk)} crosswalk rows from {args.crosswalk_out}")
    else:
        print("Linking people across years to derive title transitions...")
        trans = compute_transitions(conn)
        crosswalk = build_crosswalk(trans, inv)
        T.write_csv(args.crosswalk_out, crosswalk, T.CROSSWALK_COLUMNS)
        kinds = collections.Counter(r["relation"] for r in crosswalk)
        print(f"  wrote {len(crosswalk)} rows to {args.crosswalk_out}: {dict(kinds)}")
    successors = successor_map(crosswalk)
    nexts = next_title_map(crosswalk)

    # Existing human work to carry over.
    existing = {}
    if os.path.exists(args.out):
        for r in T.read_csv(args.out):
            existing[r["title"]] = r
        print(f"  carrying over original_category / review_notes / manual rows from {args.out}")

    print("Classifying...")
    results = {}
    for title, meta in inv.items():
        res = classify(title, cto_lookup)
        results[title] = res

    # Inheritance for weak results: successor (rename) or base title.
    weak_rules = {"M-ROLE", "P-ROLE", "O-ROLE", "P-SUPV"}
    strong = lambda r: r and r["rule_id"] not in weak_rules and r["confidence"] in (H, M)
    for title, res in list(results.items()):
        if strong(res):
            continue
        # 1) the title this one was renamed to
        succ = successors.get(title)
        hops = 0
        while succ and not strong(results.get(succ)) and hops < 3:
            succ = successors.get(succ)
            hops += 1
        if succ and strong(results.get(succ)):
            s = results[succ]
            results[title] = dict(category=s["category"], secondary_category=s["secondary_category"],
                                  rule_id=f"X-SUCCESSOR", confidence=M,
                                  notes=f"inherited from successor title '{succ}' ({s['rule_id']})")
            continue
        # 2) the suffix-stripped base title
        bt = base_title(title)
        if bt != norm(title):
            b = results.get(bt) or classify(bt, cto_lookup, allow_staff=False)
            if strong(b):
                results[title] = dict(category=b["category"], secondary_category=b["secondary_category"],
                                      rule_id="X-VARIANT", confidence=b["confidence"],
                                      notes=f"variant of '{bt}' ({b['rule_id']})")

    rows = []
    for title, meta in inv.items():
        res = results[title]
        old = existing.get(title, {})
        row = dict(meta)
        row.update(res)
        row["source"] = "claude"
        if old.get("source") == "manual":
            row["category"] = old.get("category", row["category"])
            row["secondary_category"] = old.get("secondary_category", row["secondary_category"])
            row["rule_id"] = "MANUAL"
            row["confidence"] = H
            row["source"] = "manual"
        elif old.get("source") == "original" and old.get("category"):
            row["category"] = old["category"]
            row["secondary_category"] = old.get("secondary_category") or row["secondary_category"]
            row["rule_id"] = "ORIGINAL"
            row["source"] = "original"
        row["original_category"] = old.get("original_category", "")
        row["review_notes"] = old.get("review_notes", "")
        allowed = T.ORIGINAL_TO_NEW.get(row["original_category"])
        row["needs_review"] = "Y" if (row["original_category"] and allowed is not None
                                      and row["category"] not in allowed) else ""
        ref = cto_lookup.get(norm(title))
        row["uc_cto"] = ref["cto"] if ref else ""
        row["uc_cto_name"] = ref["cto_name"] if ref else ""
        row["uc_academic_group"] = CTO_MAP[ref["cto"]][0] if ref and ref["cto"] in CTO_MAP else ""
        fam, lvl = family_level(title)
        succ = successors.get(title)
        if succ and succ in inv:
            fam, lvl = family_level(succ)
        row["family"], row["level"] = fam, lvl
        row["successor_title"] = succ or ""
        row["next_title"] = nexts.get(title, "")
        row["is_health"] = is_health_flag(row)
        rows.append(row)

    rows.sort(key=lambda r: (-r["n_all"], r["title"]))
    T.write_csv(args.out, rows, T.CATEGORY_COLUMNS)
    print(f"  wrote {len(rows)} rows to {args.out}")

    if args.report:
        report(rows)
    return 0


def report(rows):
    by_cat = collections.defaultdict(lambda: [0, 0, 0])
    for r in rows:
        b = by_cat[r["category"]]
        b[0] += 1
        b[1] += r["n_all"]
        b[2] += r["n_2025"] or 0
    print(f"\n{'category':<40}{'titles':>8}{'rows(all yrs)':>15}{'rows 2025':>11}")
    for cat, (nt, na, n25) in sorted(by_cat.items(), key=lambda x: -x[1][1]):
        print(f"{cat:<40}{nt:>8}{na:>15,}{n25:>11,}")
    conf = collections.Counter()
    for r in rows:
        conf[r["confidence"]] += r["n_all"]
    print("\nperson-years by confidence:", dict(conf))
    weak = [r for r in rows if r["confidence"] == L or r["category"] == OTHER]
    weak.sort(key=lambda r: -r["n_all"])
    print(f"\n{len(weak)} low-confidence / uncategorized titles; top 40 by count:")
    for r in weak[:40]:
        print(f"  {r['n_all']:>7}  {r['category']:<36} {r['rule_id']:<8} {r['title']}")


if __name__ == "__main__":
    sys.exit(main())
