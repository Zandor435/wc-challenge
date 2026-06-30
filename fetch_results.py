#!/usr/bin/env python3
"""Fetch World Cup fixtures and emit results JSON for scoring.py.

Two interchangeable sources, selected with --source:

  worldcup26ir  (DEFAULT) — the free https://worldcup26.ir/get/games feed. No auth,
                no API key, returns the live 2026 World Cup. This is what the live
                pipeline uses.
  api-football  — the original paid v3.football.api-sports.io path. Kept intact as a
                fallback; reads the key from API_FOOTBALL_KEY (falls back to the
                challenge key).

Both paths map their source through data/team_aliases.json and write the SAME
normalized schema scoring.py expects:

    {"source", "fetched_finished", "matches": [
        {"fixture_id", "date" (YYYY-MM-DD), "stage" ("group"|"knockout"),
         "home", "away", "home_score" (int), "away_score" (int),
         "round"? ("R32".."FINAL"), "pen_home"?, "pen_away"?, "decided_by"?}
    ]}

Only finished matches are emitted (they're the ones that score points).

Penalty shootouts: the free worldcup26ir feed exposes no shootout aggregate, so a
knockout that ends level in regulation has no advancing side. resolve_level_knockouts
fills the winner in priority order — data/knockout_overrides.json first, then an
optional api-football fallback (--ko-fallback, default auto) — and DROPS anything still
unresolved, so scoring never fabricates a winner. The api-football source already
carries score.penalty and needs no such handling.

Usage:
    # 2026 World Cup from the free feed (default source):
    python fetch_results.py --out data/live_results.json
    # explicit:
    python fetch_results.py --source worldcup26ir --out data/live_results.json
    # original API-Football path (e.g. the 2022 proxy):
    python fetch_results.py --source api-football --season 2022 --out data/proxy_2022_results.json

Then:
    python scoring.py --results data/live_results.json --out-dir site/data

Goal-scorer note: --with-goals attaches scorers to data/goals.json (schema:
{fixture_id, date, team, player, minute, detail}) for the Golden Boot pipeline.
  - api-football pulls goal events from /fixtures/events?fixture=ID (1 req/fixture).
  - worldcup26ir parses the inline home_scorers/away_scorers strings (free, no extra
    request) — these are messy mixed-quote strings, so parsing is best-effort.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import ssl
import tempfile
import time
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OVERRIDES_PATH = os.path.join(DATA, "knockout_overrides.json")
BASE = "https://v3.football.api-sports.io"
# `or` (not the dict default): an env var present-but-empty — e.g. a workflow that
# wires `API_FOOTBALL_KEY: ${{ secrets.X }}` when the secret is unset — must still
# fall back to the challenge key rather than auth with "".
KEY = os.environ.get("API_FOOTBALL_KEY") or "fa4a83828c1f8b553acba91e321faabb"
WORLD_CUP_LEAGUE_ID = 1
WC26_URL = "https://worldcup26.ir/get/games"

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


# ---------------------------------------------------------------------------
# worldcup26.ir source
# ---------------------------------------------------------------------------

# worldcup26.ir's `type` field is already a clean stage code, so we map it
# directly instead of string-sniffing a human round label.
WC26_TYPE_MAP = {
    "group": ("group", None),
    "r32": ("knockout", "R32"),
    "r16": ("knockout", "R16"),
    "qf": ("knockout", "QF"),
    "sf": ("knockout", "SF"),
    "third": ("knockout", "3RD"),
    "final": ("knockout", "FINAL"),
}

# Scorer strings mix straight and curly quotes, e.g.
#   {"I.B. Hwang 67'","H.G. Oh 80'"}   and   {“J. Quiñones 9'”,”R. Jiménez 67'”}
# Pull each quoted token regardless of which quote style wraps it.
_SCORER_TOKEN = re.compile(r"[\"“”„‟]([^\"“”„‟]+)[\"“”„‟]")
_MINUTE = re.compile(r"(\d+(?:\+\d+)?)\s*'")
_GOAL_MARKER = re.compile(r"\s*\((?:p|pen|pk|og)\)\s*$", re.I)


def http_get_json(url, timeout=30, retries=3, base_delay=5):
    """Plain GET -> parsed JSON. No auth (worldcup26.ir needs none).

    worldcup26.ir intermittently drops the connection mid-fetch (SSL EOF, socket
    reset, DNS timeout). A single failure used to crash the whole cron run, so
    retry with exponential backoff (5s, 10s, 20s by default). This is a separate
    code path from api_get's 429 backoff — different failure mode (transient
    network vs. rate limit), so it gets its own loop rather than sharing one.

    On a *persistent* outage we deliberately re-raise the original exception
    after the last attempt: the job should fail loud rather than silently score
    stale elimination data.
    """
    req = urllib.request.Request(url, headers={"User-Agent": "wc-challenge-fetch/1.0"})
    delay = base_delay
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except (ssl.SSLError, urllib.error.URLError, socket.error,
                ConnectionResetError, TimeoutError) as e:
            # socket.error is an alias for OSError, which also covers
            # ConnectionResetError/TimeoutError — listed explicitly for clarity.
            if attempt >= retries:
                print(f"  {url}: network error on attempt {attempt}/{retries} "
                      f"({e!r}); all retries exhausted, giving up")
                raise
            print(f"  {url}: network error on attempt {attempt}/{retries} "
                  f"({e!r}); retrying in {delay}s…")
            time.sleep(delay)
            delay *= 2


def wc26_date(local_date: str) -> str:
    """'MM/DD/YYYY HH:MM' -> 'YYYY-MM-DD'. Returns '' if unparseable."""
    head = (local_date or "").strip().split(" ")[0]
    try:
        mm, dd, yyyy = head.split("/")
        return f"{int(yyyy):04d}-{int(mm):02d}-{int(dd):02d}"
    except (ValueError, AttributeError):
        return ""


def parse_scorers(raw, team, fixture_id, date):
    """Best-effort parse of a worldcup26.ir scorers blob into goal events.

    `raw` looks like {"Name 12'","Name 45+2'"} (mixed quote styles) or the literal
    string "null" when there were no goals. We extract player + minute and tag a
    coarse detail (Penalty / Own Goal / Normal Goal) for the Golden Boot pipeline.
    Anything we can't parse is skipped silently — goals are continue-on-error.
    """
    out = []
    s = (raw or "").strip()
    if not s or s.strip("\"'{}").lower() == "null":
        return out
    for tok in _SCORER_TOKEN.findall(s):
        tok = tok.strip()
        if not tok or tok.lower() == "null":
            continue
        low = tok.lower()
        if "(og)" in low or "own goal" in low:
            detail = "Own Goal"
        elif "(p)" in low or "(pen" in low or "(pk)" in low or "penalty" in low:
            detail = "Penalty"
        else:
            detail = "Normal Goal"
        minute = None
        m = _MINUTE.search(tok)
        if m:
            name = tok[:m.start()].strip()
            minute = int(m.group(1).split("+")[0])
        else:
            name = tok
        name = _GOAL_MARKER.sub("", name).strip().rstrip(",;").strip()
        if not name:
            continue
        out.append({"fixture_id": fixture_id, "date": date, "team": team,
                    "player": name, "minute": minute, "detail": detail})
    return out


# ---------------------------------------------------------------------------
# Penalty-shootout resolution for the free feed
# ---------------------------------------------------------------------------
# The worldcup26.ir feed reports a knockout that ended level in regulation as a
# draw with no shootout aggregate, so it carries no advancing side. We resolve
# those in priority order — manual overrides, then an optional api-football
# fallback — and DROP whatever is left so neither scoring.py nor sim/engine.py
# fabricates a winner from the away team (their `pen_home - pen_away` defaults to
# 0, which silently picks `away`).

def _pair_key(a, b):
    """Order-independent key for a knockout fixture. A given pair meets at most
    once in a single-elimination bracket, so the team pair alone is unambiguous
    (no date needed — which also sidesteps feed/API date drift)."""
    return frozenset((a, b))


def load_ko_overrides(path, canon):
    """Read data/knockout_overrides.json -> {pair_key: {winner, winner_pens,
    loser_pens}}. Missing/empty/malformed file -> {} (overrides are optional)."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        if isinstance(e, json.JSONDecodeError):
            print(f"  knockout overrides {path} is not valid JSON ({e}); ignoring")
        return {}
    out = {}
    for key, v in (raw.get("overrides") or {}).items():
        parts = [p.strip() for p in key.split("|")]
        if len(parts) != 3:
            print(f"  skipping malformed override key {key!r} (want 'DATE|TeamA|TeamB')")
            continue
        a, b = canon(parts[1]), canon(parts[2])
        winner = canon((v.get("winner") or "").strip())
        if winner not in (a, b):
            print(f"  skipping override {key!r}: winner {v.get('winner')!r} "
                  "is not one of the two teams")
            continue
        out[_pair_key(a, b)] = {
            "winner": winner,
            "winner_pens": v.get("winner_pens"),
            "loser_pens": v.get("loser_pens"),
        }
    return out


