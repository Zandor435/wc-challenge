"""Parse the real `schedule.txt` into a normalized match list + validation.

The schedule is human-written prose, so the parser tolerates the real-world
messiness flagged up front:
  - separators: 'vs.', 'vs', 'v.', 'versus'
  - trailing '(venue), time' text on every line
  - name variants handled via team_aliases.csv (Korea Republic -> South Korea,
    Curaçao -> Curacao)
  - knockout feeder text ('Group A/B/C/D/F third place', 'Group E winners')
  - generic later-round lines ('Round of 16 match 1') that carry NO feeders

It does NOT silently guess. Anything it cannot parse is recorded as a failed
line and surfaced in the validation report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import data_io

GROUP_LETTERS = list("ABCDEFGHIJKL")

# Section headers in the file, mapped to canonical stage names.
SECTION_HEADERS = {
    "group stage": "group",
    "round of 32": "R32",
    "round of 16": "R16",
    "quarterfinals": "QF",
    "semifinals": "SF",
}

# Matchup separators, longest first so 'vs.' is tried before 'vs'.
_SEP_RE = re.compile(r"\s+(?:versus|vs\.|vs|v\.)\s+", re.IGNORECASE)
# A date header like "Thursday, June 11:" or "Wednesday, July 1:".
_DATE_RE = re.compile(r"^[A-Z][a-z]+,\s+([A-Z][a-z]+)\s+(\d{1,2}):")
_GROUP_LINE_RE = re.compile(r"^Group\s+([A-L]):\s*(.+)$")


@dataclass
class Match:
    stage: str
    group: str = ""
    date: str = ""
    team_1: str = ""
    team_2: str = ""
    knockout_match_id: str = ""
    feeder_1: str = ""
    feeder_2: str = ""
    raw_line: str = ""


@dataclass
class ParseResult:
    matches: list[Match] = field(default_factory=list)
    failed_lines: list[tuple[int, str]] = field(default_factory=list)
    aliases_applied: list[tuple[int, str, str]] = field(default_factory=list)
    preamble_lines: list[tuple[int, str]] = field(default_factory=list)


def _strip_venue(text: str) -> str:
    """Drop everything from the first parenthesis (venue) onward."""
    idx = text.find("(")
    return text[:idx].strip() if idx != -1 else text.strip()


def _split_matchup(text: str) -> tuple[str, str] | None:
    parts = _SEP_RE.split(text)
    if len(parts) != 2:
        return None
    return parts[0].strip(), parts[1].strip()


def parse_schedule(path=None) -> tuple[ParseResult, dict[str, str]]:
    path = Path(path) if path else data_io.PROJECT_ROOT / "schedule.txt"
    aliases = data_io.load_aliases()
    result = ParseResult()

    stage = None
    current_date = ""
    ko_counters = {"R32": 0, "R16": 0, "QF": 0, "SF": 0}

    with open(path, encoding="utf-8-sig") as f:
        lines = f.readlines()

    for lineno, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line:
            continue

        low = line.lower()

        # Section header?
        if low in SECTION_HEADERS:
            stage = SECTION_HEADERS[low]
            continue

        # Special standalone lines (third place / final) that double as headers.
        if low.startswith("third-place") or low.startswith("third place"):
            result.matches.append(Match(stage="3rd_place", date=_extract_inline_date(line),
                                        knockout_match_id="3P", raw_line=line))
            continue
        if low.startswith("final"):
            result.matches.append(Match(stage="Final", date=_extract_inline_date(line),
                                        knockout_match_id="FINAL", raw_line=line))
            continue

        # Date header?
        m = _DATE_RE.match(line)
        if m:
            current_date = f"{m.group(1)} {int(m.group(2))}"
            continue

        if stage == "group":
            _parse_group_line(line, lineno, current_date, aliases, result)
        elif stage in ("R32",):
            _parse_r32_line(line, lineno, current_date, ko_counters, result)
        elif stage in ("R16", "QF", "SF"):
            _parse_generic_ko_line(line, lineno, current_date, stage, ko_counters, result)
        elif stage is None:
            # Preamble before the first section header (e.g. the title line).
            result.preamble_lines.append((lineno, line))
        else:
            # Inside a known section but unrecognized -> flag it.
            result.failed_lines.append((lineno, line))

    return result, aliases


def _extract_inline_date(line: str) -> str:
    m = re.search(r"([A-Z][a-z]+)\s+(\d{1,2})", line)
    return f"{m.group(1)} {int(m.group(2))}" if m else ""


def _norm(name: str, aliases, lineno: int, result: ParseResult) -> str:
    canon = data_io.normalize_team(name, aliases)
    if canon != " ".join(name.split()).strip().strip(",."):
        result.aliases_applied.append((lineno, name.strip(), canon))
    return canon


def _parse_group_line(line, lineno, date, aliases, result: ParseResult):
    m = _GROUP_LINE_RE.match(line)
    if not m:
        result.failed_lines.append((lineno, line))
        return
    group, rest = m.group(1), m.group(2)
    matchup = _strip_venue(rest)
    pair = _split_matchup(matchup)
    if not pair:
        result.failed_lines.append((lineno, line))
        return
    t1 = _norm(pair[0], aliases, lineno, result)
    t2 = _norm(pair[1], aliases, lineno, result)
    result.matches.append(Match(stage="group", group=group, date=date,
                                team_1=t1, team_2=t2, raw_line=line))


def _parse_r32_line(line, lineno, date, ko_counters, result: ParseResult):
    matchup = _strip_venue(line)
    pair = _split_matchup(matchup)
    if not pair:
        result.failed_lines.append((lineno, line))
        return
    ko_counters["R32"] += 1
    mid = f"R32-{ko_counters['R32']:02d}"
    result.matches.append(Match(stage="R32", date=date, knockout_match_id=mid,
                                feeder_1=pair[0].strip(), feeder_2=pair[1].strip(),
                                raw_line=line))


def _parse_generic_ko_line(line, lineno, date, stage, ko_counters, result: ParseResult):
    # e.g. "Round of 16 match 1", "Quarterfinal 2:", "Semifinal 1"
    m = re.match(r"^(Round of 16 match|Quarterfinal|Semifinal)\s+(\d+)", line, re.IGNORECASE)
    if not m:
        result.failed_lines.append((lineno, line))
        return
    ko_counters[stage] += 1
    mid = f"{stage}-{int(m.group(2)):02d}"
    result.matches.append(Match(stage=stage, date=date, knockout_match_id=mid,
                                feeder_1="", feeder_2="", raw_line=line))


# ----------------------------- feeder parsing ----------------------------- #

def parse_feeder(text: str):
    """Return a structured feeder descriptor.

    ('winner', 'A') | ('runner', 'A') | ('third', ['A','B','C','D','F'])
    Returns None if it does not match a known feeder pattern.
    """
    t = text.strip()
    m = re.match(r"^Group\s+([A-L])\s+winners?$", t, re.IGNORECASE)
    if m:
        return ("winner", m.group(1).upper())
    m = re.match(r"^Group\s+([A-L])\s+runners?-?up$", t, re.IGNORECASE)
    if m:
        return ("runner", m.group(1).upper())
    m = re.match(r"^Group\s+([A-L/]+)\s+third\s+place$", t, re.IGNORECASE)
    if m:
        groups = [g for g in m.group(1).split("/") if g]
        return ("third", [g.upper() for g in groups])
    return None
