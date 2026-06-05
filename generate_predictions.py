#!/usr/bin/env python3
"""Daily match prediction tracker for the WC Challenge.

For every match on a given date, record the model's predicted outcome
probabilities BEFORE the match is played, then (post-match) backfill the actual
result and score how well the model did. The output is a single cumulative file
the site can read: site/data/predictions.json.

Why a separate file from the win-probability timeline: this tracks the model's
*match-level* calibration (was Brazil-to-beat-Morocco priced right?), not the
owner-standings forecast. It reuses the same vendored sim so the probabilities
are identical to what the preseason/daily engine uses.

The probability math is NOT reimplemented here -- it comes from
sim.match_model.group_match_probs (group: win/draw/loss) and
sim.match_model.expected_score (knockout: a single win probability, no draw).
Team strength ratings come from engine.load_context(), which canonicalises team
spellings via data/team_aliases.json so match names agree with the ratings.

Cumulative & write-once: a match's prediction is recorded the first time its date
comes up and never overwritten -- we never "re-predict" after kickoff. Knockout
fixtures whose teams are still TBD are skipped until the bracket fills in.

Usage:
    # daily: record predictions for today's matches (idempotent -- safe to rerun)
    python generate_predictions.py

    # testing / backfill a specific day:
    python generate_predictions.py --date 2026-06-13

    # post-match: pull actual results in and refresh the Brier/accuracy summary:
    python generate_predictions.py --score

Inputs (read-only):
    data/matches.csv            full fixture list (dates, group structure, teams)
    data/team_strength.csv      Elo-style strength ratings (via load_context)
    data/team_aliases.json      spelling -> canonical name (via load_context)
    data/live_results.json      actual results so far (scoring.py schema), --score only

Output (the ONLY file this writes):
    site/data/predictions.json  {"meta": {...}, "predictions": [...]}, cumulative
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import date, datetime

from sim import engine
from sim.match_model import expected_score, group_match_probs

HERE = os.path.dirname(os.path.abspath(__file__))
MATCHES_CSV = os.path.join(HERE, "data", "matches.csv")
LIVE_RESULTS = os.path.join(HERE, "data", "live_results.json")
PREDICTIONS = os.path.join(HERE, "site", "data", "predictions.json")

# Placeholder used in matches.csv for knockout fixtures whose teams aren't known.
TBD = "TBD"


def match_id_for(phase: str, group: str, seq: int) -> str:
    """Stable, fixture-derived id. Group matches are numbered within their group
    (grp_C_1 = the first Group C fixture in matches.csv); knockout matches are
    numbered within their round (round_of_32_1, ...). matches.csv is static, so
    the same row always yields the same id across runs."""
    if phase == "group":
        return f"grp_{group}_{seq}"
    return f"{phase}_{seq}"


def load_fixtures_with_ids() -> list[dict]:
    """Read matches.csv in file order and attach a deterministic match_id to each
    row. IDs are assigned per (group | round) counter so they stay stable."""
    counters: dict[str, int] = {}
    fixtures: list[dict] = []
    with open(MATCHES_CSV, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            phase = row["phase"].strip().lower()
            group = row["group"].strip()
            key = group if phase == "group" else phase
            counters[key] = counters.get(key, 0) + 1
            fixtures.append({
                "match_id": match_id_for(phase, group, counters[key]),
                "date": row["date"].strip(),
                "phase": phase,
                "group": group,
                "team1": row["team1"].strip(),
                "team2": row["team2"].strip(),
            })
    return fixtures


def predict_match(ctx, fx: dict) -> dict | None:
    """Build a prediction record for one fixture, or None if it isn't predictable
    yet (teams still TBD or missing a strength rating)."""
    t1, t2 = ctx.canon(fx["team1"]), ctx.canon(fx["team2"])
    if t1 == TBD or t2 == TBD or t1 not in ctx.ratings or t2 not in ctx.ratings:
        return None

    r1, r2 = ctx.ratings[t1], ctx.ratings[t2]
    is_group = fx["phase"] == "group"
    if is_group:
        p1, pd, p2 = group_match_probs(r1, r2)
        predicted = {
            "team1_win": round(float(p1), 4),
            "draw": round(float(pd), 4),
            "team2_win": round(float(p2), 4),
        }
        stage = "group"
    else:
        e1 = expected_score(r1, r2)
        predicted = {
            "team1_win": round(float(e1), 4),
            "team2_win": round(float(1.0 - e1), 4),
        }
        stage = fx["phase"]

    return {
        "date": fx["date"],
        "match_id": fx["match_id"],
        "team1": t1,
        "team2": t2,
        "stage": stage,
        "predicted": predicted,
        "actual": None,
    }


def load_existing() -> list[dict]:
    """Load the cumulative predictions list. Accepts the wrapped object form this
    script writes, or a bare list (so an older/hand-edited file still loads)."""
    if not os.path.exists(PREDICTIONS):
        return []
    try:
        with open(PREDICTIONS, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(data, dict):
        preds = data.get("predictions", [])
        return preds if isinstance(preds, list) else []
    return data if isinstance(data, list) else []


def add_predictions(ctx, existing: list[dict], target: str) -> int:
    """Append predictions for every predictable match on `target` whose match_id
    isn't already recorded. Returns how many were added."""
    have = {p["match_id"] for p in existing}
    added = 0
    for fx in load_fixtures_with_ids():
        if fx["date"] != target or fx["match_id"] in have:
            continue
        rec = predict_match(ctx, fx)
        if rec is None:
            continue
        existing.append(rec)
        have.add(rec["match_id"])
        added += 1
    return added


