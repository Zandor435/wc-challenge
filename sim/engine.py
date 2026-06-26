"""Forward-simulation + scoring layer for the daily win-probability engine.

This is the NEW piece (everything else in sim/ is vendored unchanged). It:

  1. Loads the field from the wc-challenge data files, unifying every source into
     ONE canonical namespace -- the draft-board spelling (e.g. "Korea Republic",
     "Turkey", "USA", "Bosnia", "Curacao"). team_strength.csv uses schedule
     spellings ("South Korea", "Türkiye", ...) and is canonicalised on load via
     data/team_aliases.json so it agrees with draft_board.json / tiers.json.

  2. Splits actual results into "locked" group and knockout outcomes.

  3. Simulates ONLY the remaining matches forward, per Monte Carlo draw, resolving
     group standings -> Annex C thirds -> the official bracket, and scores every
     match (locked + simulated) under rebalanced_v3. Locked matches contribute the
     same points in every sim (the "already earned" floor); simulated matches vary.

The group/knockout point rules are read straight from scoring_config.json and are
identical to the production scoring.py (win 3 / draw 1 / loss 0; tier-gap upset
+2 per gap; draw-upset +0.5 when a weaker tier draws a stronger one; knockout win 3;
advancement R16 2 / QF 5 / SF 10 / Final 18 / WC 30).
A zero-locked run therefore reproduces the preseason 20k baseline within MC noise.
"""

from __future__ import annotations

import csv
import json
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import bracket, match_model
from .third_place import ThirdPlaceTable

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

# Round a team ENTERS by winning a match of the given round bucket.
_WIN_ADVANCES_INTO = {
    "R32": "round_of_16",
    "R16": "quarterfinal",
    "QF": "semifinal",
    "SF": "final",
    "Final": "win_world_cup",
}
# Knockout round codes used in results JSON / fixtures -> bracket round bucket.
_KO_ROUND_CODE = {
    "R32": "R32", "ROUND_OF_32": "R32",
    "R16": "R16", "ROUND_OF_16": "R16",
    "QF": "QF", "QUARTERFINAL": "QF",
    "SF": "SF", "SEMIFINAL": "SF",
    "FINAL": "Final",
    "3RD": "3RD", "THIRD_PLACE": "3RD",
}


# --------------------------------------------------------------------------- IO
def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def load_aliases(path=None) -> dict[str, str]:
    """alias(lower) -> canonical, from data/team_aliases.json."""
    path = Path(path) if path else DATA / "team_aliases.json"
    with open(path, encoding="utf-8") as f:
        raw = json.load(f).get("aliases", {})
    return {k.strip().lower(): v.strip() for k, v in raw.items()}


def make_canon(aliases: dict[str, str]):
    """Canonicalise any team spelling to the draft-board name."""
    canon_names = set(aliases.values())

    def canon(name: str) -> str:
        if name is None:
            return None
        raw = " ".join(name.split()).strip().strip(",.")
        if raw.lower() in aliases:
            return aliases[raw.lower()]
        stripped = _strip_accents(raw)
        if stripped.lower() in aliases:
            return aliases[stripped.lower()]
        for c in canon_names:
            if _strip_accents(c).lower() == stripped.lower():
                return c
        return raw

    return canon


def load_draft(canon, path=None) -> tuple[dict[str, str], list[str]]:
    """Return (owner_of: team->owner, owners: sorted list)."""
    path = Path(path) if path else DATA / "draft_board.json"
    with open(path, encoding="utf-8") as f:
        owners_map = json.load(f)["owners"]
    owner_of = {}
    for owner, teams in owners_map.items():
        for t in teams:
            owner_of[canon(t)] = owner
    return owner_of, sorted(owners_map.keys())


def load_tiers(canon, path=None) -> dict[str, int]:
    path = Path(path) if path else DATA / "tiers.json"
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)["tiers"]
    return {canon(t): int(v) for t, v in raw.items()}


def load_strength(canon, path=None) -> dict[str, float]:
    path = Path(path) if path else DATA / "team_strength.csv"
    ratings: dict[str, float] = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            ratings[canon(row["team"])] = float(row["strength_rating"])
    return ratings


def load_scoring_config(path=None) -> dict:
    path = Path(path) if path else DATA / "scoring_config.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@dataclass
