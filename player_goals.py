#!/usr/bin/env python3
"""Aggregate goal events (data/goals.json) into player-goal leaders for the site.

Reads goal events produced by `fetch_results.py --with-goals`, maps each scorer's
team through the aliases to a drafted owner, and writes a leaderboard of scorers
on drafted teams. Undrafted-team goals are ignored (no owner to credit).

Usage:
    python player_goals.py --goals data/goals.json --out site/data/player_goals.json
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--goals", default=os.path.join(DATA, "goals.json"))
    ap.add_argument("--out", default=os.path.join(HERE, "site", "data", "player_goals.json"))
    args = ap.parse_args()

    draft = load(os.path.join(DATA, "draft_board.json"))["owners"]
    aliases = load(os.path.join(DATA, "team_aliases.json"))["aliases"]
    owner_of = {t: o for o, teams in draft.items() for t in teams}

    def canon(n):
        return aliases.get((n or "").strip(), (n or "").strip())

    try:
        goals_doc = load(args.goals)
    except FileNotFoundError:
        goals_doc = {"goals": [], "source": "none"}
    goals = goals_doc.get("goals", [])

    # (player, team) -> {goals, owner, penalties, own_goals}
    tally = defaultdict(lambda: {"goals": 0, "penalties": 0})
    for g in goals:
        team = canon(g.get("team"))
        owner = owner_of.get(team)
        if owner is None:
            continue  # scorer's team isn't drafted -> nobody to track
        detail = (g.get("detail") or "").lower()
        if "own goal" in detail:
            continue  # own goals don't credit the scorer
        key = (g["player"], team)
        tally[key]["goals"] += 1
        tally[key]["owner"] = owner
        if "penalty" in detail:
            tally[key]["penalties"] += 1

    leaders = [
        {"player": p, "team": t, "owner": v["owner"],
         "goals": v["goals"], "penalties": v["penalties"]}
        for (p, t), v in tally.items()
    ]
    leaders.sort(key=lambda r: (-r["goals"], r["player"]))
    for i, r in enumerate(leaders, 1):
        r["rank"] = i

    out = {
        "source": goals_doc.get("source", "unknown"),
        "note": "Goals by players on drafted teams only. Penalties counted in the total.",
        "leaders": leaders,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"Wrote {len(leaders)} scorers -> {args.out}")


if __name__ == "__main__":
    main()
