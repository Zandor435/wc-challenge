#!/usr/bin/env python3
"""Resolve the knockout bracket into the schedule once the group stage is done.

THE PROBLEM THIS SOLVES
-----------------------
data/matches.csv ships the knockout fixtures as static "TBD vs TBD" rows (the
real teams aren't known until the groups finish). Everything downstream — the
homepage "Today's Matches" hero, predictions, the email "Up Next" — reads
matches.csv and treats TBD as "unknown", so once the tournament reaches the
Round of 32 the schedule freezes on placeholder cards. This step fills those
rows in.

HOW IT WORKS
------------
It reuses the SAME engine the win-probability simulator uses (sim/), so the
bracket logic can never drift from the live pipeline:

  1. Read the completed results (site/data/daily_results.json — the committed,
     accumulated scored output) and rebuild the final group standings
     deterministically (points, then goal difference, then goals for — the FIFA
     order; no random tiebreak, unlike the Monte Carlo sim).
  2. Resolve group winners / runners-up / the 8 best third-placed teams through
     the official Annex C table into the R32 pairings (matches 73-88).
  3. Propagate the winners of any COMPLETED knockout matches forward into the
     later rounds (R16 -> QF -> SF -> Final / third place), filling each fixture
     the moment both of its feeder matches have a decided winner.
  4. Rewrite ONLY the team columns of the knockout rows in matches.csv, mapping
     each round's rows in file order to that round's match numbers (R32 rows ->
     73..88, R16 -> 89..96, ...). Dates, venues and kickoff times are the
     hand-authored schedule and are preserved untouched. Rows whose teams aren't
     decided yet are left exactly as they were (still TBD).

OVERWRITE-BY-DEFAULT: the knockout rows are fully regenerated from results each
run, so it is idempotent and safe to rerun. The group rows are passed through
verbatim. It writes both data/matches.csv (the source) and
site/data/matches.csv (the copy the static site serves).

Gate: if the group stage is not yet complete (any group match missing from the
results) the standings aren't final, so the script makes NO changes and exits 0.
That makes it safe to wire into the every-run pipeline before the groups finish.

A knockout match that ended level and was decided on penalties is dropped by the
free worldcup26.ir feed (it exposes no shootout aggregate — see fetch_results.py),
so its winner stays unknown here and the fixtures it feeds remain TBD until the
feed carries a decided result. That is a feed limitation, not a bug in this step.

Usage:
    python resolve_knockout_schedule.py
    python resolve_knockout_schedule.py --results site/data/daily_results.json \
        --matches data/matches.csv --site-matches site/data/matches.csv
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os

from sim import bracket, engine

HERE = os.path.dirname(os.path.abspath(__file__))

# matches.csv phase -> the ordered list of official FIFA match numbers for that
# round. The knockout rows of each round, in file order, map onto these numbers
# (the csv is authored in bracket order, matching sim/bracket.py's numbering).
PHASE_MATCH_NUMBERS = {
    "round_of_32": sorted(bracket.R32_DEFS),     # 73..88
    "round_of_16": sorted(bracket.R16_DEFS),     # 89..96
    "quarterfinal": sorted(bracket.QF_DEFS),     # 97..100
    "semifinal": sorted(bracket.SF_DEFS),        # 101, 102
    "third_place": [103],
    "final": [104],
}
KNOCKOUT_PHASES = set(PHASE_MATCH_NUMBERS)


def load_completed(ctx: engine.Context, results_path: str):
    """Return (LockedResults, group_complete: bool) from a daily_results.json or a
    live_results.json. Both share the per-match schema split_results expects."""
    with open(results_path, encoding="utf-8") as fh:
        raw = json.load(fh)
    if "days" in raw:   # site/data/daily_results.json shape
        matches = [
            {"stage": m["stage"], "round": m.get("round"),
             "home": m["home"], "away": m["away"],
             "home_score": m["home_score"], "away_score": m["away_score"]}
            for day in raw.get("days", []) for m in day.get("matches", [])
        ]
    else:               # live_results.json / fetch_results.py shape
        matches = raw.get("matches", [])
    locked = engine.split_results(ctx, {"matches": matches})
    group_complete = all(
        frozenset((a, b)) in locked.group for _g, a, b, _d in ctx.group_pairings
    )
    return locked, group_complete


def final_standings(ctx: engine.Context, locked: engine.LockedResults):
    """Final group order per group, deterministic (points, GD, GF) — the FIFA
    primary tiebreakers. Assumes every group match is complete."""
    record = {t: {"pts": 0, "gd": 0, "gf": 0} for g in ctx.groups for t in ctx.groups[g]}
    for _g, a, b, _d in ctx.group_pairings:
        lk = locked.group[frozenset((a, b))]
        ga, gb = lk[a], lk[b]
        record[a]["gf"] += ga; record[a]["gd"] += ga - gb
        record[b]["gf"] += gb; record[b]["gd"] += gb - ga
        if ga > gb:
            record[a]["pts"] += 3
        elif gb > ga:
            record[b]["pts"] += 3
        else:
            record[a]["pts"] += 1; record[b]["pts"] += 1

    def rank_key(t):
        r = record[t]
        return (r["pts"], r["gd"], r["gf"])

    group_order = {g: sorted(teams, key=rank_key, reverse=True)
                   for g, teams in ctx.groups.items()}
    thirds = sorted(group_order, key=lambda g: rank_key(group_order[g][2]), reverse=True)
    qual_third_groups = thirds[:8]
    annex = ctx.third_table.assign(qual_third_groups)
    return group_order, annex


def build_resolver(ctx, locked, group_order, annex):
    """Return pairing(mnum) -> (teamA, teamB) with None for any undecided slot."""
    def resolve_feeder(feeder):
        kind, val = feeder
        if kind == "W":
            return group_order[val][0]
        if kind == "R":
            return group_order[val][1]
        return group_order[annex[val]][2]   # Annex-C third

    r32_pair = {m: (resolve_feeder(f1), resolve_feeder(f2))
                for m, (f1, f2) in bracket.R32_DEFS.items()}

    pair_cache: dict[int, tuple] = {}

    def winner(mnum):
        a, b = pairing(mnum)
        if a is None or b is None:
            return None
        return locked.ko.get(frozenset((a, b)))   # None until the match is decided

    def loser(mnum):
        a, b = pairing(mnum)
        w = winner(mnum)
        if a is None or b is None or w is None:
            return None
        return a if w == b else b

    def pairing(mnum):
        if mnum in pair_cache:
            return pair_cache[mnum]
        if mnum in r32_pair:
            res = r32_pair[mnum]
        elif mnum in bracket.R16_DEFS:
            m1, m2 = bracket.R16_DEFS[mnum]; res = (winner(m1), winner(m2))
        elif mnum in bracket.QF_DEFS:
            m1, m2 = bracket.QF_DEFS[mnum]; res = (winner(m1), winner(m2))
        elif mnum in bracket.SF_DEFS:
            m1, m2 = bracket.SF_DEFS[mnum]; res = (winner(m1), winner(m2))
        elif mnum == 104:                          # Final
            m1, m2 = bracket.FINAL_FEEDERS; res = (winner(m1), winner(m2))
        elif mnum == 103:                          # third-place game
            m1, m2 = bracket.THIRD_PLACE_FEEDERS; res = (loser(m1), loser(m2))
        else:
            res = (None, None)
        pair_cache[mnum] = res
        return res

    return pairing


def fill_rows(ctx, rows, fieldnames, pairing):
    """Rewrite knockout rows in place (file order -> match numbers). Returns the
    count of fixtures filled. Group rows and still-undecided knockout rows are
    left untouched."""
    counters: dict[str, int] = {}
    filled = 0
    for row in rows:
        phase = row["phase"].strip().lower()
        if phase not in KNOCKOUT_PHASES:
            continue
        idx = counters.get(phase, 0)
        counters[phase] = idx + 1
        nums = PHASE_MATCH_NUMBERS[phase]
        if idx >= len(nums):
            print(f"  WARNING: more '{phase}' rows than bracket slots "
                  f"({idx + 1} > {len(nums)}) — leaving extra row untouched.")
            continue
        a, b = pairing(nums[idx])
        if a is None or b is None:
            continue   # not decided yet — keep the existing TBD row verbatim
        row["team1"], row["team2"] = a, b
        row["team1_owner"] = ctx.owner_of.get(a, "")
        row["team2_owner"] = ctx.owner_of.get(b, "")
        row["team1_tier"] = ctx.tiers.get(a, "")
        row["team2_tier"] = ctx.tiers.get(b, "")
        filled += 1
    return filled


def write_csv(path, fieldnames, rows):
    """Serialize with the same minimal quoting as the source (only fields with a
    comma get quoted), then write only if the content actually changed."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    new = buf.getvalue()
    try:
        with open(path, encoding="utf-8") as fh:
            if fh.read() == new:
                return False
    except FileNotFoundError:
        pass
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(new)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=os.path.join(HERE, "site", "data", "daily_results.json"),
                    help="scored results (daily_results.json or live_results.json)")
    ap.add_argument("--matches", default=os.path.join(HERE, "data", "matches.csv"),
                    help="canonical schedule to rewrite")
    ap.add_argument("--site-matches", default=os.path.join(HERE, "site", "data", "matches.csv"),
                    help="served copy of the schedule to keep in sync ('' to skip)")
    args = ap.parse_args()

    ctx = engine.load_context()

    if not os.path.exists(args.results):
        print(f"No results file at {args.results}; nothing to resolve.")
        return
    locked, group_complete = load_completed(ctx, args.results)
    if not group_complete:
        print("Group stage not complete yet — standings aren't final; "
              "leaving the knockout schedule untouched.")
        return

    group_order, annex = final_standings(ctx, locked)
    pairing = build_resolver(ctx, locked, group_order, annex)

    with open(args.matches, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames
        rows = list(reader)

    filled = fill_rows(ctx, rows, fieldnames, pairing)

    targets = [args.matches] + ([args.site_matches] if args.site_matches else [])
    changed = [p for p in targets if write_csv(p, fieldnames, rows)]
    print(f"Resolved {filled} knockout fixture(s) from results.")
    if changed:
        print("Updated: " + ", ".join(os.path.relpath(p, HERE) for p in changed))
    else:
        print("Schedule already up to date — no changes written.")


if __name__ == "__main__":
    main()