def _apply_ko_result(rec, winner, winner_pens=None, loser_pens=None):
    """Mark a level-knockout record as decided on penalties for `winner`. Sets an
    explicit `winner` (so scoring/display never has to guess) and `decided_by`.
    The actual shootout score is recorded ONLY when known — oriented to the
    record's home/away — so we never display a fabricated scoreline; winner-only
    overrides resolve the result without inventing a number."""
    rec["winner"] = winner
    rec["decided_by"] = "penalties"
    if winner_pens is not None and loser_pens is not None:
        if winner == rec["home"]:
            rec["pen_home"], rec["pen_away"] = winner_pens, loser_pens
        else:
            rec["pen_home"], rec["pen_away"] = loser_pens, winner_pens


def _af_penalty_lookup(season, league, canon):
    """One api-football /fixtures call -> {pair_key: (winner, winner_pens,
    loser_pens)} for every fixture decided on penalties. Returns {} on any
    failure — the fallback must never take the whole fetch down."""
    try:
        data = api_get("/fixtures", {"league": league, "season": season})
    except Exception as e:   # noqa: BLE001 - any API/network error -> graceful skip
        print(f"  api-football penalty fallback unavailable ({e!r}); leaving unresolved")
        return {}
    out = {}
    for f in data.get("response", []):
        fx, teams = f["fixture"], f["teams"]
        if fx.get("status", {}).get("short") != "PEN":
            continue
        h, a = canon(teams["home"]["name"]), canon(teams["away"]["name"])
        pen = f.get("score", {}).get("penalty", {}) or {}
        ph, pa = pen.get("home") or 0, pen.get("away") or 0
        winner, wp, lp = (h, ph, pa) if ph >= pa else (a, pa, ph)
        out[_pair_key(h, a)] = (winner, wp, lp)
    return out


