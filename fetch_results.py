#!/usr/bin/env python3
"""Fetch World Cup fixtures from API-Football and emit results JSON for scoring.py.

Reads the API key from the API_FOOTBALL_KEY env var (falls back to the challenge
key). Pulls finished fixtures for a league+season, maps API team names through
data/team_aliases.json, classifies each fixture's stage/round, and writes a
results file in the schema scoring.py expects.

Usage:
    # 2026 World Cup (once API-Football populates it):
    python fetch_results.py --season 2026 --out data/live_results.json
    # 2022 proxy (data exists today), only finished matches:
    python fetch_results.py --season 2022 --out data/proxy_2022_results.json

Then:
    python scoring.py --results data/live_results.json --out-dir site/data

Goal-scorer note: API-Football exposes goal events at /fixtures/events?fixture=ID
(player name, team, minute, detail). --with-goals attaches them so a later
player-tracking feature can read data/goals.json.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
BASE = "https://v3.football.api-sports.io"
KEY = os.environ.get("API_FOOTBALL_KEY", "fa4a83828c1f8b553acba91e321faabb")
WORLD_CUP_LEAGUE_ID = 1


def api_get(path, params, retries=2):
    """GET with simple 429 backoff (free tier = 10 req/min)."""
    qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    url = f"{BASE}{path}?{qs}"
    req = urllib.request.Request(url, headers={"x-apisports-key": KEY})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                print("  rate-limited (429); waiting 62s for the per-minute window…")
                time.sleep(62)
                continue
            raise


def classify_round(api_round: str):
    """Map API-Football's league.round string to (stage, round_code)."""
    r = (api_round or "").lower()
    if "group" in r:
        return "group", None
    if "round of 32" in r:
        return "knockout", "R32"
    if "round of 16" in r:
        return "knockout", "R16"
    if "quarter" in r:
        return "knockout", "QF"
    if "semi" in r:
        return "knockout", "SF"
    if "3rd place" in r or "third place" in r:
        return "knockout", "3RD"
    if "final" in r:
        return "knockout", "FINAL"
    return "group", None  # default safe


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--league", type=int, default=WORLD_CUP_LEAGUE_ID)
    ap.add_argument("--out", default=os.path.join(DATA, "live_results.json"))
    ap.add_argument("--with-goals", action="store_true",
                    help="also fetch goal-scorer events -> data/goals.json (uses 1 req/fixture)")
    ap.add_argument("--max-goal-fixtures", type=int, default=0,
                    help="cap how many fixtures to pull goal events for (0 = no cap). Protects the free-tier quota.")
    args = ap.parse_args()

    aliases = json.load(open(os.path.join(DATA, "team_aliases.json"), encoding="utf-8"))["aliases"]

    def canon(n):
        return aliases.get((n or "").strip(), (n or "").strip())

    data = api_get("/fixtures", {"league": args.league, "season": args.season})
    fixtures = data.get("response", [])
    print(f"API returned {len(fixtures)} fixtures for league {args.league} season {args.season}")

    matches, goals = [], []
    goal_fixtures_done = 0
    for f in fixtures:
        fx, teams, g = f["fixture"], f["teams"], f["goals"]
        if fx["status"]["short"] not in ("FT", "AET", "PEN"):
            continue  # only finished matches generate points
        stage, rnd = classify_round(f["league"]["round"])
        rec = {
            "fixture_id": fx["id"],
            "date": fx["date"][:10],
            "stage": stage,
            "home": canon(teams["home"]["name"]),
            "away": canon(teams["away"]["name"]),
            "home_score": g["home"], "away_score": g["away"],
        }
        if rnd:
            rec["round"] = rnd
        if fx["status"]["short"] == "PEN":
            # API-Football puts the shootout aggregate in score.penalty
            pen = f.get("score", {}).get("penalty", {})
            rec["pen_home"] = pen.get("home") or 0
            rec["pen_away"] = pen.get("away") or 0
            rec["decided_by"] = "penalties"
        matches.append(rec)

        if args.with_goals and (args.max_goal_fixtures == 0
                                or goal_fixtures_done < args.max_goal_fixtures):
            goal_fixtures_done += 1
            ev = api_get("/fixtures/events", {"fixture": fx["id"]})
            for e in ev.get("response", []):
                if e.get("type") == "Goal":
                    goals.append({
                        "fixture_id": fx["id"], "date": rec["date"],
                        "team": canon(e["team"]["name"]),
                        "player": e["player"]["name"],
                        "minute": e["time"]["elapsed"],
                        "detail": e.get("detail"),
                    })
            time.sleep(7)  # free tier = 10 req/min; stay safely under it

    out = {
        "source": f"api-football league {args.league} season {args.season}",
        "fetched_finished": len(matches),
        "matches": matches,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    print(f"Wrote {len(matches)} finished matches -> {args.out}")

    if args.with_goals:
        gp = os.path.join(DATA, "goals.json")
        with open(gp, "w", encoding="utf-8") as fh:
            json.dump({"source": out["source"], "goals": goals}, fh,
                      indent=2, ensure_ascii=False)
        print(f"Wrote {len(goals)} goal events -> {gp}")


if __name__ == "__main__":
    main()
