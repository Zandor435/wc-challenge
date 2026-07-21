"""Scratch analysis: do the 8 third-place slot eligibility lists force a UNIQUE
assignment for every one of the C(12,8)=495 qualifying-group combinations?

Slot eligibility comes straight from the schedule feeder text (the union of
groups whose third-placed team can ever occupy that slot). If every combination
admits exactly one perfect matching, then the eligibility lists ARE the official
Annex C allocation (FIFA's choice must also be a valid matching, and uniqueness
leaves no other option). If some combos admit >1, we need Annex C's tiebreaks.
"""
from itertools import combinations, permutations

GROUPS = list("ABCDEFGHIJKL")

# slot -> set of eligible third-place groups (from schedule feeders / R32 map)
SLOTS = {
    "M74": set("ABCDF"),   # 1E vs 3rd
    "M77": set("CDFGH"),   # 1I vs 3rd
    "M79": set("CEFHI"),   # 1A vs 3rd
    "M80": set("EHIJK"),   # 1L vs 3rd
    "M81": set("BEFIJ"),   # 1D vs 3rd
    "M82": set("AEHIJ"),   # 1G vs 3rd
    "M85": set("EFGIJ"),   # 1B vs 3rd
    "M87": set("DEIJL"),   # 1K vs 3rd
}
SLOT_ORDER = ["M74", "M77", "M79", "M80", "M81", "M82", "M85", "M87"]


def count_matchings(qualifying):
    """Count perfect matchings (slot<-group) for a set of 8 qualifying groups."""
    elig = [SLOTS[s] for s in SLOT_ORDER]
    count = 0
    example = None
    # Backtracking over slots, assigning a distinct qualifying group to each.
    def bt(i, used, acc):
        nonlocal count, example
        if i == 8:
            count += 1
            if example is None:
                example = dict(acc)
            return
        for g in qualifying:
            if g not in used and g in elig[i]:
                used.add(g)
                acc.append((SLOT_ORDER[i], g))
                bt(i + 1, used, acc)
                acc.pop()
                used.remove(g)
    bt(0, set(), [])
    return count, example


def main():
    total = 0
    zero = []
    unique = 0
    multi = []
    for qual in combinations(GROUPS, 8):
        total += 1
        n, ex = count_matchings(set(qual))
        if n == 0:
            zero.append(qual)
        elif n == 1:
            unique += 1
        else:
            multi.append((qual, n))
    print(f"Total combinations (expect 495): {total}")
    print(f"  unique matching   : {unique}")
    print(f"  multiple matchings: {len(multi)}")
    print(f"  ZERO matchings    : {len(zero)}")
    if zero:
        print("  *** combos with no valid assignment (schedule/eligibility bug?):")
        for z in zero[:10]:
            print("     ", "".join(z))
    if multi:
        print(f"  Example ambiguous combos (first 10 of {len(multi)}):")
        for q, n in multi[:10]:
            print(f"     {''.join(q)} -> {n} matchings")


if __name__ == "__main__":
    main()
