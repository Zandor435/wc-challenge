"""Loading and normalization of the editable input files.

Everything that reads a CSV/JSON from disk lives here so the rest of the
package works with clean Python objects. Team-name normalization (aliases +
diacritic handling) is centralized in `normalize_team` so the parser, the
tier table, and the strength table all agree on canonical names.
"""

from __future__ import annotations

import csv
import json
import os
import unicodedata
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _strip_accents(text: str) -> str:
    """Curaçao -> Curacao. Used as a last-resort normalizer for matching."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def load_aliases(path: str | os.PathLike | None = None) -> dict[str, str]:
    """alias -> canonical. Lower-cased keys for case-insensitive lookup."""
    path = Path(path) if path else PROJECT_ROOT / "team_aliases.csv"
    aliases: dict[str, str] = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            alias = row["alias"].strip()
            canonical = row["canonical"].strip()
            aliases[alias.lower()] = canonical
    return aliases


def normalize_team(name: str, aliases: dict[str, str]) -> str:
    """Canonicalize a raw team name from any source.

    Order: trim -> exact alias -> accent-stripped alias -> accent-stripped raw.
    Note we keep the canonical spelling exactly as the alias file specifies
    (e.g. 'Türkiye' is preserved because it is not aliased away).
    """
    raw = " ".join(name.split()).strip().strip(",.")
    if raw.lower() in aliases:
        return aliases[raw.lower()]
    stripped = _strip_accents(raw)
    if stripped.lower() in aliases:
        return aliases[stripped.lower()]
    # If a canonical name itself is the accent-stripped form, prefer that.
    for canonical in set(aliases.values()):
        if _strip_accents(canonical).lower() == stripped.lower():
            return canonical
    return raw


def load_drafted_teams(aliases: dict[str, str], path=None) -> list[dict]:
    path = Path(path) if path else PROJECT_ROOT / "drafted_teams.csv"
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rows.append({
                "owner": row["owner"].strip(),
                "tier": int(row["tier"]),
                "team": normalize_team(row["team"], aliases),
                "team_raw": row["team"].strip(),
            })
    return rows


def load_team_tiers(aliases: dict[str, str], path=None) -> dict[str, dict]:
    """canonical team -> {tier (int or None), source}."""
    path = Path(path) if path else PROJECT_ROOT / "team_tiers.csv"
    tiers: dict[str, dict] = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            team = normalize_team(row["team"], aliases)
            tier_raw = row.get("tier", "").strip()
            tiers[team] = {
                "tier": int(tier_raw) if tier_raw else None,
                "source": row.get("source", "").strip(),
            }
    return tiers


def load_team_strength(aliases: dict[str, str], path=None) -> dict[str, float]:
    path = Path(path) if path else PROJECT_ROOT / "team_strength.csv"
    strength: dict[str, float] = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            team = normalize_team(row["team"], aliases)
            strength[team] = float(row["strength_rating"])
    return strength


def load_scoring_config(path=None) -> dict:
    path = Path(path) if path else PROJECT_ROOT / "scoring_config.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_full_tiers(aliases: dict[str, str], path=None) -> dict[str, int]:
    """Universal tier table for ALL 48 teams -> canonical team -> tier (int).

    Reads tiers.csv (columns: team,tier). Names are normalized through the same
    alias map as everything else, so draft-style names (Turkey, USA, Bosnia,
    Korea Republic) resolve to the canonical schedule spellings used by the sim.
    Used for the upset bonus, which compares the tiers of any two opponents.
    """
    path = Path(path) if path else PROJECT_ROOT / "tiers.csv"
    tiers: dict[str, int] = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            team = normalize_team(row["team"], aliases)
            tiers[team] = int(row["tier"])
    return tiers
