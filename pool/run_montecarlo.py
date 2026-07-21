"""Monte Carlo scoring run -> phase1_summary.csv

Simulates the full 2026 World Cup N times, scores each owner under the current
scoring_config.json (provisional_v1), and writes a per-owner summary plus a
human-readable report (including an upset-bonus breakdown).

Usage:
    python run_montecarlo.py [n_sims] [seed]
Defaults: 20000 sims, seed 20260603 (reproducible).
"""

from __future__ import annotations

import csv
import statistics
import sys
from collections import defaultdict

import numpy as np

from wc_pool import data_io, scoring
from wc_pool.tournament import load_context

# all upset matchups (winner_tier, loser_tier) where the winner is the weaker side
UPSET_TYPES = [(2, 1), (3, 2), (4, 3), (3, 1), (4, 2), (4, 1)]
TYPE_LABEL = {(2, 1): "T2 beats T1", (3, 2): "T3 beats T2", (4, 3): "T4 beats T3",
              (3, 1): "T3 beats T1", (4, 2): "T4 beats T2", (4, 1): "T4 beats T1"}


def main():
    n_sims = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 20260603

    aliases = data_io.load_aliases()
    drafted = data_io.load_drafted_teams(aliases)
    tiers = data_io.load_full_tiers(aliases)
    cfg = data_io.load_scoring_config()
    ctx = load_context()
    roster = scoring.build_roster(drafted)

    # sanity: every team in the sim must have both a rating and a tier
    miss_rating = sorted(t for t in roster if t not in ctx.ratings)
    if miss_rating:
        raise SystemExit(f"Drafted teams missing a strength rating (alias gap): {miss_rating}")
    miss_tier = sorted(t for t in ctx.ratings if t not in tiers)
    if miss_tier:
        raise SystemExit(f"Teams in the field missing a tier in tiers.csv: {miss_tier}")

    owners = sorted({r["owner"] for r in drafted})
    rng = np.random.default_rng(seed)

    totals = {o: [] for o in owners}
    comp_sums = {o: defaultdict(float) for o in owners}
    wins = {o: 0.0 for o in owners}
    champ_owner_counts = defaultdict(int)
    champ_team_counts = defaultdict(int)
    # upset_agg[(wt,lt)][owner] = total count over all sims
    upset_agg = {ut: defaultdict(int) for ut in UPSET_TYPES}

    for _ in range(n_sims):
        res = scoring.simulate_and_score(ctx, roster, tiers, cfg, rng)
        t = res["totals"]
        for o in owners:
            totals[o].append(t[o])
            for k, v in res["components"][o].items():
                comp_sums[o][k] += v
        top = max(t.values())
        leaders = [o for o in owners if t[o] == top]
        for o in leaders:
            wins[o] += 1.0 / len(leaders)
        if res["champion_owner"]:
            champ_owner_counts[res["champion_owner"]] += 1
        champ_team_counts[res["champion"]] += 1
        for ut, by_owner in res["upsets"].items():
            for o, c in by_owner.items():
                upset_agg[ut][o] += c

    # ---- write phase1_summary.csv -------------------------------------------
    fields = ["owner", "sims", "win_prob", "mean_score", "median_score", "std_score",
              "p10_score", "p25_score", "p75_score", "p90_score", "min_score", "max_score",
              "mean_group_base", "mean_group_upset", "mean_ko_match_pts", "mean_ko_advance",
              "mean_qualifiers", "mean_ko_wins", "champion_prob"]
    rows = []
    for o in owners:
        arr = sorted(totals[o])
        def pct(p):
            return arr[min(len(arr) - 1, int(p / 100 * len(arr)))]
        rows.append({
            "owner": o,
            "sims": n_sims,
            "win_prob": round(wins[o] / n_sims, 4),
            "mean_score": round(statistics.fmean(arr), 3),
            "median_score": round(statistics.median(arr), 3),
            "std_score": round(statistics.pstdev(arr), 3),
            "p10_score": pct(10), "p25_score": pct(25),
            "p75_score": pct(75), "p90_score": pct(90),
            "min_score": arr[0], "max_score": arr[-1],
            "mean_group_base": round(comp_sums[o]["group_base"] / n_sims, 3),
            "mean_group_upset": round(comp_sums[o]["group_upset"] / n_sims, 3),
            "mean_ko_match_pts": round(comp_sums[o]["ko_match"] / n_sims, 3),
            "mean_ko_advance": round(comp_sums[o]["ko_advance"] / n_sims, 3),
            "mean_qualifiers": round(comp_sums[o]["qualifiers"] / n_sims, 3),
            "mean_ko_wins": round(comp_sums[o]["ko_wins"] / n_sims, 3),
            "champion_prob": round(champ_owner_counts.get(o, 0) / n_sims, 4),
        })
    rows.sort(key=lambda r: r["win_prob"], reverse=True)
    with open("phase1_summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    # ---- human-readable report ----------------------------------------------
    L = []
    L.append(f"Monte Carlo scoring run  |  {n_sims:,} sims  |  seed {seed}")
    L.append(f"Scoring config: {cfg.get('version')}  (winner-takes-all, ${cfg['meta']['buyin']} buy-in)")
    L.append("=" * 74)
    L.append("")
    L.append("POOL WIN PROBABILITY (share of sims with the top score; ties split)")
    for r in rows:
        bar = "#" * int(round(r["win_prob"] * 50))
        L.append(f"  {r['owner']:8s} {r['win_prob']*100:5.1f}%  {bar}")
    spread = rows[0]["win_prob"] - rows[-1]["win_prob"]
    L.append(f"  spread (leader - laggard): {spread*100:.1f} pts")
    L.append("")
    L.append("MEAN SCORE AND COMPOSITION (points per tournament)")
    L.append(f"  {'owner':8s} {'mean':>7s} {'median':>7s} {'grp_base':>9s} {'upset':>6s} "
             f"{'ko_match':>9s} {'ko_adv':>7s} {'qual':>5s}")
    for r in rows:
        L.append(f"  {r['owner']:8s} {r['mean_score']:7.2f} {r['median_score']:7.1f} "
                 f"{r['mean_group_base']:9.2f} {r['mean_group_upset']:6.2f} "
                 f"{r['mean_ko_match_pts']:9.2f} {r['mean_ko_advance']:7.2f} {r['mean_qualifiers']:5.2f}")
    L.append("")

    # ---- upset bonus breakdown ----------------------------------------------
    L.append("UPSET BONUS BREAKDOWN  (+2 each; fires when a drafted team beats a")
    L.append("stronger-tier opponent; tiers from tiers.csv, all 48 teams)")
    grand_total = sum(sum(upset_agg[ut].values()) for ut in UPSET_TYPES)
    L.append(f"  total upset bonuses per sim: {grand_total / n_sims:.3f}   "
             f"(= {grand_total / n_sims * 2:.2f} bonus pts/sim across the pool)")
    L.append("")
    L.append(f"  {'matchup':14s} {'gap':>3s} {'per_sim':>8s}   by owner (per sim)")
    for ut in sorted(UPSET_TYPES, key=lambda x: (x[0] - x[1], x[0])):
        total = sum(upset_agg[ut].values())
        gap = ut[0] - ut[1]
        owner_bits = "  ".join(f"{o} {upset_agg[ut][o]/n_sims:.3f}" for o in owners)
        L.append(f"  {TYPE_LABEL[ut]:14s} {gap:>3d} {total/n_sims:8.3f}   {owner_bits}")
    L.append("")
    L.append("  upset bonuses by owner (per sim, all matchup types):")
    for o in owners:
        o_total = sum(upset_agg[ut][o] for ut in UPSET_TYPES)
        L.append(f"    {o:8s} {o_total/n_sims:.3f} bonuses  ->  {o_total/n_sims*2:.2f} pts/sim")
    L.append("  (note: T2-beats-T1 is included though it was not in the requested list,")
    L.append("   because under the rule winner_tier > loser_tier it also fires.)")
    L.append("")

    # ---- champions ----------------------------------------------------------
    L.append("CHAMPION PROBABILITY BY OWNER (owner holds the team that wins it all)")
    drafted_champ = sum(champ_owner_counts.values())
    for r in sorted(rows, key=lambda x: x["champion_prob"], reverse=True):
        L.append(f"  {r['owner']:8s} {r['champion_prob']*100:5.1f}%")
    L.append(f"  any drafted team champion: {drafted_champ/n_sims*100:.1f}%  "
             f"(undrafted: {(1-drafted_champ/n_sims)*100:.1f}%)")
    L.append("")
    L.append("TOP 8 MOST-LIKELY CHAMPIONS (all teams)")
    for team, c in sorted(champ_team_counts.items(), key=lambda kv: kv[1], reverse=True)[:8]:
        own = roster.get(team, {}).get("owner", "-")
        L.append(f"  {team:24s} {c/n_sims*100:5.1f}%   owner: {own}")
    report = "\n".join(L)

    with open("outputs/phase1_report.txt", "w", encoding="utf-8") as f:
        f.write(report + "\n")
    print(report)
    print("\nwrote phase1_summary.csv and outputs/phase1_report.txt")


if __name__ == "__main__":
    main()
