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
import tempfile
import time
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
BASE = "https://v3.football.api-sports.io"
KEY = os.environ.get("API_FOOTBALL_KEY", "fa4a83828c1f8b553acba91e321faabb")
WORLD_CUP_LEAGUE_ID = 1

# Count every HTTP request we make this run so the budget logger (end of main)
# can report usage and keep a cumulative daily tally against the free-tier cap.
REQUEST_COUNT = 0


def api_get(path, params, retries=2):
    """GET with simple 429 backoff (free tier = 10 req/min)."""
    global REQUEST_COUNT
    qs = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    url = f"{BASE}{path}?{qs}"
    req = urllib.request.Request(url, headers={"x-apisports-key": KEY})
    for attempt in range(retries + 1):
        try:
            REQUEST_COUNT += 1  # count each attempt; a 429 retry still hit the API
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

    source = f"api-football league {args.league} season {args.season}"

    # Incremental goals: only fetch goal events for fixtures we have NOT already
    # processed. Load the prior goals.json and reuse it ONLY if it was produced for
    # this same league+season (otherwise it's stale — e.g. the committed 2022 proxy
    # — and must be rebuilt from scratch). `processed` tracks every fixture we've
    # already pulled events for, INCLUDING 0-0 finals that yield no goal rows, so a
    # scoreless match isn't re-queried every night. This keeps the per-run event
    # call count proportional to *new* finals, not total finals.
    existing_goals, processed = [], set()
    goals_path = os.path.join(DATA, "goals.json")
    if args.with_goals:
        try:
            prev = json.load(open(goals_path, encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            prev = None
        if prev and prev.get("source") == source:
            existing_goals = prev.get("goals", [])
            processed = set(prev.get("fixtures_with_events", []))
            processed |= {g["fixture_id"] for g in existing_goals}
            print(f"Incremental goals: {len(existing_goals)} events already stored "
                  f"across {len(processed)} processed fixtures (reusing).")
        elif prev is not None:
            print("goals.json is from a different league/season — rebuilding goal events.")

    data = api_get("/fixtures", {"league": args.league, "season": args.season})
    fixtures = data.get("response", [])
    print(f"API returned {len(fixtures)} fixtures for league {args.league} season {args.season}")

    matches, new_goals = [], []
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

        if (args.with_goals and fx["id"] not in processed
                and (args.max_goal_fixtures == 0
                     or goal_fixtures_done < args.max_goal_fixtures)):
            goal_fixtures_done += 1
            processed.add(fx["id"])  # mark fetched even if it yields no goals (0-0)
            ev = api_get("/fixtures/events", {"fixture": fx["id"]})
            for e in ev.get("response", []):
                if e.get("type") == "Goal":
                    new_goals.append({
                        "fixture_id": fx["id"], "date": rec["date"],
                        "team": canon(e["team"]["name"]),
                        "player": e["player"]["name"],
                        "minute": e["time"]["elapsed"],
                        "detail": e.get("detail"),
                    })
            time.sleep(7)  # free tier = 10 req/min; stay safely under it

    out = {
        "source": source,
        "fetched_finished": len(matches),
        "matches": matches,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    print(f"Wrote {len(matches)} finished matches -> {args.out}")

    if args.with_goals:
        all_goals = existing_goals + new_goals
        with open(goals_path, "w", encoding="utf-8") as fh:
            json.dump({
                "source": source,
                "fixtures_with_events": sorted(processed),
                "goals": all_goals,
            }, fh, indent=2, ensure_ascii=False)
        print(f"Wrote {len(all_goals)} goal events "
              f"({len(new_goals)} new from {goal_fixtures_done} fixtures) -> {goals_path}")

    log_api_budget()


def log_api_budget():
    """Report this run's API-Football usage and keep a cumulative daily tally.

    The free tier is request-capped, and the pipeline now runs several times a day.
    We persist a small {date, count} file in the temp dir so usage accumulates
    across every fetch_results.py invocation in a UTC day (it resets when the date
    rolls over) and warn loudly once the day's total clears 80 requests.
    """
    print(f"API-Football requests this run: {REQUEST_COUNT}")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tally_path = os.path.join(tempfile.gettempdir(), "api_football_daily_count.txt")
    daily_total = REQUEST_COUNT
    try:
        prev = json.load(open(tally_path, encoding="utf-8"))
        if prev.get("date") == today:
            daily_total += int(prev.get("count", 0))
    except (FileNotFoundError, json.JSONDecodeError, ValueError, TypeError):
        pass
    try:
        with open(tally_path, "w", encoding="utf-8") as fh:
            json.dump({"date": today, "count": daily_total}, fh)
    except OSError as e:
        print(f"  (could not persist daily API tally: {e})")
    print(f"API-Football cumulative requests today ({today} UTC): {daily_total}")
    if daily_total > 80:
        print(f"WARNING: API-Football daily total ({daily_total}) exceeds 80 — "
              "approaching free-tier limits; throttle scheduled runs if this persists.")


if __name__ == "__main__":
    main()
