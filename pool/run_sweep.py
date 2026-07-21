"""Rebalance sweep: find a scoring config that pulls owner win% toward parity.

Strategy: tournament OUTCOMES are independent of the pool scoring config (group
qualification always uses real 3/1/0 football rules; pool scoring is applied
afterward). So we simulate N tournaments ONCE, record per-owner bucketed event
counts, then score every candidate config against those records with vectorized
arithmetic. Identical seed => identical tournaments for every config.

Locked rules (never varied): draw=1, loss=0, knockout match win=3,
R32 advancement=0, knockouts have no draw, third-place game generates points
(its winner earns the +3 knockout match-win point).

Varied levers:
  1. advancement bonus curve (R16/QF/SF/Final/WinWC)
  2. tier-weighted advancement (bonus * winning team's tier number)
  3. upset bonus magnitude (flat, or scaled by tier gap)
  4. group-stage win points (3/4/5)

Usage: python run_sweep.py [phase A|B|all] [n_sims] [seed]
"""

from __future__ import annotations

import csv
import sys

import numpy as np

from wc_pool import bracket, data_io, match_model
from wc_pool.tournament import load_context

RK = ["round_of_16", "quarterfinal", "semifinal", "final", "win_world_cup"]
RK_IDX = {k: i for i, k in enumerate(RK)}
_WIN_ADVANCES_INTO = {"R32": "round_of_16", "R16": "quarterfinal",
                      "QF": "semifinal", "SF": "final", "Final": "win_world_cup"}
GAP = np.array([1, 2, 3], dtype=float)        # tier gaps 1,2,3
TIERS4 = np.array([1, 2, 3, 4], dtype=float)  # tier multipliers


# --------------------------------------------------------------------------- #
# 1. record phase: run N tournaments once, fill per-owner numpy arrays
# --------------------------------------------------------------------------- #
def record(ctx, roster, tiers, owners, n_sims, seed):
    oi = {o: i for i, o in enumerate(owners)}
    owner_of = {t: roster[t]["owner"] for t in roster}
    no = len(owners)

    A_gw = np.zeros((no, n_sims))            # group wins by drafted teams
    A_gd = np.zeros((no, n_sims))            # group draws
    A_upset = np.zeros((no, n_sims, 3))      # drafted-team upset wins, by gap (1,2,3)
    A_kowins = np.zeros((no, n_sims))        # knockout wins (match points; incl 3rd place)
    A_adv = np.zeros((no, n_sims, 5, 4))     # advancement events by (round, winner tier)
    champ_team = []
    rng = np.random.default_rng(seed)

    for s in range(n_sims):
        record_one(ctx, roster, tiers, owner_of, oi, rng, s,
                   A_gw, A_gd, A_upset, A_kowins, A_adv, champ_team)
    return A_gw, A_gd, A_upset, A_kowins, A_adv, champ_team


def record_one(ctx, roster, tiers, owner_of, oi, rng, s,
               A_gw, A_gd, A_upset, A_kowins, A_adv, champ_team):
    rec = {t: {"pts": 0, "gd": 0, "gf": 0} for g in ctx.groups for t in ctx.groups[g]}
    for g, a, b in ctx.group_pairings:
        ga, gb = match_model.simulate_group_match(ctx.ratings[a], ctx.ratings[b], rng)
        rec[a]["gf"] += ga; rec[a]["gd"] += ga - gb
        rec[b]["gf"] += gb; rec[b]["gd"] += gb - ga
        if ga == gb:
            rec[a]["pts"] += 1; rec[b]["pts"] += 1
            for t in (a, b):
                o = owner_of.get(t)
                if o is not None:
                    A_gd[oi[o], s] += 1
            continue
        win, lose = (a, b) if ga > gb else (b, a)
        rec[win]["pts"] += 3
        o = owner_of.get(win)
        if o is not None:
            A_gw[oi[o], s] += 1
            wt, lt = tiers.get(win), tiers.get(lose)
            if wt is not None and lt is not None and wt > lt:
                A_upset[oi[o], s, (wt - lt) - 1] += 1

    group_order = {g: sorted(teams, key=lambda t: (rec[t]["pts"], rec[t]["gd"],
                   rec[t]["gf"], rng.random()), reverse=True)
                   for g, teams in ctx.groups.items()}
    thirds = sorted(group_order, key=lambda g: (rec[group_order[g][2]]["pts"],
             rec[group_order[g][2]]["gd"], rec[group_order[g][2]]["gf"], rng.random()),
             reverse=True)
    annex = ctx.third_table.assign(thirds[:8])

    def resolve(f):
        k, v = f
        if k == "W": return group_order[v][0]
        if k == "R": return group_order[v][1]
        return group_order[annex[v]][2]

    ko_w, ko_l = {}, {}

    def play(mnum, a, b, advance=True):
        a_adv = match_model.simulate_knockout(ctx.ratings[a], ctx.ratings[b], rng)
        win, lose = (a, b) if a_adv else (b, a)
        ko_w[mnum] = win; ko_l[mnum] = lose
        o = owner_of.get(win)
        if o is not None:
            A_kowins[oi[o], s] += 1                       # +3 match point (locked)
            if advance:
                key = _WIN_ADVANCES_INTO[bracket.ROUND_NAME[mnum]]
                A_adv[oi[o], s, RK_IDX[key], tiers[win] - 1] += 1
        return win

    for mnum, (f1, f2) in bracket.R32_DEFS.items():
        play(mnum, resolve(f1), resolve(f2))
    for defs in (bracket.R16_DEFS, bracket.QF_DEFS, bracket.SF_DEFS):
        for mnum, (m1, m2) in defs.items():
            play(mnum, ko_w[m1], ko_w[m2])
    # third-place game (match 103): SF losers, match points only, NO advancement
    play(103, ko_l[101], ko_l[102], advance=False)
    champ = play(104, ko_w[bracket.FINAL_FEEDERS[0]], ko_w[bracket.FINAL_FEEDERS[1]])
    champ_team.append(champ)


