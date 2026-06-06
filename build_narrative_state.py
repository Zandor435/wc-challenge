#!/usr/bin/env python3
"""Build the WC Challenge rolling narrative state.

Pipeline position: runs AFTER scoring.py / win_probability.py / player_goals.py
and BEFORE generate_commentary.py. It reads every committed data file and distils
them into ONE structured context file -- site/data/narrative_state.json -- that
the (now stateful) commentary engine feeds to GPT so the tournament story can
build on itself: streaks, head-to-heads, notable events, and auto-tagged themes
that accumulate over the tournament instead of being regenerated from scratch.

This script is the ACCUMULATOR. It does not call any API and has no third-party
dependencies (stdlib only). It is safe to run repeatedly; the state is always a
fresh, deterministic projection of the current data files.

Inputs (read-only):
    site/data/owner_standings.json   current leaderboard + per-owner points
    site/data/daily_results.json     every match played, with the points it paid
    site/data/team_table.json        per-team W/D/L + points (drafted teams)
    site/data/timeline.json          win-probability snapshots (latest = current)
    site/data/player_goals.json      Golden Boot leaders (optional)
    data/draft_board.json            owner -> 6 teams
    data/tiers.json                  48-team tiers (for upset detection)
    data/scoring_config.json         rebalanced_v3 rules (upset bonus formula)
    data/matches.csv                 full fixture list (phase / matches remaining)

Output (the ONLY file this writes):
    site/data/narrative_state.json

Usage:
    python build_narrative_state.py
    python build_narrative_state.py --preseason          # force the Day-0 state
    python build_narrative_state.py --generated 2026-06-11T18:00:00Z
"""
from __future__ import annotations

import argparse
import csv
import json
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "site", "data")
ROOT_DATA = os.path.join(HERE, "data")
OUT = os.path.join(DATA, "narrative_state.json")

# matches.csv "phase" -> (readable round, sort order). Higher order = later round.
PHASE_ORDER = {
    "group": (1, "Group Stage"),
    "round_of_32": (2, "Round of 32"),
    "round_of_16": (3, "Round of 16"),
    "quarterfinal": (4, "Quarterfinal"),
    "semifinal": (5, "Semifinal"),
    "third_place": (6, "Third-place Game"),
    "final": (7, "Final"),
}
# daily_results carries knockout rounds as short codes in the "round" field.
ROUND_CODE_TO_PHASE = {
    "R32": "round_of_32", "R16": "round_of_16", "QF": "quarterfinal",
    "SF": "semifinal", "3RD": "third_place", "FINAL": "final",
}


