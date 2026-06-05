#!/usr/bin/env python3
"""Generate the WC Challenge commentary via the OpenAI API.

Same pattern as fetch_results.py: stdlib only (urllib), reads the committed
JSON, calls an HTTP API, writes the output.

Two products, both written here:

1. Pundit Roundtable (STATELESS) -> commentary.json
   Calls GPT four times — once per pundit, each character bio as the system
   prompt and standings/results/rosters as the user message. Regenerated cold
   every run. Written to the repo root AND site/data/commentary.json.

2. Jim Rome's tournament column (STATEFUL) -> site/data/tournament_recap.md
   Reads narrative_state.json (the structured accumulator built upstream by
   build_narrative_state.py) AND the previous tournament_recap.md, feeds both to
   GPT alongside today's results, and writes the next installment — overwriting
   the file. The MEMORY accumulates in narrative_state.json; the recap is always
   just the latest column, building on the one before it.

Pipeline position: runs AFTER build_narrative_state.py (so the narrative context
is fresh). Both outputs are committed by the GitHub Action.

Usage:
    OPENAI_API_KEY=sk-... python generate_commentary.py
    python generate_commentary.py --placeholder      # no API call; writes stubs
    python generate_commentary.py --recap-only        # only tournament_recap.md
    python generate_commentary.py --skip-recap        # only commentary.json
    python generate_commentary.py --model gpt-4o --max-tokens 260

Cost: 5 calls/run (4 pundits + 1 column). ~150 calls over the tournament —
pocket change on GPT-4o.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "site", "data")
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

# Fixed draft board, pasted into every prompt (matches data/draft_board.json).
ROSTERS = """- Zach: Brazil (T1) | Switzerland, Austria (T2) | Ghana, Czechia (T3) | Saudi Arabia (T4)
- Gunner: France (T1) | Senegal, Morocco (T2) | Egypt, Canada (T3) | DR Congo (T4)
- Gayden: England (T1) | Japan, Ivory Coast (T2) | Korea Republic, Turkey (T3) | Jordan (T4)
- Devin: Spain (T1) | USA, Norway (T2) | South Africa, Bosnia (T3) | Haiti (T4)"""

USER_TEMPLATE = """Here are the current WC Challenge fantasy pool standings and recent results.

STANDINGS:
{standings}

RECENT RESULTS:
{results}

ROSTERS:
{rosters}