class Fixtures:
    groups: dict[str, list[str]]                       # group -> [teams]
    group_pairings: list[tuple[str, str, str, str]]    # (group, team_a, team_b, date)
    ko_dates: dict[str, list[str]]                     # round bucket -> [dates]


def load_fixtures(canon, path=None) -> Fixtures:
    """Parse the full fixture list (matches.csv): group structure + dates."""
    path = Path(path) if path else DATA / "matches.csv"
    groups: dict[str, list[str]] = defaultdict(list)
    seen: dict[str, set] = defaultdict(set)
    pairings: list[tuple[str, str, str, str]] = []
    ko_dates: dict[str, list[str]] = defaultdict(list)
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            phase = row["phase"].strip().lower()
            if phase == "group":
                g = row["group"].strip()
                a, b = canon(row["team1"]), canon(row["team2"])
                for t in (a, b):
                    if t not in seen[g]:
                        seen[g].add(t)
                        groups[g].append(t)
                pairings.append((g, a, b, row["date"].strip()))
            else:
                bucket = _KO_ROUND_CODE.get(phase.upper().replace(" ", "_"), None)
                if bucket:
                    ko_dates[bucket].append(row["date"].strip())
    return Fixtures(dict(groups), pairings, dict(ko_dates))


# ----------------------------------------------------------------- context
@dataclass
class Context:
    groups: dict[str, list[str]]
    group_pairings: list[tuple[str, str, str, str]]
    ratings: dict[str, float]
    tiers: dict[str, int]
    owner_of: dict[str, str]
    owners: list[str]
    cfg: dict
    third_table: ThirdPlaceTable
    canon: object

    # scoring params unpacked from cfg
    win_pts: int = 0
    draw_pts: int = 0
    loss_pts: int = 0
    upset_mode: str = "flat"
    upset_coeff: float = 0
    draw_upset_mode: str = "flat"
    draw_upset_coeff: float = 0
    ko_match_pts: int = 0
    adv: dict = field(default_factory=dict)


def load_context() -> Context:
    aliases = load_aliases()
    canon = make_canon(aliases)
    fx = load_fixtures(canon)
    ratings = load_strength(canon)
    tiers = load_tiers(canon)
    owner_of, owners = load_draft(canon)
    cfg = load_scoring_config()
    third_table = ThirdPlaceTable.load()

    # --- sanity checks (mirror the preseason engine) ---
    if len(fx.groups) != 12 or any(len(t) != 4 for t in fx.groups.values()):
        bad = {g: len(t) for g, t in fx.groups.items() if len(t) != 4}
        raise SystemExit(f"Group structure invalid (expected 12 groups of 4): {bad or fx.groups.keys()}")
    field_teams = {t for ts in fx.groups.values() for t in ts}
    miss_rating = sorted(t for t in field_teams if t not in ratings)
    if miss_rating:
        raise SystemExit(f"Field teams missing a strength rating (alias gap?): {miss_rating}")
    miss_tier = sorted(t for t in field_teams if t not in tiers)
    if miss_tier:
        raise SystemExit(f"Field teams missing a tier (alias gap?): {miss_tier}")
    miss_owner = sorted(t for t in owner_of if t not in field_teams)
    if miss_owner:
        raise SystemExit(f"Drafted teams not found in the fixture field (alias gap?): {miss_owner}")

    gs, ks = cfg["group_stage"], cfg["knockout_stage"]
    return Context(
        groups=fx.groups, group_pairings=fx.group_pairings,
        ratings=ratings, tiers=tiers, owner_of=owner_of, owners=owners,
        cfg=cfg, third_table=third_table, canon=canon,
        win_pts=gs["win"], draw_pts=gs["draw"], loss_pts=gs["loss"],
        upset_mode=gs.get("upset_mode", "flat"),
        upset_coeff=gs.get("upset_bonus_per_tier_gap", gs.get("upset_bonus", 0)),
        draw_upset_mode=gs.get("draw_upset_mode", "flat"),
        draw_upset_coeff=gs.get("draw_upset_bonus", 0),
        ko_match_pts=(ks.get("win", gs["win"]) if ks.get("match_points_apply") else 0),
        adv=ks["advancement_bonuses"],
    )


# ------------------------------------------------------------ locked results
@dataclass
class LockedResults:
    # frozenset({a,b}) -> (goals_a_by_team dict, raw)  for completed group matches
    group: dict
    # frozenset({a,b}) -> winner team  for completed knockout matches
    ko: dict
    # the round bucket of each completed ko match (for the locked-only scorer)
    ko_round: dict
    n_group: int = 0
    n_ko: int = 0


