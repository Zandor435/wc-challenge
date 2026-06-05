#!/usr/bin/env python3
"""Daily win-probability engine for the WC Challenge.

Locks in all actual results so far and simulates ONLY the remaining matches
forward (Monte Carlo), scoring everything under rebalanced_v3, to estimate each
owner's probability of finishing the tournament in 1st place -- plus a p10/median/
p90 band on their final point total. Appends a dated snapshot to
site/data/timeline.json so the site can draw a win-probability line chart.

Pipeline position: runs AFTER scoring.py in the update cycle. scoring.py turns
the latest results into site/data/*.json; this reads the same results and the
draft/strength/fixture data, then writes the one file it owns: timeline.json.

Reuses the vendored preseason simulation engine (sim/) unchanged: same match
model, bracket tree, and Annex C third-place allocation. A zero-results run
therefore reproduces the preseason 20k baseline within Monte Carlo noise.

Usage:
    # daily, during the tournament (uses the live results scoring.py consumed):
    python win_probability.py --results data/live_results.json

    # one-time preseason / Day 0 snapshot (no results locked):
    python win_probability.py --results none --date 2026-06-10 --label preseason

Inputs (read-only):
    data/matches.csv            full fixture list (group structure + dates)
    data/team_strength.csv      Elo-style strength ratings (canonicalised on load)
    data/third_place_mapping.csv  Annex C 8-of-12 third-place allocation
    data/draft_board.json       owner -> 6 teams
    data/tiers.json             48-team tiers (upset bonus)
    data/scoring_config.json    rebalanced_v3 rules
    data/team_aliases.json      spelling -> canonical name
    <results JSON>              actual results so far (scoring.py schema)
    site/data/owner_standings.json   current standings (consistency check only)

Output (the ONLY file this writes):
    site/data/timeline.json     cumulative [{date, matchday, win_probability,
                                projected_points, champion_probability}, ...]
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from sim import engine

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RESULTS = os.path.join(HERE, "data", "live_results.json")
TIMELINE = os.path.join(HERE, "site", "data", "timeline.json")
OWNER_STANDINGS = os.path.join(HERE, "site", "data", "owner_standings.json")

DEFAULT_SEED = 20260603   # same seed as the preseason 20k baseline (reproducible)


def load_results(path: str) -> dict:
    """Load the actual-results JSON, or an empty result set for a Day-0 run."""
    if path is None or str(path).lower() in ("none", "", "empty"):
        return {"matches": []}
    if not os.path.exists(path):
        print(f"  (results file {path} not found -> treating as zero results)")
        return {"matches": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def percentiles(sorted_arr, ps):
    """Nearest-rank percentile, matching the preseason run_montecarlo convention."""
    n = len(sorted_arr)
    return {p: sorted_arr[min(n - 1, int(p / 100 * n))] for p in ps}


def run_sims(ctx, locked, n_sims, seed):
    rng = np.random.default_rng(seed)
    owners = ctx.owners
    totals = {o: [] for o in owners}
    wins = {o: 0.0 for o in owners}
    champ = defaultdict(int)

    for _ in range(n_sims):
        res = engine.simulate_forward(ctx, locked, rng)
        t = res["totals"]
        for o in owners:
            totals[o].append(t[o])
        top = max(t.values())
        leaders = [o for o in owners if t[o] == top]
        for o in leaders:                 # ties split evenly (preseason convention)
            wins[o] += 1.0 / len(leaders)
        if res["champion_owner"]:
            champ[res["champion_owner"]] += 1

    win_prob, proj, champ_prob = {}, {}, {}
    for o in owners:
        arr = sorted(totals[o])
        pct = percentiles(arr, (10, 50, 90))
        win_prob[o] = round(wins[o] / n_sims, 4)
        proj[o] = {"p10": round(pct[10]), "median": round(pct[50]), "p90": round(pct[90])}
        champ_prob[o] = round(champ.get(o, 0) / n_sims, 4)
    return win_prob, proj, champ_prob


def upsert_timeline(entry: dict, path: str = TIMELINE):
    """Append the entry, or overwrite the existing entry for the same date.
    Never clobbers other days -- the timeline is cumulative."""
    timeline = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                timeline = json.load(f)
            if not isinstance(timeline, list):
                timeline = []
        except (json.JSONDecodeError, OSError):
            timeline = []
    timeline = [e for e in timeline if e.get("date") != entry["date"]]
    timeline.append(entry)
    timeline.sort(key=lambda e: (e.get("date") or "", e.get("matchday") or 0))
    out_dir = os.path.dirname(path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(timeline, f, indent=2, ensure_ascii=False)
    return len(timeline)


def consistency_check(ctx, locked):
    """Compare locked-only points to site/data/owner_standings.json (informational)."""
    locked_pts = engine.score_locked_only(ctx, locked)
    if not os.path.exists(OWNER_STANDINGS):
        return locked_pts, None
    try:
        with open(OWNER_STANDINGS, encoding="utf-8") as f:
            standings = {s["owner"]: s["total_points"]
                         for s in json.load(f).get("standings", [])}
    except (json.JSONDecodeError, OSError, KeyError):
        return locked_pts, None
    mismatch = {o: (locked_pts[o], standings.get(o)) for o in ctx.owners
                if standings.get(o) is not None and abs(locked_pts[o] - standings[o]) > 1e-6}
    return locked_pts, mismatch


def main():
    ap = argparse.ArgumentParser(description="WC Challenge daily win-probability engine")
    ap.add_argument("--results", default=DEFAULT_RESULTS,
                    help="actual-results JSON (scoring.py schema). 'none' for a zero-results run.")
    ap.add_argument("--sims", type=int, default=5000, help="Monte Carlo simulations (default 5000)")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED, help="RNG seed (default = preseason seed)")
    ap.add_argument("--date", default=None,
                    help="entry date YYYY-MM-DD (default: latest completed match date, else today UTC)")
    ap.add_argument("--label", default=None, help="optional label stored on the entry (e.g. 'preseason')")
    ap.add_argument("--out", default=TIMELINE, help="timeline.json path")
    ap.add_argument("--skip-if-empty", action="store_true",
                    help="exit without writing if there are zero locked results "
                         "(used in CI so the pre-tournament cron doesn't append duplicate baselines)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    ctx = engine.load_context()
    results = load_results(args.results)
    if args.skip_if_empty and not results.get("matches"):
        print("No results yet (0 matches); --skip-if-empty set -> leaving timeline.json untouched.")
        return
    locked = engine.split_results(ctx, results)

    # entry date + matchday from the completed matches themselves
    completed_dates = sorted({m["date"] for m in results.get("matches", []) if m.get("date")})
    matchday = len(completed_dates)
    if args.date:
        entry_date = args.date
    elif completed_dates:
        entry_date = completed_dates[-1]
    else:
        entry_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    win_prob, proj, champ_prob = run_sims(ctx, locked, args.sims, args.seed)
    locked_pts, mismatch = consistency_check(ctx, locked)

    entry = {
        "date": entry_date,
        "matchday": matchday,
        "win_probability": win_prob,
        "projected_points": proj,
        "champion_probability": champ_prob,
    }
    if args.label:
        entry["label"] = args.label
    n_entries = upsert_timeline(entry, args.out)

    if not args.quiet:
        order = sorted(ctx.owners, key=lambda o: -win_prob[o])
        print(f"\n=== Win probability  |  {args.sims:,} sims  |  seed {args.seed} "
              f"|  {locked.n_group} group + {locked.n_ko} KO matches locked ===")
        print(f"date={entry_date}  matchday={matchday}")
        for o in order:
            p = proj[o]
            bar = "#" * int(round(win_prob[o] * 50))
            print(f"  {o:8s} {win_prob[o]*100:5.1f}%  champ {champ_prob[o]*100:4.1f}%  "
                  f"pts p10/med/p90 = {p['p10']:3d}/{p['median']:3d}/{p['p90']:3d}  {bar}")
        print(f"  locked points so far: " +
              ", ".join(f"{o} {locked_pts[o]:g}" for o in order))
        if mismatch:
            print("  NOTE: locked points differ from owner_standings.json "
                  f"(expected if --results isn't the live source): {mismatch}")
        print(f"  wrote {args.out}  ({n_entries} dated entries)")


if __name__ == "__main__":
    main()