def resolve_level_knockouts(matches, overrides, args, canon):
    """Resolve (or drop) every finished knockout that ended level with no shootout
    aggregate. Overrides first, then the api-football fallback (unless --ko-fallback
    off); anything still unresolved is dropped with a warning. Returns the surviving
    match list."""
    def needs_winner(m):
        return (m.get("stage") == "knockout"
                and m["home_score"] == m["away_score"]
                and "winner" not in m and "pen_home" not in m)

    level = [m for m in matches if needs_winner(m)]
    if not level:
        return matches
    print(f"{len(level)} level knockout(s) need a shootout result.")

    for m in level:
        ov = overrides.get(_pair_key(m["home"], m["away"]))
        if ov:
            _apply_ko_result(m, ov["winner"], ov["winner_pens"], ov["loser_pens"])
            print(f"  resolved {m['home']} v {m['away']} via override -> "
                  f"{ov['winner']} on penalties")

    unresolved = [m for m in level if needs_winner(m)]
    if unresolved and args.ko_fallback != "off":
        lookup = _af_penalty_lookup(args.season, args.league, canon)
        for m in unresolved:
            hit = lookup.get(_pair_key(m["home"], m["away"]))
            if hit:
                _apply_ko_result(m, *hit)
                print(f"  resolved {m['home']} v {m['away']} via api-football -> "
                      f"{hit[0]} on penalties")

    still = [m for m in level if needs_winner(m)]
    if still:
        drop = {id(m) for m in still}
        for m in still:
            print(f"  WARNING: dropping unresolved knockout {m['home']} v {m['away']} "
                  f"({m['date']}) — level score, no shootout result. Add an entry to "
                  "data/knockout_overrides.json (or enable the api-football fallback).")
        matches = [m for m in matches if id(m) not in drop]
    return matches