# --------------------------------------------------------------- scoring
def _result_label(s1: int, s2: int, allow_draw: bool, pen: int = 0) -> str:
    """Normalise a scoreline to team1_win / draw / team2_win. For knockouts a
    level score is broken by the penalty differential (pen = pen1 - pen2)."""
    if s1 > s2:
        return "team1_win"
    if s2 > s1:
        return "team2_win"
    if allow_draw:
        return "draw"
    return "team1_win" if pen > 0 else "team2_win"


def index_results(ctx, results: dict) -> dict[frozenset, dict]:
    """Index live results by their (canonical) unordered team pair. Each pair
    plays at most once per tournament stage, so the pair identifies the match
    without depending on home/away order matching the fixture's team1/team2."""
    index: dict[frozenset, dict] = {}
    for m in results.get("matches", []):
        try:
            home, away = ctx.canon(m["home"]), ctx.canon(m["away"])
            hs, as_ = int(m["home_score"]), int(m["away_score"])
        except (KeyError, TypeError, ValueError):
            continue
        pen = int(m.get("pen_home", 0)) - int(m.get("pen_away", 0))
        index[frozenset((home, away))] = {
            "home": home, "away": away, "hs": hs, "as": as_, "pen": pen,
        }
    return index


def backfill_actuals(predictions: list[dict], results_index: dict) -> int:
    """Fill the `actual` field for any prediction whose match now has a result.
    Already-scored predictions are left untouched. Returns count newly scored."""
    scored = 0
    for p in predictions:
        if p["actual"] is not None:
            continue
        r = results_index.get(frozenset((p["team1"], p["team2"])))
        if r is None:
            continue
        # Orient the scoreline to this prediction's team1/team2 ordering.
        if r["home"] == p["team1"]:
            s1, s2, pen = r["hs"], r["as"], r["pen"]
        else:
            s1, s2, pen = r["as"], r["hs"], -r["pen"]
        allow_draw = p["stage"] == "group"
        p["actual"] = {
            "team1_score": s1,
            "team2_score": s2,
            "result": _result_label(s1, s2, allow_draw, pen),
        }
        scored += 1
    return scored


def _classes(stage: str) -> list[str]:
    return ["team1_win", "draw", "team2_win"] if stage == "group" else ["team1_win", "team2_win"]


def compute_meta(predictions: list[dict]) -> dict:
    """Running Brier score (multiclass, one-hot actual) and winner-call accuracy
    over every scored prediction. Brier is the mean squared error between the
    predicted distribution and the realised outcome; lower is better."""
    scored = [p for p in predictions if p["actual"] is not None]
    meta = {
        "total_predictions": len(predictions),
        "scored": len(scored),
        "brier_score": None,
        "correct_winner_pct": None,
    }
    if not scored:
        return meta

    brier_sum = 0.0
    correct = 0
    for p in scored:
        classes = _classes(p["stage"])
        actual = p["actual"]["result"]
        brier_sum += sum((p["predicted"][c] - (1.0 if c == actual else 0.0)) ** 2 for c in classes)
        predicted_pick = max(classes, key=lambda c: p["predicted"][c])
        if predicted_pick == actual:
            correct += 1

    meta["brier_score"] = round(brier_sum / len(scored), 4)
    meta["correct_winner_pct"] = round(correct / len(scored), 4)
    return meta


def write_predictions(predictions: list[dict]) -> None:
    predictions.sort(key=lambda p: (p["date"], p["match_id"]))
    payload = {"meta": compute_meta(predictions), "predictions": predictions}
    os.makedirs(os.path.dirname(PREDICTIONS), exist_ok=True)
    with open(PREDICTIONS, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate/track WC match predictions.")
    parser.add_argument("--date", help="YYYY-MM-DD to predict (default: today).")
    parser.add_argument("--score", action="store_true",
                        help="Backfill actuals from data/live_results.json and refresh the summary.")
    args = parser.parse_args()

    if args.date:
        try:
            datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            raise SystemExit(f"--date must be YYYY-MM-DD, got: {args.date!r}")
        target = args.date
    else:
        target = date.today().isoformat()

    ctx = engine.load_context()
    predictions = load_existing()

    added = add_predictions(ctx, predictions, target)
    print(f"  predictions: +{added} new for {target} ({len(predictions)} total)")

    if args.score:
        with open(LIVE_RESULTS, encoding="utf-8") as f:
            results = json.load(f)
        newly = backfill_actuals(predictions, index_results(ctx, results))
        print(f"  scoring: +{newly} newly scored")

    write_predictions(predictions)
    meta = compute_meta(predictions)
    print(f"  summary: scored={meta['scored']}/{meta['total_predictions']} "
          f"brier={meta['brier_score']} correct_winner_pct={meta['correct_winner_pct']}")
    print(f"  wrote {os.path.relpath(PREDICTIONS, HERE)}")


if __name__ == "__main__":
    main()
