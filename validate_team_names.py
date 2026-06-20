#!/usr/bin/env python3
"""Fail the pipeline if a fetched team name doesn't resolve to a canonical team.

Runs BETWEEN fetch and score. Loads data/team_aliases.json + data/tiers.json and
applies the exact alias resolution scoring.py uses (``aliases.get(name, name)``,
see scoring.py make_canon). Every resolved name must be one of the 48 canonical
teams in tiers.json. A name that doesn't resolve is almost always a new feed
spelling missing from team_aliases.json -- scoring would silently treat that team
as "undrafted" and drop its points (exactly the DR Congo bug). Better to block the
run and name the offender so the alias gets added.

Pure name-resolution check, nothing else. Exit 0 = all recognized (or no matches to
check); exit 1 = one or more unmapped names (workflow fails).

Usage:
    python validate_team_names.py --results data/live_results.json
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load(path):
    full = path if os.path.isabs(path) else os.path.join(HERE, path)
    with open(full, encoding="utf-8") as fh:
        return json.load(fh)


def main():
    ap = argparse.ArgumentParser(description="Fail if any fetched team name is unmapped.")
    ap.add_argument("--results", default=os.path.join("data", "live_results.json"),
                    help="fetched results JSON (default: data/live_results.json)")
    args = ap.parse_args()

    results = load(args.results)
    aliases = load(os.path.join("data", "team_aliases.json"))["aliases"]
    canonical = set(load(os.path.join("data", "tiers.json"))["tiers"])

    matches = results.get("matches", [])
    if not matches:
        print("validate_team_names: 0 matches in results -- nothing to check.")
        return 0

    # raw name -> set of fixture dates it appears in (for a helpful error message)
    seen = {}
    for m in matches:
        for side in ("home", "away"):
            name = m.get(side)
            if name:
                seen.setdefault(name, set()).add(m.get("date", "?"))

    unmapped = []
    for name in sorted(seen):
        resolved = aliases.get(name, name)
        if resolved not in canonical:
            unmapped.append((name, resolved, sorted(seen[name])))

    if unmapped:
        print("ERROR: unrecognized team name(s) in fetched data -- scoring would drop "
              "their points.\n", file=sys.stderr)
        for name, resolved, dates in unmapped:
            arrow = f" (alias -> {resolved!r})" if resolved != name else ""
            print(f"  - {name!r}{arrow} is not a canonical team in data/tiers.json",
                  file=sys.stderr)
            print(f"      appears in fixtures dated: {', '.join(dates)}", file=sys.stderr)
        print("\nFix: add the spelling to data/team_aliases.json, mapping it to the "
              "canonical\ndraft-board name (the 48 names in data/tiers.json), then re-run.",
              file=sys.stderr)
        return 1

    print(f"validate_team_names: OK -- all {len(seen)} team names resolve to canonical teams.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
