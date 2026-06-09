#!/usr/bin/env python3
"""WC Challenge scoring engine (rules version: rebalanced_v3).

Takes real (or fake) match results and computes fantasy points per owner.
EVERYTHING is config-driven:
  - scoring rules   <- data/scoring_config.json
  - draft board     <- data/draft_board.json
  - 48-team tiers   <- data/tiers.json
  - name aliases    <- data/team_aliases.json
Nothing about the rules or the roster is hard-coded here.

Usage:
    python scoring.py --results data/fake_results.json --out-dir site/data [--explain]

Outputs (into --out-dir):
    owner_standings.json   leaderboard: owners ranked by total points, with breakdown
    daily_results.json     every match, the points it generated, and the math
    team_table.json        every drafted team: owner, tier, W/D/L, points

Scoring summary (read from config, shown here for reference):
  GROUP   win=3 draw=1 loss=0; upset bonus = 2 * (winner_tier - loser_tier) when a
          weaker (higher-number) tier beats a stronger one, paid only to a drafting owner.
  KNOCKOUT win=3 loss=0 (penalties = a win); advancement bonus for REACHING a round
          (R16=2, QF=5, SF=10, Final=18, WinWC=30); 3rd-place game = match points only.
  Points are only awarded when the relevant team is drafted by an owner.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

# Which advancement bonus a knockout WIN earns, keyed by the round just played.
# Winning a round-of-32 match means you REACHED the round of 16, etc.
WIN_ADVANCES_INTO = {
    "R32": "round_of_16",
    "R16": "quarterfinal",
    "QF": "semifinal",
    "SF": "final",
    "FINAL": "win_world_cup",
}
ROUND_LABELS = {
    "R32": "Round of 32", "R16": "Round of 16", "QF": "Quarterfinal",
    "SF": "Semifinal", "FINAL": "Final", "3RD": "Third-place game",
}


# --------------------------------------------------------------------------- IO
def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_inputs(data_dir=DATA):
    cfg = load_json(os.path.join(data_dir, "scoring_config.json"))
    draft = load_json(os.path.join(data_dir, "draft_board.json"))["owners"]
    tiers = load_json(os.path.join(data_dir, "tiers.json"))["tiers"]
    aliases = load_json(os.path.join(data_dir, "team_aliases.json"))["aliases"]
    return cfg, draft, tiers, aliases


def build_owner_of(draft):
    """canonical team name -> owner."""
    owner_of = {}
    for owner, teams in draft.items():
        for t in teams:
            owner_of[t] = owner
    return owner_of


def make_canon(aliases):
    def canon(name):
        if name is None:
            return None
        n = name.strip()
        return aliases.get(n, n)
    return canon


# ---------------------------------------------------------------- scoring core
def score_group_match(m, cfg, owner_of, tiers, canon):
    """Return (events, detail) for one group match.

    events: list of {owner, team, points, reason}
    detail: human-readable breakdown lines for verification.
    """
    gs = cfg["group_stage"]
    win, draw, loss = gs["win"], gs["draw"], gs["loss"]
    coeff = gs.get("upset_bonus_per_tier_gap", gs.get("upset_bonus", 0))
    mode = gs.get("upset_mode", "flat")

    home, away = canon(m["home"]), canon(m["away"])
    hs, as_ = m["home_score"], m["away_score"]
    events, detail = [], []

    def tier(t):
        return tiers.get(t)

    if hs == as_:  # draw
        for t in (home, away):
            o = owner_of.get(t)
            if o is not None:
                events.append({"owner": o, "team": t, "points": draw, "reason": "group draw"})
                detail.append(f"DRAW: {t} ({o}) +{draw} (draw)")
            else:
                detail.append(f"DRAW: {t} undrafted, no points")
        return events, detail

    winner, loser = (home, away) if hs > as_ else (away, home)
    wo, lo = owner_of.get(winner), owner_of.get(loser)
    wt, lt = tier(winner), tier(loser)

    # winner
    if wo is not None:
        pts = win
        line = f"WIN: {winner} ({wo}, T{wt}) +{win} (win)"
        # upset bonus: weaker tier (higher number) beats stronger (lower number)
        if wt is not None and lt is not None and wt > lt:
            gap = wt - lt
            bonus = coeff * gap if mode == "tier_gap" else coeff
            pts += bonus
            line += f"  +{bonus} UPSET (T{wt} beat T{lt}, gap {gap} x {coeff})"
            events.append({"owner": wo, "team": winner, "points": win, "reason": "group win"})
            events.append({"owner": wo, "team": winner, "points": bonus,
                           "reason": f"upset T{wt} over T{lt} (gap {gap})"})
        else:
            note = ""
            if wt is not None and lt is not None:
                note = " (no upset: " + ("equal tier" if wt == lt else "favorite won") + ")"
            line += note
            events.append({"owner": wo, "team": winner, "points": win, "reason": "group win"})
        detail.append(line)
    else:
        detail.append(f"WIN: {winner} (T{wt}) undrafted, no points")

    # loser
    if lo is not None:
        events.append({"owner": lo, "team": loser, "points": loss, "reason": "group loss"})
        detail.append(f"LOSS: {loser} ({lo}, T{lt}) +{loss} (loss)")
    else:
        detail.append(f"LOSS: {loser} (T{lt}) undrafted, no points")

    return events, detail


def score_knockout_match(m, cfg, owner_of, tiers, canon):
    ks = cfg["knockout_stage"]
    win = ks.get("win", cfg["group_stage"]["win"]) if ks.get("match_points_apply") else 0
    loss = ks.get("loss", 0)
    adv = ks["advancement_bonuses"]

    rnd = str(m.get("round", "")).upper()
    home, away = canon(m["home"]), canon(m["away"])
    hs, as_ = m["home_score"], m["away_score"]
    # knockout: a tie in regulation is decided by penalties; the input carries the
    # final advancing side via 'winner' or via penalty scores. We require a winner.
    if hs == as_:
        # expect explicit shootout result
        pw = m.get("pen_home", 0) - m.get("pen_away", 0)
        winner, loser = (home, away) if pw > 0 else (away, home)
        decided = "penalties"
    else:
        winner, loser = (home, away) if hs > as_ else (away, home)
        decided = m.get("decided_by", "regulation")

    wo, lo = owner_of.get(winner), owner_of.get(loser)
    events, detail = [], []

    if wo is not None:
        events.append({"owner": wo, "team": winner, "points": win,
                       "reason": f"{ROUND_LABELS.get(rnd, rnd)} win ({decided})"})
        line = f"KO WIN ({ROUND_LABELS.get(rnd, rnd)}): {winner} ({wo}) +{win}"
        # advancement bonus for reaching the next round (not for 3rd-place game)
        if rnd in WIN_ADVANCES_INTO:
            bonus = adv[WIN_ADVANCES_INTO[rnd]]
            if bonus:
                events.append({"owner": wo, "team": winner, "points": bonus,
                               "reason": f"reached {WIN_ADVANCES_INTO[rnd]}"})
                line += f"  +{bonus} advancement (reached {WIN_ADVANCES_INTO[rnd]})"
        elif rnd == "3RD":
            line += "  (3rd-place game: match points only, no advancement)"
        detail.append(line)
    else:
        detail.append(f"KO WIN ({ROUND_LABELS.get(rnd, rnd)}): {winner} undrafted, no points")

    if lo is not None:
        events.append({"owner": lo, "team": loser, "points": loss,
                       "reason": f"{ROUND_LABELS.get(rnd, rnd)} loss"})
        detail.append(f"KO LOSS: {loser} ({lo}) +{loss}")
    else:
        detail.append(f"KO LOSS: {loser} undrafted, no points")
    return events, detail


def run(results, cfg, draft, tiers, aliases):
    owner_of = build_owner_of(draft)
    canon = make_canon(aliases)
    owners = list(draft.keys())

    owner_totals = {o: 0 for o in owners}
    owner_breakdown = {o: defaultdict(float) for o in owners}  # reason-category -> pts
    # team table: W/D/L + points for every drafted team
    team_rows = {}
    for o, teams in draft.items():
        for t in teams:
            team_rows[t] = {"team": t, "owner": o, "tier": tiers.get(t),
                            "W": 0, "D": 0, "L": 0, "points": 0}

    daily = defaultdict(list)  # date -> list of match dicts

    for m in results["matches"]:
        stage = m.get("stage", "group").lower()
        if stage.startswith("group"):
            events, detail = score_group_match(m, cfg, owner_of, tiers, canon)
        else:
            events, detail = score_knockout_match(m, cfg, owner_of, tiers, canon)

        # tally owner points
        match_pts_by_owner = defaultdict(float)
        for e in events:
            owner_totals[e["owner"]] += e["points"]
            match_pts_by_owner[e["owner"]] += e["points"]
            # bucket: win/draw/loss/upset/advancement/etc.
            cat = "upset" if e["reason"].startswith("upset") else \
                  "advancement" if e["reason"].startswith("reached") else \
                  "match"
            owner_breakdown[e["owner"]][cat] += e["points"]

        # tally W/D/L + team points (drafted teams only)
        home, away = canon(m["home"]), canon(m["away"])
        hs, as_ = m["home_score"], m["away_score"]
        _tally_team(team_rows, events, home, away, hs, as_, m, canon)

        daily[m["date"]].append({
            "date": m["date"], "stage": stage, "round": m.get("round"),
            "home": home, "away": away,
            "home_score": hs, "away_score": as_,
            "score": f"{home} {hs}-{as_} {away}",
            "points": {o: match_pts_by_owner[o] for o in match_pts_by_owner},
            "math": detail,
        })

    # ---- assemble standings ----
    standings = []
    for o in owners:
        b = owner_breakdown[o]
        standings.append({
            "owner": o,
            "total_points": round(owner_totals[o], 2),
            "breakdown": {
                "match": round(b.get("match", 0), 2),
                "upset": round(b.get("upset", 0), 2),
                "advancement": round(b.get("advancement", 0), 2),
            },
        })
    standings.sort(key=lambda r: (-r["total_points"], r["owner"]))
    for i, r in enumerate(standings, 1):
        r["rank"] = i

    team_table = sorted(team_rows.values(),
                        key=lambda r: (r["owner"], r["tier"] or 9, -r["points"]))

    owner_standings = {
        "rules_version": cfg.get("version"),
        "tournament": cfg.get("meta", {}).get("tournament"),
        "source": results.get("source", "unknown"),
        "standings": standings,
    }
    daily_results = {
        "rules_version": cfg.get("version"),
        "source": results.get("source", "unknown"),
        "days": [{"date": d, "matches": daily[d]} for d in sorted(daily)],
    }
    team_table_out = {
        "rules_version": cfg.get("version"),
        "teams": team_table,
    }
    return owner_standings, daily_results, team_table_out


def _tally_team(team_rows, events, home, away, hs, as_, m, canon):
    """Record W/D/L and per-team points for drafted teams in this match."""
    if hs == as_ and m.get("stage", "group").lower().startswith("group"):
        outcomes = {home: "D", away: "D"}
    else:
        # determine winner/loser (knockouts decided by penalties handled upstream too)
        if hs == as_:
            pw = m.get("pen_home", 0) - m.get("pen_away", 0)
            w, l = (home, away) if pw > 0 else (away, home)
        else:
            w, l = (home, away) if hs > as_ else (away, home)
        outcomes = {w: "W", l: "L"}

    # points earned per team = sum of event points for that team
    pts_by_team = defaultdict(float)
    for e in events:
        pts_by_team[e["team"]] += e["points"]

    for team, res in outcomes.items():
        if team in team_rows:
            team_rows[team][res] += 1
            team_rows[team]["points"] += pts_by_team.get(team, 0)


# --------------------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(description="WC Challenge scoring engine")
    ap.add_argument("--results", required=True, help="path to results JSON")
    ap.add_argument("--out-dir", default=os.path.join(HERE, "site", "data"),
                    help="where to write owner_standings.json / daily_results.json / team_table.json")
    ap.add_argument("--explain", action="store_true", help="print per-match math to stdout")
    args = ap.parse_args()

    cfg, draft, tiers, aliases = load_inputs()
    results = load_json(args.results)
    owner_standings, daily_results, team_table = run(results, cfg, draft, tiers, aliases)

    os.makedirs(args.out_dir, exist_ok=True)
    for name, obj in (("owner_standings.json", owner_standings),
                      ("daily_results.json", daily_results),
                      ("team_table.json", team_table)):
        with open(os.path.join(args.out_dir, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)

    if args.explain:
        for day in daily_results["days"]:
            print(f"\n=== {day['date']} ===")
            for mt in day["matches"]:
                print(f"  {mt['score']}  [{mt['stage']}{'/' + mt['round'] if mt['round'] else ''}]")
                for line in mt["math"]:
                    print(f"      {line}")
                if mt["points"]:
                    pts = ", ".join(f"{o} +{p:g}" for o, p in mt["points"].items())
                    print(f"      => {pts}")
        print("\n=== LEADERBOARD ===")
        for r in owner_standings["standings"]:
            b = r["breakdown"]
            print(f"  {r['rank']}. {r['owner']:7} {r['total_points']:g} pts "
                  f"(match {b['match']:g}, upset {b['upset']:g}, adv {b['advancement']:g})")

    print(f"\nWrote owner_standings.json, daily_results.json, team_table.json -> {args.out_dir}")


if __name__ == "__main__":
    main()
