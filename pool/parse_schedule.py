"""Phase 1 runner: parse schedule.txt -> matches.csv + schedule_validation.txt.

Run:  .venv\\Scripts\\python.exe parse_schedule.py
This performs NO simulation. It only parses, normalizes, and validates, then
prints a report and writes it to outputs/schedule_validation.txt.
"""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

from wc_pool import data_io
from wc_pool.schedule_parser import GROUP_LETTERS, parse_schedule, parse_feeder

OUT = data_io.PROJECT_ROOT / "outputs"
OUT.mkdir(exist_ok=True)

EXPECTED = {"group": 72, "R32": 16, "R16": 8, "QF": 4, "SF": 2, "3rd_place": 1, "Final": 1}


def write_matches_csv(matches, path):
    cols = ["stage", "group", "date", "team_1", "team_2",
            "knockout_match_id", "feeder_1", "feeder_2"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for m in matches:
            w.writerow({c: getattr(m, c) for c in cols})


def main():
    result, aliases = parse_schedule()
    matches = result.matches

    aliases_tbl = data_io.load_aliases()
    drafted = data_io.load_drafted_teams(aliases_tbl)
    tiers = data_io.load_team_tiers(aliases_tbl)

    write_matches_csv(matches, data_io.PROJECT_ROOT / "matches.csv")

    lines: list[str] = []
    problems: list[str] = []

    def out(s=""):
        lines.append(s)

    out("=" * 70)
    out("SCHEDULE VALIDATION REPORT  (Phase 1 - parser only, no simulation)")
    out("=" * 70)

    # --- stage counts ---
    stage_counts = Counter(m.stage for m in matches)
    out("\n[1] Stage / match counts")
    for stage, exp in EXPECTED.items():
        got = stage_counts.get(stage, 0)
        flag = "OK" if got == exp else "*** MISMATCH ***"
        if got != exp:
            problems.append(f"Stage {stage}: expected {exp}, got {got}")
        out(f"    {stage:10s} expected {exp:3d}   got {got:3d}   {flag}")

    # --- group structure ---
    group_matches = [m for m in matches if m.stage == "group"]
    group_teams = defaultdict(list)
    for m in group_matches:
        group_teams[m.group].append(m.team_1)
        group_teams[m.group].append(m.team_2)

    out("\n[2] Groups and membership")
    out(f"    Groups found: {sorted(group_teams)}  (expected {GROUP_LETTERS})")
    if sorted(group_teams) != GROUP_LETTERS:
        problems.append(f"Group set mismatch: {sorted(group_teams)}")
    for g in sorted(group_teams):
        teams = sorted(set(group_teams[g]))
        per_team = Counter(group_teams[g])
        n_matches = sum(1 for m in group_matches if m.group == g)
        ok = (len(teams) == 4 and n_matches == 6 and all(v == 3 for v in per_team.values()))
        if not ok:
            problems.append(f"Group {g}: {len(teams)} teams, {n_matches} matches")
        out(f"    Group {g}: {n_matches} matches, {len(teams)} teams -> {teams}")

    # --- each team appears exactly 3 times in group stage ---
    appearances = Counter()
    for m in group_matches:
        appearances[m.team_1] += 1
        appearances[m.team_2] += 1
    out("\n[3] Team appearance counts (group stage)")
    out(f"    Distinct teams in group stage: {len(appearances)} (expected 48)")
    if len(appearances) != 48:
        problems.append(f"Distinct group-stage teams = {len(appearances)}, expected 48")
    bad = {t: c for t, c in appearances.items() if c != 3}
    if bad:
        problems.append(f"Teams NOT appearing exactly 3x: {bad}")
        out(f"    *** Teams not appearing exactly 3 times: {bad}")
    else:
        out("    All teams appear exactly 3 times: OK")

    # --- duplicate group games ---
    pair_counter = Counter()
    for m in group_matches:
        pair_counter[frozenset((m.team_1, m.team_2))] += 1
    dups = {tuple(sorted(p)): c for p, c in pair_counter.items() if c > 1}
    out("\n[4] Duplicate group games")
    if dups:
        problems.append(f"Duplicate group games: {dups}")
        out(f"    *** Duplicates: {dups}")
    else:
        out("    None: OK")

    # --- name reconciliation vs tier table ---
    schedule_teams = set(appearances)
    tier_teams = set(tiers)
    out("\n[5] Name reconciliation (schedule vs team_tiers.csv)")
    missing_from_tiers = sorted(schedule_teams - tier_teams)
    extra_in_tiers = sorted(tier_teams - schedule_teams)
    if missing_from_tiers:
        problems.append(f"Schedule teams missing from team_tiers.csv: {missing_from_tiers}")
        out(f"    *** In schedule but missing from team_tiers.csv: {missing_from_tiers}")
    if extra_in_tiers:
        problems.append(f"team_tiers.csv teams not in schedule: {extra_in_tiers}")
        out(f"    *** In team_tiers.csv but not in schedule: {extra_in_tiers}")
    if not missing_from_tiers and not extra_in_tiers:
        out("    team_tiers.csv exactly matches the 48 schedule teams: OK")

    # --- drafted teams present ---
    out("\n[6] Drafted teams present in schedule")
    drafted_names = {d["team"] for d in drafted}
    missing_drafted = sorted(drafted_names - schedule_teams)
    if missing_drafted:
        problems.append(f"Drafted teams NOT in schedule: {missing_drafted}")
        out(f"    *** Drafted teams not found in schedule: {missing_drafted}")
    else:
        out(f"    All {len(drafted_names)} drafted teams found in schedule: OK")

    # --- aliases applied ---
    out("\n[7] Aliases applied during parsing")
    if result.aliases_applied:
        seen = set()
        for lineno, raw, canon in result.aliases_applied:
            key = (raw, canon)
            if key not in seen:
                seen.add(key)
                out(f"    line {lineno:>3}: '{raw}' -> '{canon}'")
    else:
        out("    (none)")

    # --- failed / ambiguous lines ---
    out("\n[8] Lines that failed to parse")
    if result.failed_lines:
        problems.append(f"{len(result.failed_lines)} unparsed lines")
        for lineno, txt in result.failed_lines:
            out(f"    line {lineno:>3}: {txt}")
    else:
        out("    (none) OK")
    if result.preamble_lines:
        out("    Ignored preamble (before first section, not an error):")
        for lineno, txt in result.preamble_lines:
            out(f"        line {lineno:>3}: {txt}")

    # --- R32 feeder coverage ---
    out("\n[9] Round-of-32 feeder coverage")
    winners, runners, third_slots, bad_feeders = [], [], [], []
    for m in matches:
        if m.stage != "R32":
            continue
        for feeder in (m.feeder_1, m.feeder_2):
            parsed = parse_feeder(feeder)
            if parsed is None:
                bad_feeders.append((m.knockout_match_id, feeder))
            elif parsed[0] == "winner":
                winners.append(parsed[1])
            elif parsed[0] == "runner":
                runners.append(parsed[1])
            elif parsed[0] == "third":
                third_slots.append(parsed[1])
    out(f"    Group winners referenced: {sorted(winners)}")
    out(f"    Group runners-up referenced: {sorted(runners)}")
    out(f"    Third-place slots: {len(third_slots)} (expected 8)")
    for slot in third_slots:
        out(f"        eligible groups: {'/'.join(slot)}")
    if sorted(winners) != GROUP_LETTERS:
        problems.append(f"R32 winners coverage wrong: {sorted(winners)}")
    if sorted(runners) != GROUP_LETTERS:
        problems.append(f"R32 runners-up coverage wrong: {sorted(runners)}")
    if len(third_slots) != 8:
        problems.append(f"Third-place slots = {len(third_slots)}, expected 8")
    if bad_feeders:
        problems.append(f"Unparsed R32 feeders: {bad_feeders}")
        out(f"    *** Unparsed feeders: {bad_feeders}")
    if (sorted(winners) == GROUP_LETTERS and sorted(runners) == GROUP_LETTERS
            and len(third_slots) == 8 and not bad_feeders):
        out("    32 R32 feeder slots = 12 winners + 12 runners-up + 8 thirds: OK")

    # --- blank tiers ---
    out("\n[10] Tier completeness (blank tiers must be filled before scoring)")
    blanks = sorted(t for t, info in tiers.items() if info["tier"] is None)
    out(f"    Teams needing a tier ({len(blanks)}): {blanks}")
    if blanks:
        out("    NOTE: allowed during parser testing; must be filled (provisional_tiers.csv)")
        out("          before the 5,000-sim scoring run.")

    # --- summary ---
    out("\n" + "=" * 70)
    if problems:
        out(f"RESULT: {len(problems)} PROBLEM(S) FOUND")
        for p in problems:
            out(f"  - {p}")
    else:
        out("RESULT: ALL STRUCTURAL CHECKS PASSED")
    out("=" * 70)

    report = "\n".join(lines)
    print(report)
    (OUT / "schedule_validation.txt").write_text(report, encoding="utf-8")
    print(f"\nWrote {OUT / 'schedule_validation.txt'}")
    print(f"Wrote {data_io.PROJECT_ROOT / 'matches.csv'}")


if __name__ == "__main__":
    main()
