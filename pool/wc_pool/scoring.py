"""Apply scoring_config.json to simulated tournaments and aggregate over many sims.

This is the Phase 2 scoring layer that sits on top of the Phase 1 simulation
engine (groups -> Annex C thirds -> official bracket). It reads the *current*
``scoring_config.json`` (schema ``provisional_v1``) and turns each simulated
tournament into a per-owner score, then aggregates across N Monte Carlo runs.

------------------------------------------------------------------------------
Interpretation of the provisional_v1 schema (documented so it can be tuned)
------------------------------------------------------------------------------
GROUP STAGE (only a drafted team scores, for its owner):
  * win / draw / loss      -> +3 / +1 / 0   (standings points == base match pts)
  * upset_bonus (+2)       -> added when a drafted team WINS and its tier number
                              is HIGHER (weaker) than the loser's tier number.
                              Tiers are looked up UNIVERSALLY from tiers.csv for
                              all 48 teams (drafted or not), so a drafted team can
                              earn the bonus by beating any stronger-tier side.
                              The point still only goes to the drafting owner; if
                              the winner is undrafted, nobody scores.

KNOCKOUT STAGE:
  * round_of_32 bonus (0)  -> credited for qualifying out of the group (0 pts).
  * match_points_apply     -> true, so every knockout WIN earns the group win
                              value (+3). There are no draws in the knockouts.
  * advancement_bonuses    -> credited to the team that REACHES a round by winning
                              the prior match:
                                win an R32 match -> reach R16        -> +3
                                win an R16 match -> reach QF          -> +5
                                win a  QF match  -> reach SF          -> +7
                                win an SF match  -> reach Final       -> +10
                                win the Final    -> win_world_cup     -> +15

All of these are read straight from scoring_config.json; nothing is hard-coded
except the mapping of "which round does winning match X advance you into".
"""

from __future__ import annotations

from collections import defaultdict

from . import bracket, match_model


# round a team ENTERS by winning a match of the given ROUND_NAME bucket
_WIN_ADVANCES_INTO = {
    "R32": "round_of_16",
    "R16": "quarterfinal",
    "QF": "semifinal",
    "SF": "final",
    "Final": "win_world_cup",
}


def build_roster(drafted_rows) -> dict[str, dict]:
    """canonical team -> {owner, tier}. (team already normalized by data_io.)"""
    return {r["team"]: {"owner": r["owner"], "tier": r["tier"]} for r in drafted_rows}


