"""Phase 1 gate: prove the knockout bracket is correct before any scoring.

Two layers, per the plan:
  Layer 0 (one-time): the parsed schedule's 16 R32 matchups are consistent with
      the official bracket (R32_DEFS).
  Layer 1 (structural, per sim): 32 unique R32 teams; every round halves cleanly
      (16->8->4->2->1); exactly one champion; no team appears twice in a round.
  Layer 2 (official allocation, per sim): the third-place assignment used equals
      the official Annex C row for that exact qualifying set; every assigned third
      is eligible for its slot; no winner faces a third from its own group; the 8
      placed thirds are exactly the 8 qualifying groups.

Runs 20 reproducible sample simulations. NO scoring, NO 5,000-run sweep.
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

from wc_pool import bracket, data_io
from wc_pool.schedule_parser import parse_schedule, parse_feeder
from wc_pool.third_place import ThirdPlaceTable
from wc_pool.tournament import load_context, simulate_tournament

OUT = data_io.PROJECT_ROOT / "outputs"
N_SAMPLES = 20


def build_schedule_r32_and_eligibility():
    result, _ = parse_schedule()
    sched_r32, eligibility = [], {}
    for m in result.matches:
        if m.stage != "R32":
            continue
        f1, f2 = parse_feeder(m.feeder_1), parse_feeder(m.feeder_2)
        sched_r32.append((f1, f2))
        # eligibility: winner-group -> set of eligible third groups
        for a, b in ((f1, f2), (f2, f1)):
            if a[0] == "winner" and b[0] == "third":
                eligibility[a[1]] = set(b[1])
    return sched_r32, eligibility


def load_official_assignment_csv():
    """qualifying frozenset -> {winner_group: third_group} straight from the CSV,
    used as an INDEPENDENT reference (the engine loads the same file, but we parse
    it separately here so a wiring bug in tournament.py would surface)."""
    path = OUT / "third_place_mapping.csv"
    ref = {}
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        winner_cols = {c: c.split("_1")[1] for c in reader.fieldnames
                       if c not in ("option", "qualifying_groups")}
        for row in reader:
            assign = {wg: row[c].lstrip("3") for c, wg in winner_cols.items()}
            ref[frozenset(row["qualifying_groups"])] = assign
    return ref


def check_layer1(res) -> list[str]:
    p = []
    # R32: 32 distinct teams
    r32_teams = [t for pair in res.r32_teams.values() for t in pair]
    if len(res.r32_teams) != 16:
        p.append(f"R32 has {len(res.r32_teams)} matches, expected 16")
    if len(set(r32_teams)) != 32:
        p.append(f"R32 teams not 32 distinct (got {len(set(r32_teams))})")
    # round sizes & winner membership
    round_matches = {"R32": range(73, 89), "R16": range(89, 97),
                     "QF": range(97, 101), "SF": (101, 102)}
    for name, nums in round_matches.items():
        nums = list(nums)
        winners = [res.ko_winners.get(n) for n in nums]
        if any(w is None for w in winners):
            p.append(f"{name}: missing winners")
            continue
        if len(set(winners)) != len(nums):
            p.append(f"{name}: winners not distinct ({len(set(winners))}/{len(nums)})")
    # each winner must be one of its match's teams (R32 checked via r32_teams)
    for n, (a, b) in res.r32_teams.items():
        if res.ko_winners[n] not in (a, b):
            p.append(f"Match {n}: winner not a participant")
    if not res.champion:
        p.append("No champion")
    if res.ko_winners.get(104) != res.champion:
        p.append("Champion != winner of match 104")
    return p


def check_layer2(res, official_ref, eligibility) -> list[str]:
    p = []
    qual = res.qualified_third_groups
    if len(set(qual)) != 8:
        p.append(f"Qualified thirds not 8 distinct: {qual}")
        return p
    key = frozenset(qual)
    expected = official_ref.get(key)
    if expected is None:
        p.append(f"No Annex C row for qualifying set {sorted(qual)}")
        return p
    if res.annex_assignment != expected:
        p.append(f"Assignment != official Annex C for {sorted(qual)}: "
                f"got {res.annex_assignment} expected {expected}")
    placed_thirds = set(res.annex_assignment.values())
    if placed_thirds != set(qual):
        p.append(f"Placed thirds {sorted(placed_thirds)} != qualifying {sorted(qual)}")
    for winner_group, third_group in res.annex_assignment.items():
        if winner_group == third_group:
            p.append(f"Winner {winner_group} faces own-group third")
        if third_group not in eligibility[winner_group]:
            p.append(f"3{third_group} not eligible for 1{winner_group}")
    return p


def main():
    lines = []
    def out(s=""):
        lines.append(s);
    out("=" * 70)
    out("BRACKET VALIDATION  (Phase 1 gate - 20 sample sims, no scoring)")
    out("=" * 70)

    ctx = load_context()
    official_ref = load_official_assignment_csv()
    sched_r32, eligibility = build_schedule_r32_and_eligibility()

    # Layer 0: schedule vs official bracket
    out("\n[Layer 0] Schedule R32 matchups vs official bracket (R32_DEFS)")
    l0 = bracket.verify_against_schedule(sched_r32, eligibility)
    if l0:
        for prob in l0:
            out(f"    *** {prob}")
    else:
        out("    All 16 R32 matchups in schedule match the official bracket: OK")

    out(f"\n[Annex C] Official table rows loaded: {len(official_ref)} (expected 495)")

    total_problems = list(l0)
    out(f"\n[Layers 1 & 2] Running {N_SAMPLES} reproducible sample simulations")
    for seed in range(N_SAMPLES):
        rng = np.random.default_rng(seed)
        res = simulate_tournament(ctx, rng)
        l1 = check_layer1(res)
        l2 = check_layer2(res, official_ref, eligibility)
        status = "OK" if not (l1 or l2) else "*** PROBLEM ***"
        qual = "".join(sorted(res.qualified_third_groups))
        out(f"  sim {seed:2d}: champion={res.champion:<20} thirds={qual}  {status}")
        if l1 or l2:
            for prob in l1 + l2:
                out(f"        - {prob}")
            total_problems.extend(l1 + l2)

    # Show one detailed sample for eyeballing
    out("\n[Detail] Sample sim 0 third-place allocation")
    rng = np.random.default_rng(0)
    res = simulate_tournament(ctx, rng)
    for wg in sorted(res.annex_assignment):
        tg = res.annex_assignment[wg]
        winner_team = res.group_order[wg][0]
        third_team = res.group_order[tg][2]
        mnum = bracket.WINNER_TO_MATCH[wg]
        out(f"    M{mnum}: 1{wg} ({winner_team}) vs 3{tg} ({third_team})")
    out(f"    Champion path winner: {res.champion}")

    out("\n" + "=" * 70)
    if total_problems:
        out(f"RESULT: {len(total_problems)} PROBLEM(S) FOUND")
    else:
        out(f"RESULT: ALL BRACKET CHECKS PASSED ({N_SAMPLES} sims, "
            f"Layers 0/1/2)")
    out("=" * 70)

    report = "\n".join(lines)
    print(report)
    (OUT / "bracket_validation.txt").write_text(report, encoding="utf-8")
    print(f"\nWrote {OUT / 'bracket_validation.txt'}")


if __name__ == "__main__":
    main()