# --------------------------------------------------------------------------- #
# 2. score phase: evaluate a config against the recorded arrays (vectorized)
# --------------------------------------------------------------------------- #
def score_config(cfg, arrays, owners):
    A_gw, A_gd, A_upset, A_kowins, A_adv = arrays
    no, n = A_gw.shape
    totals = np.zeros((n, no))
    group_pts = np.zeros((n, no))
    ko_pts = np.zeros((n, no))
    b = np.array([cfg["adv"][RK_IDX[k]] for k in RK], dtype=float)   # (5,)
    weight = b[:, None] * (TIERS4 if cfg["tier_weighted"] else np.ones(4))  # (5,4)
    for i in range(no):
        base = A_gw[i] * cfg["group_win"] + A_gd[i] * 1.0
        if cfg["upset_mode"] == "flat":
            upset = cfg["upset_value"] * A_upset[i].sum(axis=1)
        else:  # gap-scaled
            upset = cfg["upset_value"] * (A_upset[i] * GAP).sum(axis=1)
        gp = base + upset
        adv = (A_adv[i] * weight[None, :, :]).sum(axis=(1, 2))
        kp = A_kowins[i] * 3.0 + adv
        group_pts[:, i] = gp
        ko_pts[:, i] = kp
        totals[:, i] = gp + kp
    mx = totals.max(axis=1, keepdims=True)
    is_max = (totals == mx).astype(float)
    win_share = is_max / is_max.sum(axis=1, keepdims=True)
    winpct = win_share.mean(axis=0)
    mean_total = totals.mean(axis=0)
    group_share = group_pts.sum() / totals.sum()
    return {"winpct": winpct, "mean_total": mean_total,
            "overall_mean": totals.mean(), "group_share": group_share,
            "spread": winpct.max() - winpct.min()}


# --------------------------------------------------------------------------- #
# 3. config definitions
# --------------------------------------------------------------------------- #
def cfg(name, group_win=3, upset_mode="flat", upset_value=2,
        adv=(3, 5, 7, 10, 15), tier_weighted=False):
    return {"name": name, "group_win": group_win, "upset_mode": upset_mode,
            "upset_value": upset_value, "adv": adv, "tier_weighted": tier_weighted}


def phase_a():
    return [
        cfg("baseline (current)"),
        # 1. advancement curves (flatter -> should compress deep-run advantage)
        cfg("adv 2/3/4/6/10", adv=(2, 3, 4, 6, 10)),
        cfg("adv 3/4/5/7/10", adv=(3, 4, 5, 7, 10)),
        cfg("adv 2/3/4/5/8", adv=(2, 3, 4, 5, 8)),
        cfg("adv 2/2/3/4/6 (very flat)", adv=(2, 2, 3, 4, 6)),
        cfg("adv 3/6/10/16/25 (steep)", adv=(3, 6, 10, 16, 25)),
        # 2. tier-weighted advancement
        cfg("tierW base 3/5/7/10/15", adv=(3, 5, 7, 10, 15), tier_weighted=True),
        cfg("tierW low 1/2/3/4/6", adv=(1, 2, 3, 4, 6), tier_weighted=True),
        cfg("tierW flat 1/1/2/3/4", adv=(1, 1, 2, 3, 4), tier_weighted=True),
        # 3. upset magnitude
        cfg("upset flat +3", upset_value=3),
        cfg("upset flat +4", upset_value=4),
        cfg("upset gap x2 (2/4/6)", upset_mode="gap", upset_value=2),
        cfg("upset gap x3 (3/6/9)", upset_mode="gap", upset_value=3),
        # 4. group win points
        cfg("group win 4", group_win=4),
        cfg("group win 5", group_win=5),
    ]


