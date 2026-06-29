# WC Challenge 2026 — Fantasy World Cup Backend + Site

Four owners drafted 6 national teams each. Real 2026 World Cup match results
generate fantasy points. This repo is the **data pipeline + static site**.

```
API-Football  ──fetch_results.py──►  results JSON  ──scoring.py──►  site/data/*.json  ──►  Netlify
```

The site renders JSON only — **it computes nothing**. All scoring lives in `scoring.py`,
all rules/roster live in `data/`. Update the JSON, push, and Netlify redeploys.

---

## Layout

```
data/
  scoring_config.json   scoring rules (version rebalanced_v3) — single source of truth
  draft_board.json      4 owners × 6 teams
  tiers.json            all 48 teams tiered (1=strong … 4=weak) for the upset bonus
  team_aliases.json     API/schedule spellings → canonical draft names
  fake_results.json     JOB 2 test fixtures (June 11 & 13)
scoring.py              results JSON → owner_standings / daily_results / team_table
fetch_results.py        API-Football fixtures → results JSON (+ optional goal events)
resolve_knockout_schedule.py  fills matches.csv knockout fixtures (R32→Final) with the
                        real teams once the groups finish — reuses the sim/ bracket engine
scripts/build.py        one command: (fetch) → score → write site/data
site/                   the static site (Netlify publish dir)
  index.html app.js style.css
  data/                 generated JSON the site reads
```

## Scoring rules (rebalanced_v3, config-driven)

**Group stage:** win 3 / draw 1 / loss 0. Upset bonus = `2 × (winner_tier − loser_tier)`
when a weaker (higher-number) tier beats a stronger one — so T3→T2 +2, T3→T1 +4, T4→T1 +6.
Points go only to a team's drafting owner.

**Knockout:** win 3 / loss 0 (a penalty shootout is just a win). Advancement bonus for
**reaching** a round: R16 +2, QF +5, SF +10, Final +18, Win WC +30. Third-place game
pays match points only.

## Run it

```bash
# score the committed fake results and refresh the site data
python scoring.py --results data/fake_results.json --out-dir site/data --explain

# or the one-command build
python scripts/build.py --results data/fake_results.json

# fetch live results from API-Football (league 1 = World Cup), then score
API_FOOTBALL_KEY=xxxx python scripts/build.py --fetch --season 2026
```

Serve the site locally: `python -m http.server -d site 8000` → http://localhost:8000

## Data source — API-Football (via api-sports.io)

- Base: `https://v3.football.api-sports.io` · Header: `x-apisports-key: <KEY>`
- League **1 = "World Cup"**; seasons include **2026** (fixtures not yet populated as of
  this build) and **2022** (complete — used as a proxy to validate the pipeline).
- Endpoints used: `/status`, `/leagues?search=world cup`, `/fixtures?league=1&season=YYYY`,
  `/fixtures/events?fixture=ID` (goal scorer: player, team, minute, detail — **confirmed working**).

## Deploy (Netlify)

Static site, publish dir `site/` (see `netlify.toml`). Connect this GitHub repo to
Netlify once; every push to `main` redeploys. Updating `site/data/*.json` is all it
takes to update the standings.

## Later

- `data/goals.json` (from `fetch_results.py --with-goals`) feeds the **Player Goals** section.
- A GPT key (in the workspace env) will generate Jim Rome-style recaps for the **News Feed**.
