"""Load the official Annex C third-place allocation and expose a lookup.

Reads outputs/third_place_mapping.csv (produced by build_third_place_mapping.py
from the FIFA regulations PDF). The lookup maps a set of 8 qualifying group
letters -> {winner_group: third_group}, i.e. which group's third-placed team
each group winner faces in the Round of 32.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .bracket import WINNER_TO_MATCH

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "outputs" / "third_place_mapping.csv"


class ThirdPlaceTable:
    def __init__(self, lookup: dict[frozenset, dict[str, str]]):
        self._lookup = lookup

    @classmethod
    def load(cls, path=None) -> "ThirdPlaceTable":
        path = Path(path) if path else DEFAULT_CSV
        lookup: dict[frozenset, dict[str, str]] = {}
        with open(path, encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            # Column headers look like "M79_1A"; recover the winner group from the suffix.
            winner_cols = {c: c.split("_1")[1] for c in reader.fieldnames
                           if c not in ("option", "qualifying_groups")}
            for row in reader:
                assign = {}
                for col, winner_group in winner_cols.items():
                    third_group = row[col].lstrip("3")
                    assign[winner_group] = third_group
                key = frozenset(row["qualifying_groups"])
                lookup[key] = assign
        if len(lookup) != 495:
            raise ValueError(f"Expected 495 Annex C rows, loaded {len(lookup)}")
        return cls(lookup)

    def assign(self, qualifying_groups) -> dict[str, str]:
        """qualifying_groups: iterable of 8 group letters -> {winner_group: third_group}."""
        key = frozenset(qualifying_groups)
        if key not in self._lookup:
            raise KeyError(f"No Annex C row for qualifying groups {sorted(key)}")
        return self._lookup[key]