def phase_b():
    # Phase A showed STEEPER advancement (more top-end variance) is the only lever
    # that compresses win% parity; flatten/group-win/tier-weight all worsen it.
    # Phase B explores the steepness frontier + interactions with upset levers.
    return [
        cfg("B1 steeper 3/6/12/22/36", adv=(3, 6, 12, 22, 36)),
        cfg("B2 steep 2/5/10/18/30", adv=(2, 5, 10, 18, 30)),
        cfg("B3 steep 4/8/14/22/34", adv=(4, 8, 14, 22, 34)),
        cfg("B4 v.steep 3/7/14/26/44", adv=(3, 7, 14, 26, 44)),
        cfg("B5 mod-steep 3/5/9/14/22", adv=(3, 5, 9, 14, 22)),
        cfg("B6 steep25 + upset+3", adv=(3, 6, 10, 16, 25), upset_value=3),
        cfg("B7 steep25 + gap x2", adv=(3, 6, 10, 16, 25), upset_mode="gap", upset_value=2),
        cfg("B8 steep25 + gw4", group_win=4, adv=(3, 6, 10, 16, 25)),
        cfg("B9 steeper36 + upset+3", adv=(3, 6, 12, 22, 36), upset_value=3),
        cfg("B10 x.steep 4/9/18/32/52", adv=(4, 9, 18, 32, 52)),
    ]


# --------------------------------------------------------------------------- #
def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else "all"
    n_sims = int(sys.argv[2]) if len(sys.argv) > 2 else 20000
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 20260603

    aliases = data_io.load_aliases()
    drafted = data_io.load_drafted_teams(aliases)
    tiers = data_io.load_full_tiers(aliases)
    ctx = load_context()
    roster = {r["team"]: {"owner": r["owner"], "tier": r["tier"]} for r in drafted}
    owners = sorted({r["owner"] for r in drafted})

    miss = sorted(t for t in ctx.ratings if t not in tiers)
    if miss:
        raise SystemExit(f"Teams missing a tier: {miss}")

    print(f"Recording {n_sims:,} tournaments (seed {seed})...", flush=True)
    arr = record(ctx, roster, tiers, owners, n_sims, seed)
    arrays = arr[:5]

    configs = {"A": phase_a(), "B": phase_b(), "all": phase_a() + phase_b()}[phase]
    results = []
    for c in configs:
        r = score_config(c, arrays, owners)
        results.append((c, r))
    results.sort(key=lambda cr: cr[1]["spread"])

    # ---- table ----
    print()
    hdr = f"{'config':36s} " + " ".join(f"{o:>7s}" for o in owners) + f" {'spread':>7s} {'meanTot':>8s} {'grp%':>6s}"
    print(hdr)
    print("-" * len(hdr))
    rows = []
    for c, r in results:
        wp = r["winpct"]
        line = (f"{c['name']:36s} " + " ".join(f"{wp[i]*100:6.1f}%" for i in range(len(owners)))
                + f" {r['spread']*100:6.1f}% {r['overall_mean']:8.1f} {r['group_share']*100:5.1f}%")
        print(line)
        row = {"config": c["name"], "spread_pct": round(r["spread"] * 100, 2),
               "mean_total": round(r["overall_mean"], 2),
               "group_share_pct": round(r["group_share"] * 100, 2),
               "group_win": c["group_win"], "upset_mode": c["upset_mode"],
               "upset_value": c["upset_value"], "adv": "/".join(map(str, c["adv"])),
               "tier_weighted": c["tier_weighted"]}
        for i, o in enumerate(owners):
            row[f"{o}_winpct"] = round(wp[i] * 100, 2)
        rows.append(row)

    with open("outputs/sweep_results.csv", "w", newline="", encoding="utf-8") as f:
        fields = (["config"] + [f"{o}_winpct" for o in owners] +
                  ["spread_pct", "mean_total", "group_share_pct", "group_win",
                   "upset_mode", "upset_value", "adv", "tier_weighted"])
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(rows)
    print(f"\nwrote outputs/sweep_results.csv  ({len(rows)} configs, phase {phase})")


if __name__ == "__main__":
    main()
