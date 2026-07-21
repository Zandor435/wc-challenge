"""Extract the OFFICIAL Annex C third-place allocation table from the FIFA
World Cup 2026 Regulations PDF and write it to outputs/third_place_mapping.csv.

Source: reference/FWC2026_regulations_EN.pdf, "ANNEXE C - COMBINATIONS FOR
EIGHT BEST THIRD-PLACED TEAMS" (pages 79-96 in pdfplumber's 0-based index).

The annex header reads:   Option  1A 1B 1D 1E 1G 1I 1K 1L
i.e. the 8 columns are the group WINNERS that play a third-placed team, and
each row's "3X" entries name the third-placed group assigned to that winner.
We map each winner to its official Round-of-32 match number (from the published
bracket), so downstream code keys off match numbers, not column position.

This module does NOT invent anything: every row is read verbatim from the PDF
and then cross-checked against the slot-eligibility constraints derived from the
schedule. If the parse is incomplete or inconsistent, it raises loudly.
"""

from __future__ import annotations

import csv
import re
from itertools import combinations
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parent
PDF = ROOT / "reference" / "FWC2026_regulations_EN.pdf"
OUT = ROOT / "outputs" / "third_place_mapping.csv"

# Column order in Annex C header -> winner group -> official R32 match number.
COL_WINNERS = ["A", "B", "D", "E", "G", "I", "K", "L"]
WINNER_TO_MATCH = {
    "A": "M79", "B": "M85", "D": "M81", "E": "M74",
    "G": "M82", "I": "M77", "K": "M87", "L": "M80",
}
# Eligibility (which third-place groups can occupy each winner's slot), from the
# schedule feeder text. Used only as a cross-check on the official table.
ELIGIBILITY = {
    "E": set("ABCDF"),  # M74 (1E)
    "I": set("CDFGH"),  # M77 (1I)
    "A": set("CEFHI"),  # M79 (1A)
    "L": set("EHIJK"),  # M80 (1L)
    "D": set("BEFIJ"),  # M81 (1D)
    "G": set("AEHIJ"),  # M82 (1G)
    "B": set("EFGIJ"),  # M85 (1B)
    "K": set("DEIJL"),  # M87 (1K)
}

ROW_RE = re.compile(r"^\s*(\d{1,3})\s+((?:3[A-L]\s*){8})\s*$")


def parse_pdf() -> dict[int, list[str]]:
    """option number -> [third group for col 1A, 1B, 1D, 1E, 1G, 1I, 1K, 1L]."""
    rows: dict[int, list[str]] = {}
    with pdfplumber.open(PDF) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            for line in text.splitlines():
                m = ROW_RE.match(line)
                if not m:
                    continue
                option = int(m.group(1))
                thirds = re.findall(r"3([A-L])", m.group(2))
                if len(thirds) != 8:
                    raise ValueError(f"Row {option} has {len(thirds)} entries: {line!r}")
                if option in rows and rows[option] != thirds:
                    raise ValueError(f"Conflicting duplicate for option {option}")
                rows[option] = thirds
    return rows


def validate(rows: dict[int, list[str]]) -> list[str]:
    problems: list[str] = []
    # 1) exactly 495 options, contiguous 1..495
    if sorted(rows) != list(range(1, 496)):
        problems.append(f"Option numbers not 1..495 (got {len(rows)} rows; "
                        f"min={min(rows)}, max={max(rows)})")
    # 2) each row: 8 distinct groups, none equal to its winner, eligibility holds
    seen_sets = {}
    for opt, thirds in rows.items():
        if len(set(thirds)) != 8:
            problems.append(f"Option {opt}: thirds not distinct: {thirds}")
            continue
        for winner, third in zip(COL_WINNERS, thirds):
            if third == winner:
                problems.append(f"Option {opt}: winner {winner} faces own group third")
            if third not in ELIGIBILITY[winner]:
                problems.append(f"Option {opt}: 3{third} not eligible for 1{winner} "
                               f"(allowed {sorted(ELIGIBILITY[winner])})")
        key = frozenset(thirds)
        if key in seen_sets:
            problems.append(f"Duplicate qualifying-set for options {seen_sets[key]} & {opt}")
        else:
            seen_sets[key] = opt
    # 3) the 495 qualifying sets must equal ALL C(12,8) combinations
    all_combos = {frozenset(c) for c in combinations("ABCDEFGHIJKL", 8)}
    got = set(seen_sets)
    if got != all_combos:
        missing = all_combos - got
        extra = got - all_combos
        problems.append(f"Qualifying-set coverage off: {len(missing)} missing, {len(extra)} extra")
    return problems


def write_csv(rows: dict[int, list[str]]):
    OUT.parent.mkdir(exist_ok=True)
    match_cols = [f"{WINNER_TO_MATCH[w]}_1{w}" for w in COL_WINNERS]
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["option", "qualifying_groups"] + match_cols)
        for opt in sorted(rows):
            thirds = rows[opt]
            qual = "".join(sorted(thirds))
            w.writerow([opt, qual] + [f"3{t}" for t in thirds])


def main():
    rows = parse_pdf()
    problems = validate(rows)
    print(f"Parsed {len(rows)} option rows from Annex C.")
    print("Column order (winner -> R32 match):")
    for w in COL_WINNERS:
        print(f"    1{w} -> {WINNER_TO_MATCH[w]} (eligible thirds: {''.join(sorted(ELIGIBILITY[w]))})")
    if problems:
        print(f"\n*** {len(problems)} VALIDATION PROBLEM(S):")
        for p in problems[:30]:
            print("   -", p)
        raise SystemExit(1)
    write_csv(rows)
    print(f"\nVALID: 495 unique combinations, all eligibility + coverage checks passed.")
    print(f"Wrote {OUT}")
    # show a couple of example rows
    print("\nExample rows:")
    for opt in (1, 2, 495):
        thirds = rows[opt]
        assign = ", ".join(f"{WINNER_TO_MATCH[w]}(1{w})<-3{t}" for w, t in zip(COL_WINNERS, thirds))
        print(f"  option {opt}: qualifying={''.join(sorted(thirds))} | {assign}")


if __name__ == "__main__":
    main()