# --------------------------------------------------------------------------- IO
def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def load_matches_csv(path):
    try:
        with open(path, encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def r2(x):
    """Round to 2dp but keep ints clean."""
    v = round(float(x), 2)
    return int(v) if v == int(v) else v


# --------------------------------------------------------------- match helpers
def match_phase_key(m):
    """Map a played match to a matches.csv-style phase key."""
    stage = str(m.get("stage", "group")).lower()
    if stage.startswith("group"):
        return "group"
    code = str(m.get("round", "")).upper()
    return ROUND_CODE_TO_PHASE.get(code, "round_of_32")


def winner_loser(m):
    """Return (winner_team, loser_team) or (None, None) for a draw."""
    hs, as_ = m["home_score"], m["away_score"]
    if hs == as_:
        pw = m.get("pen_home", 0) - m.get("pen_away", 0)
        if pw == 0:
            return None, None  # genuine draw (group stage)
        return (m["home"], m["away"]) if pw > 0 else (m["away"], m["home"])
    return (m["home"], m["away"]) if hs > as_ else (m["away"], m["home"])


def iter_matches(daily):
    """Yield (date, match) in chronological/file order."""
    for day in daily.get("days", []):
        for m in day["matches"]:
            yield day["date"], m


# ----------------------------------------------------------- per-owner records
def owner_records(team_table, owners):
    """Aggregate each owner's teams' W/D/L into one combined record."""
    rec = {o: {"W": 0, "D": 0, "L": 0} for o in owners}
    for t in team_table.get("teams", []):
        o = t["owner"]
        if o in rec:
            rec[o]["W"] += t.get("W", 0)
            rec[o]["D"] += t.get("D", 0)
            rec[o]["L"] += t.get("L", 0)
    return rec


def best_worst_teams(team_table, owners):
    """Best = most points; worst = fewest points among teams that have played."""
    by_owner = defaultdict(list)
    for t in team_table.get("teams", []):
        if t["owner"] in owners:
            by_owner[t["owner"]].append(t)

    best, worst = {}, {}
    for o in owners:
        teams = by_owner.get(o, [])
        played = [t for t in teams if (t.get("W", 0) + t.get("D", 0) + t.get("L", 0)) > 0]
        if not played:
            best[o] = worst[o] = None
            continue

        def card(t):
            return {"team": t["team"], "tier": t.get("tier"),
                    "points": r2(t.get("points", 0)),
                    "record": {"W": t.get("W", 0), "D": t.get("D", 0), "L": t.get("L", 0)}}

        best[o] = card(max(played, key=lambda t: (t.get("points", 0), t.get("W", 0))))
        worst[o] = card(min(played, key=lambda t: (t.get("points", 0), -t.get("L", 0))))
    return best, worst


def owner_streaks(daily, owner_of):
    """Trailing run of identical W/D/L outcomes across each owner's team-matches."""
    seq = defaultdict(list)  # owner -> [outcomes in chronological order]
    for _, m in iter_matches(daily):
        w, l = winner_loser(m)
        if w is None:  # draw
            for team in (m["home"], m["away"]):
                o = owner_of.get(team)
                if o:
                    seq[o].append("D")
        else:
            for team, outcome in ((w, "W"), (l, "L")):
                o = owner_of.get(team)
                if o:
                    seq[o].append(outcome)

    streaks = {}
    for o, outcomes in seq.items():
        if not outcomes:
            streaks[o] = {"type": None, "length": 0, "label": "-"}
            continue
        last = outcomes[-1]
        n = 0
        for x in reversed(outcomes):
            if x == last:
                n += 1
            else:
                break
        streaks[o] = {"type": last, "length": n, "label": f"{last}{n}"}
    return streaks


# ------------------------------------------------------------- head-to-head
def head_to_head(daily, owner_of):
    matrix = {}  # a -> b -> {W,D,L}  (a's record against b)
    diff = {}    # a -> b -> aggregate point swing (a's pts minus b's in their meetings)
    log = []

    def cell(a, b):
        matrix.setdefault(a, {}).setdefault(b, {"W": 0, "D": 0, "L": 0})
        return matrix[a][b]

    def bump_diff(a, b, delta):
        diff.setdefault(a, {}).setdefault(b, 0)
        diff[a][b] = r2(diff[a][b] + delta)

    for date, m in iter_matches(daily):
        ho, ao = owner_of.get(m["home"]), owner_of.get(m["away"])
        if not ho or not ao or ho == ao:
            continue  # need two different owners head-to-head
        # point swing: each owner's points FROM THIS MATCH (match + any upset bonus)
        pts = m.get("points", {})
        hp, ap = float(pts.get(ho, 0)), float(pts.get(ao, 0))
        bump_diff(ho, ao, hp - ap)
        bump_diff(ao, ho, ap - hp)

        w, l = winner_loser(m)
        if w is None:  # draw
            cell(ho, ao)["D"] += 1
            cell(ao, ho)["D"] += 1
            log.append({"result": "draw", "owners": sorted([ho, ao]),
                        "match": m["score"], "date": date})
        else:
            wo, lo = owner_of[w], owner_of[l]
            cell(wo, lo)["W"] += 1
            cell(lo, wo)["L"] += 1
            log.append({"result": "win", "winner": wo, "loser": lo,
                        "via": f"{w} beat {l}", "match": m["score"], "date": date})
    return matrix, diff, log


def matchday_point_history(history):
    """Per-matchday points earned by each owner (for streaky-vs-consistent reads).

    Derived from build_history's per-day 'points_today' so it stays consistent
    with the cumulative standings the rest of the state reports."""
    out = []
    for i, h in enumerate(history, 1):
        out.append({
            "matchday": i,
            "date": h["date"],
            "points": {o: r2(p) for o, p in h["points_today"].items()},
        })
    return out


def dependency_index(team_table, owners):
    """Share of each owner's points that come from their Tier-1 team.

    0..1; 0 when the owner has no points yet (or no Tier-1 team on record).
    High dependency = a fragile case leaning on one star."""
    by_owner = defaultdict(list)
    for t in team_table.get("teams", []):
        if t["owner"] in owners:
            by_owner[t["owner"]].append(t)
    dep = {}
    for o in owners:
        teams = by_owner.get(o, [])
        total = sum(float(t.get("points", 0)) for t in teams)
        t1 = next((t for t in teams if t.get("tier") == 1), None)
        t1_pts = float(t1.get("points", 0)) if t1 else 0.0
        dep[o] = r2(t1_pts / total) if total > 0 else 0
    return dep


# ------------------------------------------------------------- notable events
def notable_events(daily, owner_of, tiers, history, coeff, tier_gap_mode):
    events = []

    for date, m in iter_matches(daily):
        phase = match_phase_key(m)
        w, l = winner_loser(m)

        # upsets (group stage only -- that is where the upset bonus lives)
        if phase == "group" and w is not None:
            wo, wt, lt = owner_of.get(w), tiers.get(w), tiers.get(l)
            if wo and wt is not None and lt is not None and wt > lt:
                gap = wt - lt
                bonus = coeff * gap if tier_gap_mode else coeff
                events.append({
                    "type": "upset", "date": date, "owner": wo, "team": w,
                    "beat": l, "bonus": r2(bonus),
                    "description": f"{w} (T{wt}, {wo}) upset {l} (T{lt}) for +{r2(bonus)} bonus",
                })

        # eliminations (knockout losers are out)
        if phase != "group" and l is not None:
            lo = owner_of.get(l)
            round_label = PHASE_ORDER.get(phase, (0, phase))[1]
            events.append({
                "type": "elimination", "date": date, "team": l,
                "owner": lo, "round": round_label,
                "description": f"{l}" + (f" ({lo})" if lo else "")
                               + f" eliminated in the {round_label}",
            })

    # biggest single-day point swing (most points an owner banked in one day)
    best = None
    for h in history:
        for o, pts in h["points_today"].items():
            if pts > 0 and (best is None or pts > best["points"]):
                best = {"type": "biggest_day", "date": h["date"], "owner": o,
                        "points": r2(pts),
                        "description": f"{o} banked {r2(pts)} pts on {h['date']} "
                                       f"-- the biggest single-day haul so far"}
    if best:
        events.append(best)

    return events


# ------------------------------------------------------------- running themes
def running_themes(owners, records, streaks, team_table, matchdays, last_day_change):
    themes = []

    # Drought: no team wins after 3+ matchdays
    for o in owners:
        if matchdays >= 3 and records[o]["W"] == 0:
            themes.append({
                "tag": f"{o} drought", "owner": o, "kind": "drought",
                "description": f"{o} still has zero team wins after {matchdays} matchdays.",
            })

    # Cinderella: a Tier-4 underdog owned by someone has won a match
    for t in team_table.get("teams", []):
        if t.get("tier") == 4 and t.get("W", 0) > 0:
            themes.append({
                "tag": f"{t['owner']} Cinderella", "owner": t["owner"], "kind": "cinderella",
                "description": f"{t['owner']}'s Tier-4 {t['team']} has a win -- "
                               f"the Cinderella story is alive.",
            })

    # Surge / slide: rank moved 2+ spots on the most recent matchday
    for o, change in (last_day_change or {}).items():
        if change >= 2:
            themes.append({
                "tag": f"{o} surge", "owner": o, "kind": "surge",
                "description": f"{o} jumped {change} spots in the standings on the latest matchday.",
            })
        elif change <= -2:
            themes.append({
                "tag": f"{o} slide", "owner": o, "kind": "slide",
                "description": f"{o} dropped {abs(change)} spots in the standings on the latest matchday.",
            })

    return themes


# --------------------------------------------- cumulative day-by-day standings
def build_history(daily, owners, baseline_ranks):
    """Re-derive standings after each matchday so we can track rank changes."""
    cumulative = {o: 0.0 for o in owners}
    prev_ranks = dict(baseline_ranks)
    history = []

    for day in daily.get("days", []):
        points_today = {o: 0.0 for o in owners}
        for m in day["matches"]:
            for o, pts in m.get("points", {}).items():
                if o in cumulative:
                    cumulative[o] += pts
                    points_today[o] += pts
        # rank: most points first, owner name as a stable tiebreak (matches scoring.py)
        order = sorted(owners, key=lambda o: (-cumulative[o], o))
        ranks = {o: i for i, o in enumerate(order, 1)}
        change = {o: prev_ranks.get(o, ranks[o]) - ranks[o] for o in owners}
        history.append({
            "date": day["date"],
            "points": {o: r2(cumulative[o]) for o in owners},
            "points_today": {o: r2(points_today[o]) for o in owners},
            "ranks": ranks,
            "rank_change": change,
        })
        prev_ranks = ranks
    return history


# --------------------------------------------------------------- phase context
def phase_context(daily, matches_rows, eliminated, preseason):
    matches_total = len(matches_rows) or 104
    matches_played = sum(len(d["matches"]) for d in daily.get("days", []))
    matchdays = len(daily.get("days", []))

    if preseason or matches_played == 0:
        current = "Preseason"
    else:
        # furthest round that has any played match
        best = max((match_phase_key(m) for _, m in iter_matches(daily)),
                   key=lambda p: PHASE_ORDER.get(p, (0, p))[0])
        current = PHASE_ORDER.get(best, (0, best))[1]

    return {
        "current_round": current,
        "is_preseason": bool(preseason or matches_played == 0),
        "matchdays_played": matchdays,
        "matches_played": matches_played,
        "matches_remaining": max(0, matches_total - matches_played),
        "matches_total": matches_total,
        "eliminated_teams": eliminated,
    }


# --------------------------------------------------------------------- assemble
def build_state(args):
    standings = load_json(os.path.join(DATA, "owner_standings.json")) or {}
    daily = load_json(os.path.join(DATA, "daily_results.json")) or {"days": []}
    team_table = load_json(os.path.join(DATA, "team_table.json")) or {"teams": []}
    timeline = load_json(os.path.join(DATA, "timeline.json")) or []
    goals = load_json(os.path.join(DATA, "player_goals.json")) or {}

    draft = (load_json(os.path.join(ROOT_DATA, "draft_board.json")) or {}).get("owners", {})
    tiers = (load_json(os.path.join(ROOT_DATA, "tiers.json")) or {}).get("tiers", {})
    cfg = load_json(os.path.join(ROOT_DATA, "scoring_config.json")) or {}
    matches_rows = load_matches_csv(os.path.join(ROOT_DATA, "matches.csv"))

    owners = list(draft.keys()) or [s["owner"] for s in standings.get("standings", [])]
    owner_of = {team: o for o, teams in draft.items() for team in teams}

    gs = cfg.get("group_stage", {})
    coeff = gs.get("upset_bonus_per_tier_gap", gs.get("upset_bonus", 0))
    tier_gap_mode = gs.get("upset_mode", "flat") == "tier_gap"

    matches_played = sum(len(d["matches"]) for d in daily.get("days", []))
    preseason = args.preseason or matches_played == 0

    # current win / champion probability = latest timeline snapshot (preseason entry
    # if we are forcing a Day-0 state and one is labelled "preseason").
    prob_entry = {}
    if timeline:
        prob_entry = timeline[-1]
        if preseason:
            for e in timeline:
                if e.get("label") == "preseason":
                    prob_entry = e
                    break
    win_prob = prob_entry.get("win_probability", {})
    champ_prob = prob_entry.get("champion_probability", {})

    # ---- preseason / Day-0 state: zeros, ranked by win probability ----
    if preseason:
        order = sorted(owners, key=lambda o: -win_prob.get(o, 0)) if win_prob else sorted(owners)
        ranks = {o: i for i, o in enumerate(order, 1)}
        owners_block = {
            o: {
                "rank": ranks[o],
                "rank_change_from_prev_day": 0,
                "total_points": 0,
                "points_today": 0,
                "record": {"W": 0, "D": 0, "L": 0},
                "win_probability": r2(win_prob[o]) if o in win_prob else None,
                "champion_probability": r2(champ_prob[o]) if o in champ_prob else None,
                "streak": {"type": None, "length": 0, "label": "-"},
                "best_team": None,
                "worst_team": None,
            }
            for o in owners
        }
        # A Day-0 state is truly empty: ignore any (fake/test) results still sitting
        # in the data files so matches_played=0 and there is no Golden Boot yet.
        return {
            "generated": args.generated or "",
            "rules_version": standings.get("rules_version") or cfg.get("version"),
            "tournament": standings.get("tournament") or cfg.get("meta", {}).get("tournament"),
            "source": "preseason",
            "phase": phase_context({"days": []}, matches_rows, [], preseason=True),
            "owners": owners_block,
            "head_to_head_matrix": {},
            "head_to_head_log": [],
            "h2h_differential": {},
            "matchday_point_history": [],
            "dependency_index": {o: 0 for o in owners},
            "notable_events": [],
            "themes": [],
            "golden_boot_leader": None,
            "history": [],
        }

    # ---- live state ----
    baseline_ranks = ({o: i for i, o in enumerate(sorted(owners, key=lambda o: -win_prob.get(o, 0)), 1)}
                      if win_prob else {o: 1 for o in owners})
    history = build_history(daily, owners, baseline_ranks)
    last = history[-1] if history else None
    last_change = last["rank_change"] if last else {}

    records = owner_records(team_table, owners)
    streaks = owner_streaks(daily, owner_of)
    best, worst = best_worst_teams(team_table, owners)
    h2h_matrix, h2h_diff, h2h_log = head_to_head(daily, owner_of)

    # current authoritative rank from owner_standings (fallback to derived history)
    rank_of = {s["owner"]: s["rank"] for s in standings.get("standings", [])}
    points_of = {s["owner"]: s["total_points"] for s in standings.get("standings", [])}
    if last:
        for o in owners:
            rank_of.setdefault(o, last["ranks"][o])
            points_of.setdefault(o, last["points"][o])

    eliminated = []
    matchdays = len(daily.get("days", []))
    events = notable_events(daily, owner_of, tiers, history, coeff, tier_gap_mode)
    eliminated = [{"team": e["team"], "owner": e.get("owner"), "round": e.get("round")}
                  for e in events if e["type"] == "elimination"]
    themes = running_themes(owners, records, streaks, team_table, matchdays, last_change)

    owners_block = {
        o: {
            "rank": rank_of.get(o),
            "rank_change_from_prev_day": last_change.get(o, 0),
            "total_points": r2(points_of.get(o, 0)),
            "points_today": r2(last["points_today"][o]) if last else 0,
            "record": records[o],
            "win_probability": r2(win_prob[o]) if o in win_prob else None,
            "champion_probability": r2(champ_prob[o]) if o in champ_prob else None,
            "streak": streaks.get(o, {"type": None, "length": 0, "label": "-"}),
            "best_team": best.get(o),
            "worst_team": worst.get(o),
        }
        for o in owners
    }

    return {
        "generated": args.generated or "",
        "rules_version": standings.get("rules_version") or cfg.get("version"),
        "tournament": standings.get("tournament") or cfg.get("meta", {}).get("tournament"),
        "source": standings.get("source", "unknown"),
        "phase": phase_context(daily, matches_rows, eliminated, preseason=False),
        "owners": owners_block,
        "head_to_head_matrix": h2h_matrix,
        "head_to_head_log": h2h_log,
        "h2h_differential": h2h_diff,
        "matchday_point_history": matchday_point_history(history),
        "dependency_index": dependency_index(team_table, owners),
        "notable_events": events,
        "themes": themes,
        "golden_boot_leader": _golden_boot_leader(goals),
        "history": history,
    }


def _golden_boot_leader(goals):
    leaders = (goals or {}).get("leaders", [])
    if not leaders:
        return None
    top = leaders[0]
    return {"player": top.get("player"), "team": top.get("team"),
            "owner": top.get("owner"), "goals": top.get("goals")}


def main():
    ap = argparse.ArgumentParser(description="Build the rolling narrative state")
    ap.add_argument("--preseason", action="store_true",
                    help="force the Day-0 state (all zeros, preseason probabilities) "
                         "even if results files are populated")
    ap.add_argument("--generated", default=None,
                    help="ISO timestamp for the 'generated' field (CI passes one)")
    args = ap.parse_args()

    state = build_state(args)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

    ph = state["phase"]
    print(f"Wrote {OUT}")
    print(f"  phase: {ph['current_round']} | played {ph['matches_played']} / "
          f"{ph['matches_total']} | remaining {ph['matches_remaining']}")
    print(f"  owners: {len(state['owners'])} | events: {len(state['notable_events'])} | "
          f"themes: {len(state['themes'])}")


if __name__ == "__main__":
    main()