def simulate_and_score(ctx, roster, tiers, cfg, rng) -> dict:
    """Run ONE tournament and return per-owner score components + champion info.

    `tiers` is the universal team -> tier map (tiers.csv) used for the upset bonus.
    Mirrors wc_pool.tournament.simulate_tournament, but captures per-match detail
    so the upset bonus and knockout bonuses can be scored exactly.
    """
    gs = cfg["group_stage"]
    ks = cfg["knockout_stage"]
    adv = ks["advancement_bonuses"]
    win_pts, draw_pts, loss_pts = gs["win"], gs["draw"], gs["loss"]
    # Upset bonus: "tier_gap" scales by (winner_tier - loser_tier); else flat.
    upset_mode = gs.get("upset_mode", "flat")
    upset_coeff = gs.get("upset_bonus_per_tier_gap", gs.get("upset_bonus", 0))
    ko_match_pts = win_pts if ks.get("match_points_apply") else 0

    owners = sorted({r["owner"] for r in roster.values()})
    comp = {o: {"group_base": 0.0, "group_upset": 0.0,
                "ko_match": 0.0, "ko_advance": 0.0,
                "qualifiers": 0, "ko_wins": 0} for o in owners}
    # upsets[(winner_tier, loser_tier)][owner] = count
    upsets: dict[tuple, dict] = defaultdict(lambda: defaultdict(int))

    owner_of = {t: roster[t]["owner"] for t in roster}

    def credit_result(winner, loser, drew):
        """Award base group points and (on a win) the universal-tier upset bonus."""
        if drew:
            for t in (winner, loser):
                o = owner_of.get(t)
                if o is not None:
                    comp[o]["group_base"] += draw_pts
            return
        wo = owner_of.get(winner)
        if wo is not None:
            comp[wo]["group_base"] += win_pts
            wt, lt = tiers.get(winner), tiers.get(loser)
            if wt is not None and lt is not None and wt > lt:   # weaker beat stronger
                gap = wt - lt
                bonus = upset_coeff * gap if upset_mode == "tier_gap" else upset_coeff
                comp[wo]["group_upset"] += bonus
                upsets[(wt, lt)][wo] += 1
        lo = owner_of.get(loser)
        if lo is not None:
            comp[lo]["group_base"] += loss_pts

    # ---- group stage (capture each result) ----------------------------------
    record = {t: {"pts": 0, "gd": 0, "gf": 0} for g in ctx.groups for t in ctx.groups[g]}
    for g, a, b in ctx.group_pairings:
        ga, gb = match_model.simulate_group_match(ctx.ratings[a], ctx.ratings[b], rng)
        record[a]["gf"] += ga; record[a]["gd"] += ga - gb
        record[b]["gf"] += gb; record[b]["gd"] += gb - ga
        if ga > gb:
            record[a]["pts"] += 3
            credit_result(a, b, drew=False)
        elif gb > ga:
            record[b]["pts"] += 3
            credit_result(b, a, drew=False)
        else:
            record[a]["pts"] += 1; record[b]["pts"] += 1
            credit_result(a, b, drew=True)

    # ---- standings -> bracket feeders ---------------------------------------
    group_order = {}
    for g, teams in ctx.groups.items():
        group_order[g] = sorted(
            teams, key=lambda t: (record[t]["pts"], record[t]["gd"], record[t]["gf"], rng.random()),
            reverse=True)

    r32_qualifiers = set()
    for g in group_order:
        r32_qualifiers.add(group_order[g][0])
        r32_qualifiers.add(group_order[g][1])
    thirds = sorted(group_order, key=lambda g: (
        record[group_order[g][2]]["pts"], record[group_order[g][2]]["gd"],
        record[group_order[g][2]]["gf"], rng.random()), reverse=True)
    qual_third_groups = thirds[:8]
    for g in qual_third_groups:
        r32_qualifiers.add(group_order[g][2])
    annex = ctx.third_table.assign(qual_third_groups)

    for t in r32_qualifiers:
        o = owner_of.get(t)
        if o is not None:
            comp[o]["qualifiers"] += 1
            comp[o]["ko_advance"] += adv["round_of_32"]   # 0 in provisional_v1

    # ---- knockout rounds ----------------------------------------------------
    def resolve(feeder):
        kind, val = feeder
        if kind == "W":
            return group_order[val][0]
        if kind == "R":
            return group_order[val][1]
        return group_order[annex[val]][2]

    ko_winners = {}
    ko_losers = {}

    def play(mnum, a, b, advance=True):
        a_adv = match_model.simulate_knockout(ctx.ratings[a], ctx.ratings[b], rng)
        win, lose = (a, b) if a_adv else (b, a)
        ko_winners[mnum] = win
        ko_losers[mnum] = lose
        o = owner_of.get(win)
        if o is not None:
            comp[o]["ko_wins"] += 1
            comp[o]["ko_match"] += ko_match_pts
            if advance:                  # third-place game advances to nothing
                comp[o]["ko_advance"] += adv[_WIN_ADVANCES_INTO[bracket.ROUND_NAME[mnum]]]
        return win

    for mnum, (f1, f2) in bracket.R32_DEFS.items():
        play(mnum, resolve(f1), resolve(f2))
    for defs in (bracket.R16_DEFS, bracket.QF_DEFS, bracket.SF_DEFS):
        for mnum, (m1, m2) in defs.items():
            play(mnum, ko_winners[m1], ko_winners[m2])
    # third-place game (match 103): SF losers; match points only (locked rule)
    play(103, ko_losers[101], ko_losers[102], advance=False)
    champion = play(104, ko_winners[bracket.FINAL_FEEDERS[0]], ko_winners[bracket.FINAL_FEEDERS[1]])

    totals = {o: comp[o]["group_base"] + comp[o]["group_upset"] +
                 comp[o]["ko_match"] + comp[o]["ko_advance"] for o in owners}
    return {"components": comp, "totals": totals, "upsets": upsets,
            "champion": champion, "champion_owner": owner_of.get(champion)}