def split_results(ctx: Context, results: dict) -> LockedResults:
    """Partition the actual results JSON into locked group / knockout outcomes."""
    canon = ctx.canon
    group, ko, ko_round = {}, {}, {}
    n_group = n_ko = 0
    for m in results.get("matches", []):
        stage = str(m.get("stage", "group")).lower()
        home, away = canon(m["home"]), canon(m["away"])
        hs, as_ = m["home_score"], m["away_score"]
        key = frozenset((home, away))
        if stage.startswith("group"):
            group[key] = {home: hs, away: as_}
            n_group += 1
        else:
            if hs == as_:
                pw = m.get("pen_home", 0) - m.get("pen_away", 0)
                winner = home if pw > 0 else away
            else:
                winner = home if hs > as_ else away
            ko[key] = winner
            bucket = _KO_ROUND_CODE.get(str(m.get("round", "")).upper(), None)
            ko_round[key] = bucket
            n_ko += 1
    return LockedResults(group, ko, ko_round, n_group, n_ko)


# --------------------------------------------------------------- scoring core
def _credit_group(ctx, comp, a, b, ga, gb):
    """Award group base + tier-gap upset points for one (locked or simulated) match."""
    owner_of = ctx.owner_of
    if ga == gb:
        ta, tb = ctx.tiers.get(a), ctx.tiers.get(b)
        # draw-upset: lower-tier (higher number) side that holds a higher-tier
        # opponent earns a bonus, even when the opponent is undrafted.
        underdog = None
        if ta is not None and tb is not None and ta != tb:
            underdog = a if ta > tb else b
        for t in (a, b):
            o = owner_of.get(t)
            if o is not None:
                comp[o]["group_base"] += ctx.draw_pts
                if t == underdog and ctx.draw_upset_coeff:
                    ut, ot = ctx.tiers[t], ctx.tiers[b if t == a else a]
                    gap = ut - ot
                    bonus = (ctx.draw_upset_coeff * gap if ctx.draw_upset_mode == "tier_gap"
                             else ctx.draw_upset_coeff)
                    comp[o]["group_upset"] += bonus
        return
    winner, loser = (a, b) if ga > gb else (b, a)
    wo = owner_of.get(winner)
    if wo is not None:
        comp[wo]["group_base"] += ctx.win_pts
        wt, lt = ctx.tiers.get(winner), ctx.tiers.get(loser)
        if wt is not None and lt is not None and wt > lt:   # weaker beat stronger
            gap = wt - lt
            bonus = ctx.upset_coeff * gap if ctx.upset_mode == "tier_gap" else ctx.upset_coeff
            comp[wo]["group_upset"] += bonus
    lo = owner_of.get(loser)
    if lo is not None:
        comp[lo]["group_base"] += ctx.loss_pts


def _new_comp(owners):
    return {o: {"group_base": 0.0, "group_upset": 0.0, "ko_match": 0.0,
                "ko_advance": 0.0, "qualifiers": 0, "ko_wins": 0} for o in owners}


def _total(comp, o):
    c = comp[o]
    return c["group_base"] + c["group_upset"] + c["ko_match"] + c["ko_advance"]