def run_worldcup26ir(args, canon):
    """Fetch the free worldcup26.ir feed and write live_results.json (+ goals)."""
    source = "worldcup26.ir live games"
    raw = http_get_json(WC26_URL)
    games = raw.get("games", [])
    print(f"worldcup26.ir returned {len(games)} games")

    matches, goals = [], []
    for game in games:
        # Gate strictly on finished == "TRUE". Unplayed matches also report
        # "0" - "0", so the score alone can't distinguish a real 0-0 from a
        # fixture that hasn't kicked off — only `finished` can.
        if str(game.get("finished", "")).strip().upper() != "TRUE":
            continue
        stage, rnd = WC26_TYPE_MAP.get((game.get("type") or "").strip().lower(),
                                       ("group", None))
        date = wc26_date(game.get("local_date"))
        try:
            fixture_id = int(game.get("id"))
        except (TypeError, ValueError):
            fixture_id = game.get("id")
        home = canon((game.get("home_team_name_en") or "").strip())
        away = canon((game.get("away_team_name_en") or "").strip())
        try:
            hs = int(game.get("home_score") or 0)
            as_ = int(game.get("away_score") or 0)
        except (TypeError, ValueError):
            print(f"  skipping {home} v {away}: non-numeric score "
                  f"{game.get('home_score')!r}-{game.get('away_score')!r}")
            continue
        rec = {
            "fixture_id": fixture_id,
            "date": date,
            "stage": stage,
            "home": home,
            "away": away,
            "home_score": hs,
            "away_score": as_,
        }
        if rnd:
            rec["round"] = rnd
        # This feed exposes no penalty-shootout aggregate, so a knockout that ends
        # level arrives with no advancing side. We append it as-is here and resolve
        # (or drop) it below in resolve_level_knockouts — overrides, then the
        # api-football fallback — so scoring never has to guess a winner.
        matches.append(rec)

        if args.with_goals:
            goals += parse_scorers(game.get("home_scorers"), home, fixture_id, date)
            goals += parse_scorers(game.get("away_scorers"), away, fixture_id, date)

    matches.sort(key=lambda m: (m["date"],
                                m["fixture_id"] if isinstance(m["fixture_id"], int) else 0))

    # Resolve level knockouts (penalty shootouts the free feed can't express):
    # overrides first, then the optional api-football fallback, drop the rest.
    overrides = load_ko_overrides(args.overrides, canon)
    matches = resolve_level_knockouts(matches, overrides, args, canon)

    out = {"source": source, "fetched_finished": len(matches), "matches": matches}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    print(f"Wrote {len(matches)} finished matches -> {args.out}")

    if args.with_goals:
        # Overwrite-by-default: scorers are inline in the same response, so we
        # rebuild goals.json fully each run (no incremental request budget to
        # protect, unlike the api-football path).
        goals_path = os.path.join(DATA, "goals.json")
        fixtures_with_events = sorted({m["fixture_id"] for m in matches
                                       if isinstance(m["fixture_id"], int)})
        with open(goals_path, "w", encoding="utf-8") as fh:
            json.dump({"source": source,
                       "fixtures_with_events": fixtures_with_events,
                       "goals": goals}, fh, indent=2, ensure_ascii=False)
        print(f"Wrote {len(goals)} goal events (parsed inline) -> {goals_path}")


# ---------------------------------------------------------------------------
# api-football source (original path, kept intact as a fallback)
# ---------------------------------------------------------------------------

def run_api_football(args, canon):
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["worldcup26ir", "api-football"],
                    default="worldcup26ir",
                    help="results provider (default: worldcup26ir, the free live feed)")
    ap.add_argument("--season", type=int, default=2026,
                    help="api-football only; ignored by worldcup26ir")
    ap.add_argument("--league", type=int, default=WORLD_CUP_LEAGUE_ID,
                    help="api-football only")
    ap.add_argument("--out", default=os.path.join(DATA, "live_results.json"))
    ap.add_argument("--with-goals", action="store_true",
                    help="also collect goal scorers -> data/goals.json")
    ap.add_argument("--overrides", default=OVERRIDES_PATH,
                    help="manual knockout results JSON for penalty shootouts the free "
                         "feed can't resolve (default: data/knockout_overrides.json)")
    ap.add_argument("--ko-fallback", choices=["auto", "off"], default="auto",
                    help="when a level knockout has no override, query api-football for "
                         "the shootout result (auto) or skip the fallback (off)")
    ap.add_argument("--max-goal-fixtures", type=int, default=0,
                    help="api-football only: cap fixtures to pull goal events for "
                         "(0 = no cap). Protects the free-tier quota.")
    args = ap.parse_args()

    aliases = json.load(open(os.path.join(DATA, "team_aliases.json"), encoding="utf-8"))["aliases"]

    def canon(n):
        return aliases.get((n or "").strip(), (n or "").strip())

    if args.source == "worldcup26ir":
        run_worldcup26ir(args, canon)
    else:
        run_api_football(args, canon)


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
