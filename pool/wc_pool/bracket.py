"""Official 2026 knockout bracket structure (match numbers 73-104).

Sourced from the published FIFA bracket (R32 match numbering and the
R32 -> R16 -> QF -> SF -> Final tree). The third-place slots are filled from the
official Annex C table (see third_place.py). Nothing here is invented; the R32
definitions are cross-checked against the parsed schedule by `verify_against_schedule`.

Feeder encoding:
  ('W', 'A') -> winner of Group A
  ('R', 'A') -> runner-up of Group A
  ('3', 'A') -> the third-placed team Annex C assigns to Group A's winner slot
"""

from __future__ import annotations

# R32 match number -> (feeder_1, feeder_2)
R32_DEFS = {
    73: (("R", "A"), ("R", "B")),
    74: (("W", "E"), ("3", "E")),
    75: (("W", "F"), ("R", "C")),
    76: (("W", "C"), ("R", "F")),
    77: (("W", "I"), ("3", "I")),
    78: (("R", "E"), ("R", "I")),
    79: (("W", "A"), ("3", "A")),
    80: (("W", "L"), ("3", "L")),
    81: (("W", "D"), ("3", "D")),
    82: (("W", "G"), ("3", "G")),
    83: (("R", "K"), ("R", "L")),
    84: (("W", "H"), ("R", "J")),
    85: (("W", "B"), ("3", "B")),
    86: (("W", "J"), ("R", "H")),
    87: (("W", "K"), ("3", "K")),
    88: (("R", "D"), ("R", "G")),
}

# Later rounds: match number -> (feeder match 1, feeder match 2)
R16_DEFS = {89: (74, 77), 90: (73, 75), 91: (76, 78), 92: (79, 80),
            93: (83, 84), 94: (81, 82), 95: (86, 88), 96: (85, 87)}
QF_DEFS = {97: (89, 90), 98: (93, 94), 99: (91, 92), 100: (95, 96)}
SF_DEFS = {101: (97, 98), 102: (99, 100)}
FINAL_FEEDERS = (101, 102)   # match 104
THIRD_PLACE_FEEDERS = (101, 102)  # match 103, losers; non-scoring in this pool

# Which winner-group slot maps to which R32 match (the '3' feeders).
WINNER_TO_MATCH = {"A": 79, "B": 85, "D": 81, "E": 74,
                   "G": 82, "I": 77, "K": 87, "L": 80}

ROUND_NAME = {}
for _m in range(73, 89):
    ROUND_NAME[_m] = "R32"
for _m in range(89, 97):
    ROUND_NAME[_m] = "R16"
for _m in range(97, 101):
    ROUND_NAME[_m] = "QF"
for _m in (101, 102):
    ROUND_NAME[_m] = "SF"
ROUND_NAME[104] = "Final"


def verify_against_schedule(schedule_r32, eligibility) -> list[str]:
    """Cross-check the parsed schedule's 16 R32 feeder pairs against R32_DEFS.

    schedule_r32: list of (feeder_1_struct, feeder_2_struct) where each struct is
        ('winner', g) | ('runner', g) | ('third', [groups]).
    eligibility: winner-group -> set of eligible third groups.
    Returns a list of problems (empty if consistent).
    """
    problems = []

    def canon(side):
        kind, val = side
        if kind == "winner":
            return ("W", val)
        if kind == "runner":
            return ("R", val)
        return ("3", set(val))  # third: keep eligible set for matching

    # Build comparable set of schedule matchups.
    sched = []
    for f1, f2 in schedule_r32:
        sched.append(frozenset_pair(canon(f1), canon(f2)))

    official = []
    for mnum, (f1, f2) in R32_DEFS.items():
        a, b = f1, f2
        # expand a '3' feeder into ('3', eligible-set) for comparison
        a2 = ("3", eligibility[a[1]]) if a[0] == "3" else a
        b2 = ("3", eligibility[b[1]]) if b[0] == "3" else b
        official.append(frozenset_pair(a2, b2))

    # Compare as multisets via sorted repr.
    sched_keys = sorted(_pair_key(p) for p in sched)
    off_keys = sorted(_pair_key(p) for p in official)
    if sched_keys != off_keys:
        only_sched = [k for k in sched_keys if k not in off_keys]
        only_off = [k for k in off_keys if k not in sched_keys]
        problems.append(f"R32 schedule vs official mismatch. "
                        f"only_in_schedule={only_sched} only_in_official={only_off}")
    return problems


def frozenset_pair(a, b):
    return (a, b)


def _pair_key(pair):
    def k(side):
        kind, val = side
        if kind == "3":
            return ("3", "".join(sorted(val)))
        return (kind, val)
    return tuple(sorted([k(pair[0]), k(pair[1])]))
