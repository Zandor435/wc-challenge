# 2026 World Cup Pool — Scoring Calibrator

A small, self-contained, **deletable** project to calibrate the scoring rules for
a 4-person 2026 World Cup fantasy pool (Zach, Gunner, Gayden, Devin). The goal is
**not** to predict the World Cup — it is to test whether different scoring systems
keep the pool competitive and fun through the knockout rounds.

Delete this whole folder when the tournament is over. Nothing here touches your
other projects. The Python environment lives in `.venv/` inside this folder.

---

## Status: Phase 1 (inputs + parsing + bracket validation) — COMPLETE

Phase 1 deliberately stops **before** running large simulations, so the bracket
logic can be proven correct first. The scoring sweep is Phase 2.

### What is built and validated
- **Schedule parser** → `matches.csv` + `outputs/schedule_validation.txt`
  (72 group games, 12 groups, every team appears exactly 3×, all 24 drafted teams
  found, name aliases applied).
- **Official Annex C third-place table** extracted from the FIFA regulations PDF →
  `outputs/third_place_mapping.csv` (all **495** combinations, validated).
- **Two-layer bracket validation** → `outputs/bracket_validation.txt`
  (schedule ↔ official bracket consistency; structural validity; official Annex C
  allocation correctness across 20 sample sims).

---

## How to run (Phase 1)

```powershell
# from this folder; the venv already has numpy + pandas
.\.venv\Scripts\python.exe parse_schedule.py            # parse + validate schedule
.\.venv\Scripts\python.exe build_third_place_mapping.py # (re)build Annex C table from the PDF
.\.venv\Scripts\python.exe bracket_check.py             # prove the bracket is correct
```

---

## Editable inputs

| File | What it holds |
| --- | --- |
| `drafted_teams.csv` | The 4 owners × 6 teams, with draft tiers (fixed). |
| `team_tiers.csv` | Every team's tier. Drafted teams fixed; non-drafted left **blank** and flagged (`NEEDS_TIER`) — must be filled before the scoring run. |
| `team_strength.csv` | One `strength_rating` per team. **Provisional** (Mode A) values now; paste real Elo / betting-market ratings later (Mode B) — no scraping. |
| `team_aliases.csv` | Name normalization (e.g. `Korea Republic` → `South Korea`, `Curaçao` → `Curacao`). |
| `scoring_config.json` | All scoring rules + editable guardrail thresholds. |
| `schedule.txt` | The real fixture list (copied from your upload). |

## Data sources (official, not invented)
- **Schedule:** your uploaded fixture list.
- **Bracket tree (matches 73–104):** the published FIFA 2026 bracket structure.
- **Third-place allocation:** Annex C of the *Regulations for the FIFA World Cup 26™*
  (`reference/FWC2026_regulations_EN.pdf`), enumerating all 495 combinations.

## Match model (Phase 1, intentionally simple)
- Group match: Elo-style Win/Draw/Loss from the rating difference, with a draw
  probability that shrinks as the gap grows.
- Knockout match: no draw — a single strength-adjusted win probability (a penalty
  shootout is just a knockout win, folded in).
- Goals are only a light tiebreak heuristic (no Poisson); ties ultimately break
  randomly. See `wc_pool/match_model.py`.

## Deferred to Phase 2 (not built yet)
5,000-sim scoring run, the candidate-scoring grid, stage-by-stage competitiveness,
Tier-4 / upset / knockout diagnostics, charts, and the Simple / Balanced / Chaos
recommendations.

## Code layout
```
wc_pool/
  data_io.py          load + normalize the CSV/JSON inputs
  schedule_parser.py  schedule.txt -> structured matches + validation
  match_model.py      strength -> match outcomes
  bracket.py          official R32-Final tree + schedule cross-check
  third_place.py      load Annex C, expose the allocation lookup
  tournament.py       simulate one tournament (groups -> champion)
parse_schedule.py            run parser + write schedule_validation.txt
build_third_place_mapping.py extract Annex C from the PDF
bracket_check.py             two-layer bracket validation (20 sims)
```