Give your hot take on the current state of this fantasy pool. These four managers \
are personally responsible for their results — blame THEM, not the players or luck. \
Make up specific, funny reasons why their draft picks were bad decisions (didn't do \
research, picked based on jersey colors, panicked, got emotional, watched one YouTube \
highlight, etc.). Roast at least two managers by name. Reference specific teams, point \
totals, and upcoming matches. Stay in character."""

# Shared rules prepended to every pundit's system prompt (from the spec).
SHARED_RULES = """ALL PUNDITS FOLLOW THESE RULES:
- The four owners (Zach, Gunner, Gayden, Devin) are referred to as "managers." They are personally responsible for everything — good and bad.
- When a team loses or underperforms, it is ALWAYS the manager's fault. Never bad luck, never the players. The manager made a terrible pick, didn't do their homework, got emotional on draft night, panicked, or is simply not smart enough.
- Invent specific reasons for the blame (e.g. "Zach clearly didn't watch a single Ghana qualifier," "Devin picked Bosnia because he liked the jersey").
- Insult the managers directly and personally. Question their intelligence, preparation, commitment, soccer knowledge, and decision-making.
- When a manager is winning, credit luck, not skill — or grudgingly admit it while still finding something to roast.
- Use the managers' first names. Make it personal. This is a roast dressed up as analysis.
"""

# name, tone, left-accent color (one per pundit), and the character system prompt.
PUNDITS = [
    {
        "name": "Eric Wynalda",
        "tone": "arrogant",
        "color": "#e2231a",
        "system": (
            "You are Eric Wynalda providing commentary on a fantasy World Cup pool between four "
            "managers: Zach, Gunner, Gayden, and Devin. Each drafted 6 national teams across 4 tiers.\n\n"
            "Your voice: You are the most arrogant man in American soccer. You played in a World Cup. "
            "You've been in locker rooms. These four managers have not. You frame every opinion as a "
            "verdict. You reference your own career to prove why you'd have drafted better. You use short, "
            "blunt dismissals (\"You're wrong.\" \"That's not how this works.\" \"This is a joke.\") followed "
            "by sweeping conclusions. Pick ONE manager to crown as the only competent one and treat the rest "
            "as clueless amateurs who embarrassed themselves on draft night. Make up a specific reason why "
            "the worst manager's picks were idiotic — question whether they even watch soccer or just picked "
            "names they recognized from FIFA video games. Be condescending. Be cutting. Be specific.\n\n"
            "Length: 3-4 sentences. Punchy. At least one direct insult to a manager by name."
        ),
    },
    {
        "name": "Landon Donovan",
        "tone": "hedging",
        "color": "#2f6dff",
        "system": (
            "You are Landon Donovan providing commentary on a fantasy World Cup pool between four managers: "
            "Zach, Gunner, Gayden, and Devin. Each drafted 6 national teams across 4 tiers.\n\n"
            "Your voice: Painfully measured, conflict-averse, but somehow still devastating. You start with "
            "\"I think\" or \"Look, I get why people say...\" and then deliver a backhanded insult wrapped in "
            "empathy. You defend the losing manager but in a way that makes them sound even more pathetic — "
            "\"I feel for Devin, I really do, but when you draft Haiti in the fourth round, I mean... what "
            "conversation were you having with yourself?\" You hedge, you qualify, but your hedging IS the "
            "insult. You always find a way to suggest the leading manager got lucky and the trailing manager "
            "made bad decisions but \"means well.\" You sound like a therapist gently telling someone they're "
            "a failure.\n\nLength: 3-4 sentences. Backhanded empathy. At least one manager gets the "
            "\"bless his heart\" treatment."
        ),
    },
    {
        "name": "Clint Dempsey",
        "tone": "chill",
        "color": "#28c060",
        "system": (
            "You are Clint Dempsey providing commentary on a fantasy World Cup pool between four managers: "
            "Zach, Gunner, Gayden, and Devin. Each drafted 6 national teams across 4 tiers.\n\n"
            "Your voice: Laid-back Texas drawl. You talk like you're still in the locker room roasting your "
            "boys. Drop your g's — \"gonna,\" \"tryin',\" \"puttin'.\" Use player slang: \"grind,\" \"put in "
            "work,\" \"gettin' cooked,\" \"back yourself,\" \"that's tough.\" You clown the losing managers "
            "like a teammate would — no mercy but with love. Make up funny reasons for their bad picks: "
            "\"Gunner out here draftin' DR Congo like he got insider info from his barber.\" \"Zach picked "
            "Saudi Arabia 'cause he thought oil money meant goals.\" Hype whoever's winning like they just "
            "scored a banger. Roast whoever's losing like they missed an open net.\n\n"
            "Length: 2-3 sentences. Casual. At least one made-up roast per take."
        ),
    },
    {
        "name": "Alexi Lalas",
        "tone": "bombastic",
        "color": "#f4a423",
        "system": (
            "You are Alexi Lalas providing commentary on a fantasy World Cup pool between four managers: "
            "Zach, Gunner, Gayden, and Devin. Each drafted 6 national teams across 4 tiers.\n\n"
            "Your voice: The loudest, most bombastic man in American soccer media. Every take is a "
            "declaration about what these managers' failures SAY ABOUT THEM AS PEOPLE. You scold managers "
            "directly by name — \"Devin, look at me. LOOK AT ME. You drafted Bosnia.\" You question their "
            "preparation, their courage, their understanding of the beautiful game. You reference toughness, "
            "grit, and the 1994 World Cup era to explain why you'd have drafted differently. You frame last "
            "place as a moral failure, not bad luck. You frame first place as barely adequate. Nobody is "
            "safe. You enjoy making these four grown men feel small about their fantasy soccer picks.\n\n"
            "Length: 3-4 sentences. Direct address. Scolding. At least one \"look at me\" or \"let me tell "
            "you something\" moment. End with a mic-drop line."
        ),
    },
]

PLACEHOLDER_TAKE = "Pundits are warming up..."

# --------------------------------------------------------------------------- #
# Stateful narrative: Jim Rome's rolling tournament column.
#
# Unlike the pundit takes (regenerated cold each run), this column BUILDS on
# itself. The accumulating memory lives in narrative_state.json (written by
# build_narrative_state.py upstream); tournament_recap.md is always just the
# latest installment, and we feed the previous installment back in as context so
# the story escalates instead of resetting.
# --------------------------------------------------------------------------- #
RECAP_PATH = os.path.join(DATA, "tournament_recap.md")
NARRATIVE_STATE_PATH = os.path.join(DATA, "narrative_state.json")

JIM_ROME_SYSTEM = (
    "You are Jim Rome covering the WC Challenge. Here is your previous column. "
    "Here are today's results, updated standings, and narrative context including "
    "streaks, themes, and notable events. Write the next installment. Build on "
    "running themes — escalate what's working, drop what's gone stale. "
    "Reference specific results. Be opinionated about each owner's trajectory. "
    "Keep it to 200-300 words."
)

RECAP_USER_TEMPLATE = """PREVIOUS COLUMN (your last installment):
{previous}

NARRATIVE CONTEXT (structured state — ranks, records, streaks, win probabilities, \
head-to-head, notable events, running themes, tournament phase):
{state}

UPDATED STANDINGS:
{standings}

TODAY'S RESULTS:
{today}

ROSTERS:
{rosters}

Write the next installment of your column now. Build on the running themes above, \
reference specific results and point totals, and stay opinionated about each \
manager's trajectory. Output the column body only — no title, no byline."""

PLACEHOLDER_RECAP = (
    "_Jim Rome's column drops once the next slate of matches is in the books._\n"
)


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def call_openai(api_key, model, system, user, max_tokens, temperature):
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")
    req = urllib.request.Request(
        OPENAI_URL, data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    return data["choices"][0]["message"]["content"].strip()


def write_outputs(doc):
    """Write commentary.json to repo root (per spec) and site/data (for the site)."""
    paths = [os.path.join(HERE, "commentary.json"),
             os.path.join(DATA, "commentary.json")]
    for p in paths:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False)
    print("Wrote commentary.json ->", " and ".join(paths))


def read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


def latest_day(daily):
    """The most recent day's match block, or None pre-tournament."""
    if daily and daily.get("days"):
        return daily["days"][-1]
    return None


def write_recap(text):
    """Overwrite tournament_recap.md with the latest installment (site/data only)."""
    os.makedirs(os.path.dirname(RECAP_PATH), exist_ok=True)
    with open(RECAP_PATH, "w", encoding="utf-8") as f:
        f.write(text.rstrip() + "\n")
    print("Wrote tournament_recap.md ->", RECAP_PATH)


def generate_pundits(args, api_key, standings, daily):
    """The original cold-take roundtable -> list of pundit dicts for commentary.json."""
    if args.placeholder:
        return [{"name": p["name"], "take": PLACEHOLDER_TAKE,
                 "tone": p["tone"], "color": p["color"]} for p in PUNDITS]

    user = USER_TEMPLATE.format(
        standings=json.dumps(standings, indent=2) if standings else "(no standings yet)",
        results=json.dumps(daily, indent=2) if daily else "(no results yet)",
        rosters=ROSTERS,
    )
    out = []
    for p in PUNDITS:
        try:
            take = call_openai(api_key, args.model, p["system"], user,
                               args.max_tokens, args.temperature)
        except urllib.error.HTTPError as e:
            print(f"  {p['name']}: API error {e.code} — using placeholder", file=sys.stderr)
            take = PLACEHOLDER_TAKE
        except Exception as e:  # noqa: BLE001 - keep generating the rest
            print(f"  {p['name']}: {e} — using placeholder", file=sys.stderr)
            take = PLACEHOLDER_TAKE
        out.append({"name": p["name"], "take": take, "tone": p["tone"], "color": p["color"]})
        print(f"  {p['name']}: {take[:70]}...")
    return out


def generate_recap(args, api_key, standings, daily, narrative, previous):
    """Jim Rome's next installment, built on the previous column + narrative state."""
    if args.placeholder:
        return PLACEHOLDER_RECAP

    today = latest_day(daily)
    user = RECAP_USER_TEMPLATE.format(
        previous=previous or "(none yet — this is your preseason preview, written before any matches are played)",
        state=json.dumps(narrative, indent=2) if narrative else "(no narrative state available)",
        standings=json.dumps(standings, indent=2) if standings else "(no standings yet)",
        today=json.dumps(today, indent=2) if today else "(no matches played yet — preseason)",
        rosters=ROSTERS,
    )
    try:
        return call_openai(api_key, args.model, JIM_ROME_SYSTEM, user,
                           args.recap_max_tokens, args.temperature)
    except urllib.error.HTTPError as e:
        print(f"  Jim Rome recap: API error {e.code} — keeping previous column", file=sys.stderr)
    except Exception as e:  # noqa: BLE001
        print(f"  Jim Rome recap: {e} — keeping previous column", file=sys.stderr)
    # On failure, never clobber a good column with a stub: keep what we had.
    return previous or PLACEHOLDER_RECAP


def main():
    ap = argparse.ArgumentParser(description="Generate Pundit Roundtable + Jim Rome narrative")
    ap.add_argument("--model", default="gpt-4o")
    ap.add_argument("--max-tokens", type=int, default=260, help="max tokens per pundit take")
    ap.add_argument("--recap-max-tokens", type=int, default=600,
                    help="max tokens for the Jim Rome rolling column (~200-300 words)")
    ap.add_argument("--temperature", type=float, default=0.9)
    ap.add_argument("--placeholder", action="store_true",
                    help="write 'warming up' stubs without calling the API")
    ap.add_argument("--recap-only", action="store_true",
                    help="only (re)generate tournament_recap.md; leave commentary.json untouched")
    ap.add_argument("--skip-recap", action="store_true",
                    help="only generate the pundit commentary.json; leave tournament_recap.md untouched")
    ap.add_argument("--generated", default=None,
                    help="ISO timestamp for the 'generated' field (CI passes one; avoids nondeterminism)")
    args = ap.parse_args()

    standings = load_json(os.path.join(DATA, "owner_standings.json"))
    daily = load_json(os.path.join(DATA, "daily_results.json"))
    # Stateful context: the structured accumulator + the previous installment.
    narrative = load_json(NARRATIVE_STATE_PATH)
    previous_recap = read_text(RECAP_PATH)

    # derive a source label from the data (falls back gracefully)
    src = "unknown"
    if daily and daily.get("days"):
        src = f"daily_results_{daily['days'][-1]['date']}"
    elif standings:
        src = standings.get("source", "unknown")

    do_pundits = not args.recap_only
    do_recap = not args.skip_recap

    # Only the live (non-placeholder) path needs the API key.
    api_key = None
    if not args.placeholder and (do_pundits or do_recap):
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            print("ERROR: OPENAI_API_KEY not set. Use --placeholder to write stubs instead.",
                  file=sys.stderr)
            sys.exit(1)

    if do_pundits:
        pundits_out = generate_pundits(args, api_key, standings, daily)
        write_outputs({
            "generated": args.generated or "",
            "source": src,
            "pundits": pundits_out,
        })

    if do_recap:
        recap = generate_recap(args, api_key, standings, daily, narrative, previous_recap)
        write_recap(recap)
        print(f"  Jim Rome: {recap.strip()[:70]}...")


if __name__ == "__main__":
    main()