def simulate_forward(ctx: Context, locked: LockedResults, rng) -> dict:
    """Run ONE forward simulation (locked actuals + simulated remainder) and score it.

    Returns {"totals": {owner: pts}, "components": {...}, "champion": team,
             "champion_owner": owner or None}.
    """
    comp = _new_comp(ctx.owners)
    owner_of = ctx.owner_of
    record = {t: {"pts": 0, "gd": 0, "gf": 0} for g in ctx.groups for t in ctx.groups[g]}

    # ---- group stage: lock completed, simulate the rest ----
    for g, a, b, _date in ctx.group_pairings:
        key = frozenset((a, b))
        lk = locked.group.get(key)
        if lk is not None:
            ga, gb = lk[a], lk[b]
        else:
            ga, gb = match_model.simulate_group_match(ctx.ratings[a], ctx.ratings[b], rng)
        record[a]["gf"] += ga; record[a]["gd"] += ga - gb
        record[b]["gf"] += gb; record[b]["gd"] += gb - ga
        if ga > gb:
            record[a]["pts"] += 3
        elif gb > ga:
            record[b]["pts"] += 3
        else:
            record[a]["pts"] += 1; record[b]["pts"] += 1
        _credit_group(ctx, comp, a, b, ga, gb)

    # ---- standings -> qualifiers -> Annex C ----
    group_order = {}
    for g, teams in ctx.groups.items():
        group_order[g] = sorted(
            teams, key=lambda t: (record[t]["pts"], record[t]["gd"], record[t]["gf"], rng.random()),
            reverse=True)
    qualifiers = set()
    for g in group_order:
        qualifiers.add(group_order[g][0])
        qualifiers.add(group_order[g][1])
    thirds = sorted(group_order, key=lambda g: (
        record[group_order[g][2]]["pts"], record[group_order[g][2]]["gd"],
        record[group_order[g][2]]["gf"], rng.random()), reverse=True)
    qual_third_groups = thirds[:8]
    for g in qual_third_groups:
        qualifiers.add(group_order[g][2])
    annex = ctx.third_table.assign(qual_third_groups)

    for t in qualifiers:
        o = owner_of.get(t)
        if o is not None:
            comp[o]["qualifiers"] += 1
            comp[o]["ko_advance"] += ctx.adv["round_of_32"]   # 0 in rebalanced_v3

    # ---- knockout: lock completed matches (by participant set), simulate the rest ----
    def resolve(feeder):
        kind, val = feeder
        if kind == "W":
            return group_order[val][0]
        if kind == "R":
            return group_order[val][1]
        return group_order[annex[val]][2]

    ko_winners, ko_losers = {}, {}

    def play(mnum, a, b, advance=True):
        key = frozenset((a, b))
        locked_winner = locked.ko.get(key)
        if locked_winner in (a, b):
            win = locked_winner
        else:
            win = a if match_model.simulate_knockout(ctx.ratings[a], ctx.ratings[b], rng) else b
        lose = b if win == a else a
        ko_winners[mnum] = win
        ko_losers[mnum] = lose
        o = owner_of.get(win)
        if o is not None:
            comp[o]["ko_wins"] += 1
            comp[o]["ko_match"] += ctx.ko_match_pts
            if advance:
                comp[o]["ko_advance"] += ctx.adv[_WIN_ADVANCES_INTO[bracket.ROUND_NAME[mnum]]]
        return win

    for mnum, (f1, f2) in bracket.R32_DEFS.items():
        play(mnum, resolve(f1), resolve(f2))
    for defs in (bracket.R16_DEFS, bracket.QF_DEFS, bracket.SF_DEFS):
        for mnum, (m1, m2) in defs.items():
            play(mnum, ko_winners[m1], ko_winners[m2])
    play(103, ko_losers[101], ko_losers[102], advance=False)   # third-place game
    champion = play(104, ko_winners[bracket.FINAL_FEEDERS[0]],
                    ko_winners[bracket.FINAL_FEEDERS[1]])

    totals = {o: _total(comp, o) for o in ctx.owners}
    return {"totals": totals, "components": comp, "ko_winners": ko_winners,
            "champion": champion, "champion_owner": owner_of.get(champion)}


def score_locked_only(ctx: Context, locked: LockedResults) -> dict[str, float]:
    """Deterministic points already earned from completed matches (for reporting /
    a consistency check against owner_standings.json). Knockout advancement is
    credited from each completed match's own round code."""
    comp = _new_comp(ctx.owners)
    # completed group matches
    for g, a, b, _date in ctx.group_pairings:
        lk = locked.group.get(frozenset((a, b)))
        if lk is not None:
            _credit_group(ctx, comp, a, b, lk[a], lk[b])
    # qualifiers bonus (round_of_32 == 0) is only known once a group is complete;
    # it is 0 in rebalanced_v3 so it never affects the floor and is omitted here.
    # completed knockout matches
    for key, winner in locked.ko.items():
        o = ctx.owner_of.get(winner)
        if o is None:
            continue
        comp[o]["ko_match"] += ctx.ko_match_pts
        bucket = locked.ko_round.get(key)
        if bucket and bucket in _WIN_ADVANCES_INTO:   # not the 3rd-place game
            comp[o]["ko_advance"] += ctx.adv[_WIN_ADVANCES_INTO[bucket]]
    return {o: round(_total(comp, o), 2) for o in ctx.owners}
