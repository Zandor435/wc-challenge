"""
Build matches.csv from schedule.txt using the canonical drafted_teams.csv.

Output columns:
  date,phase,group,team1,team2,team1_owner,team2_owner,team1_tier,team2_tier,venue,time_et

Rules:
- Group-stage team names are normalized to the EXACT draft names used in
  drafted_teams.csv (e.g. Turkiye->Turkey, South Korea->Korea Republic,
  Bosnia and Herzegovina->Bosnia, United States->USA, Cote d'Ivoire->Ivory Coast).
- Drafted teams get owner + tier filled in; non-drafted teams leave them blank.
- All knockout matches have undetermined participants -> "TBD" in every
  team/owner/tier field. Venue + ET time are still recorded.
"""
import csv
import re
import unicodedata

YEAR = 2026
MONTHS = {"January":1,"February":2,"March":3,"April":4,"May":5,"June":6,
          "July":7,"August":8,"September":9,"October":10,"November":11,"December":12}

# schedule spelling -> canonical draft spelling
SCHEDULE_TO_DRAFT = {
    "Turkiye": "Turkey",
    "Turkiye ": "Turkey",
    "Turkiye)": "Turkey",
    "South Korea": "Korea Republic",
    "Bosnia and Herzegovina": "Bosnia",
    "United States": "USA",
    "Cote d'Ivoire": "Ivory Coast",
}

def strip_diacritics(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))

def norm_team(name: str) -> str:
    name = strip_diacritics(name.strip())
    return SCHEDULE_TO_DRAFT.get(name, name)

# ---- load draft board -------------------------------------------------------
roster = {}  # canonical team name -> (owner, tier)
with open("drafted_teams.csv", newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f):
        roster[row["team"].strip()] = (row["owner"].strip(), row["tier"].strip())

def owner_tier(team: str):
    return roster.get(team, ("", ""))

# ---- time/venue helpers -----------------------------------------------------
def extract_et(time_blob: str) -> str:
    """Pull the ET portion out of a '... local / 3 p.m. ET (June 14)' blob."""
    time_blob = time_blob.strip().rstrip(".")
    # split on slash; keep the segment that mentions ET
    for seg in time_blob.split("/"):
        if "ET" in seg:
            return seg.strip()
    return time_blob.strip()

# parenthetical venue followed by ", <time>"
VENUE_TIME = re.compile(r"\(([^)]*)\),\s*(.+)$")

def parse_venue_time(tail: str):
    m = VENUE_TIME.search(tail)
    if not m:
        return "", ""
    return m.group(1).strip(), extract_et(m.group(2))

# ---- parse schedule ---------------------------------------------------------
DATE_HDR = re.compile(r"^[A-Z][a-z]+,\s+([A-Z][a-z]+)\s+(\d+):")
GROUP_MATCH = re.compile(r"^Group ([A-L]):\s+(.+?)\s+vs\.?\s+(.+?)\s+(\(.+)$")
INLINE_DATE = re.compile(r"--\s+[A-Z][a-z]+,\s+([A-Z][a-z]+)\s+(\d+)\s+(\(.+)$")

rows = []
cur_date = ""
phase = "group"

def isodate(month_name, day):
    return f"{YEAR}-{MONTHS[month_name]:02d}-{int(day):02d}"

with open("schedule.txt", encoding="utf-8") as f:
    lines = [ln.rstrip("\n") for ln in f]

for raw in lines:
    line = raw.strip()
    if not line:
        continue

    # phase section headers
    if line == "Round of 32": phase = "round_of_32"; continue
    if line == "Round of 16": phase = "round_of_16"; continue
    if line == "Quarterfinals": phase = "quarterfinal"; continue
    if line == "Semifinals": phase = "semifinal"; continue
    if line.startswith("Group stage"): phase = "group"; continue

    # day header (sets current date)
    dh = DATE_HDR.match(line)
    if dh:
        cur_date = isodate(dh.group(1), dh.group(2))
        continue

    # group-stage match
    gm = GROUP_MATCH.match(line)
    if gm and phase == "group":
        grp, t1, t2, tail = gm.groups()
        t1, t2 = norm_team(t1), norm_team(t2)
        venue, time_et = parse_venue_time(tail)
        o1, tr1 = owner_tier(t1)
        o2, tr2 = owner_tier(t2)
        rows.append([cur_date, "group", grp, t1, t2, o1, o2, tr1, tr2, venue, time_et])
        continue

    # third-place game and final carry their date inline
    if line.startswith("Third-place game") or line.startswith("Final"):
        ph = "third_place" if line.startswith("Third-place") else "final"
        idm = INLINE_DATE.search(line)
        if idm:
            d = isodate(idm.group(1), idm.group(2))
            venue, time_et = parse_venue_time(idm.group(3))
            rows.append([d, ph, "", "TBD", "TBD", "TBD", "TBD", "TBD", "TBD", venue, time_et])
        continue

    # any other knockout line (R32 placeholder pairing, R16/QF/SF "match N")
    if phase in ("round_of_32", "round_of_16", "quarterfinal", "semifinal") and "(" in line:
        venue, time_et = parse_venue_time(line)
        rows.append([cur_date, phase, "", "TBD", "TBD", "TBD", "TBD", "TBD", "TBD", venue, time_et])
        continue

# ---- write ------------------------------------------------------------------
HEADER = ["date","phase","group","team1","team2","team1_owner","team2_owner",
          "team1_tier","team2_tier","venue","time_et"]
with open("matches.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(HEADER)
    w.writerows(rows)

# ---- validation summary -----------------------------------------------------
from collections import Counter
phase_counts = Counter(r[1] for r in rows)
group_rows = [r for r in rows if r[1] == "group"]
appearances = Counter()
for r in group_rows:
    appearances[r[3]] += 1
    appearances[r[4]] += 1

print("matches.csv written:", len(rows), "matches")
for p in ["group","round_of_32","round_of_16","quarterfinal","semifinal","third_place","final"]:
    print(f"  {p:14s} {phase_counts.get(p,0)}")

drafted_found = sorted(t for t in roster if t in appearances)
drafted_missing = sorted(t for t in roster if t not in appearances)
print(f"drafted teams present in group stage: {len(drafted_found)}/24")
if drafted_missing:
    print("  MISSING:", drafted_missing)
bad = {t:c for t,c in appearances.items() if c != 3}
print("teams NOT playing exactly 3 group games:", bad if bad else "none")
print("distinct teams in group stage:", len(appearances))
