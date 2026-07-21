"""Simulate one full tournament: group stage -> knockout -> champion.

Phase 1 scope: produce a structurally valid bracket using the official Annex C
third-place allocation and the official bracket tree. The result object captures
enough to (a) validate the bracket and (b) later drive scoring.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from . import bracket, data_io, match_model
from .schedule_parser import parse_schedule
from .third_place import ThirdPlaceTable


@dataclass
class TournamentContext:
    groups: dict[str, list[str]]                 # group -> [teams]
    group_pairings: list[tuple[str, str, str]]   # (group, team_a, team_b)
    ratings: dict[str, float]
    third_table: ThirdPlaceTable


@dataclass
class TournamentResult:
    group_order: dict[str, list[str]]            # group -> [1st,2nd,3rd,4th]
    group_record: dict[str, dict]                # team -> {pts,gd,gf,results:[...]}
    qualified_third_groups: list[str]            # the 8 groups whose 3rd advanced
    annex_assignment: dict[str, str]             # winner_group -> third_group
    r32_teams: dict[int, tuple[str, str]]        # match_num -> (teamA, teamB)
    ko_winners: dict[int, str]                   # match_num -> winner team
    ko_losers: dict[int, str]                    # match_num -> loser team
    champion: str = ""


def load_context() -> TournamentContext:
    aliases = data_io.load_aliases()
    ratings = data_io.load_team_strength(aliases)
    result, _ = parse_schedule()
    groups: dict[str, list[str]] = defaultdict(list)
    pairings: list[tuple[str, str, str]] = []
    seen = defaultdict(set)
    for m in result.matches:
        if m.stage != "group":
            continue
        pairings.append((m.group, m.team_1, m.team_2))
        for t in (m.team_1, m.team_2):
            if t not in seen[m.group]:
                seen[m.group].add(t)
                groups[m.group].append(t)
    third_table = ThirdPlaceTable.load()
    return TournamentContext(dict(groups), pairings, ratings, third_table)


def _simulate_groups(ctx: TournamentContext, rng):
    record = {t: {"pts": 0, "gd": 0, "gf": 0} for g in ctx.groups for t in ctx.groups[g]}
    for g, a, b in ctx.group_pairings:
        ga, gb = match_model.simulate_group_match(ctx.ratings[a], ctx.ratings[b], rng)
        record[a]["gf"] += ga; record[a]["gd"] += ga - gb
        record[b]["gf"] += gb; record[b]["gd"] += gb - ga
        if ga > gb:
            record[a]["pts"] += 3
        elif gb > ga:
            record[b]["pts"] += 3
        else:
            record[a]["pts"] += 1; record[b]["pts"] += 1

    group_order = {}
    for g, teams in ctx.groups.items():
        ranked = sorted(teams, key=lambda t: (record[t]["pts"], record[t]["gd"],
                                              record[t]["gf"], rng.random()), reverse=True)
        group_order[g] = ranked
    return group_order, record


def _select_best_thirds(group_order, record, rng) -> list[str]:
    thirds = [(g, group_order[g][2]) for g in group_order]
    ranked = sorted(thirds, key=lambda gt: (record[gt[1]]["pts"], record[gt[1]]["gd"],
                                            record[gt[1]]["gf"], rng.random()), reverse=True)
    return [g for g, _ in ranked[:8]]


def _resolve_feeder(feeder, group_order, annex_assignment):
    kind, val = feeder
    if kind == "W":
        return group_order[val][0]
    if kind == "R":
        return group_order[val][1]
    # ('3', winner_group): the third-place team Annex C assigns to this winner.
    third_group = annex_assignment[val]
    return group_order[third_group][2]


def simulate_tournament(ctx: TournamentContext, rng) -> TournamentResult:
    group_order, record = _simulate_groups(ctx, rng)
    qual_groups = _select_best_thirds(group_order, record, rng)
    annex = ctx.third_table.assign(qual_groups)

    r32_teams, ko_winners, ko_losers = {}, {}, {}

    def play(mnum, a, b):
        a_adv = match_model.simulate_knockout(ctx.ratings[a], ctx.ratings[b], rng)
        win, lose = (a, b) if a_adv else (b, a)
        ko_winners[mnum] = win
        ko_losers[mnum] = lose
        return win

    # Round of 32
    for mnum, (f1, f2) in bracket.R32_DEFS.items():
        a = _resolve_feeder(f1, group_order, annex)
        b = _resolve_feeder(f2, group_order, annex)
        r32_teams[mnum] = (a, b)
        play(mnum, a, b)
    # Later rounds
    for defs in (bracket.R16_DEFS, bracket.QF_DEFS, bracket.SF_DEFS):
        for mnum, (m1, m2) in defs.items():
            play(mnum, ko_winners[m1], ko_winners[m2])
    # Final (match 104)
    champ = play(104, ko_winners[bracket.FINAL_FEEDERS[0]], ko_winners[bracket.FINAL_FEEDERS[1]])

    return TournamentResult(
        group_order=group_order, group_record=record,
        qualified_third_groups=qual_groups, annex_assignment=annex,
        r32_teams=r32_teams, ko_winners=ko_winners, ko_losers=ko_losers,
        champion=champ,
    )
