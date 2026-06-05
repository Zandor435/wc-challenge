#!/usr/bin/env python3
"""Generate the WC Challenge "Pundit Roundtable" commentary via the OpenAI API.

Same pattern as fetch_results.py: stdlib only (urllib), reads the committed
JSON, calls an HTTP API, writes a JSON output.

It reads owner_standings.json + daily_results.json (the scoring outputs), then
calls GPT-4o four times — once per pundit, using each character bio as the system
prompt and the standings/results/rosters as the user message — and writes
commentary.json.

The site is served from site/, so the file is written BOTH to the repo root
(per the spec) and to site/data/commentary.json (so the static site can fetch it
at data/commentary.json). Both are committed by the GitHub Action.

Usage:
    OPENAI_API_KEY=sk-... python generate_commentary.py
    python generate_commentary.py --placeholder    # no API call; writes warming-up takes
    python generate_commentary.py --model gpt-4o --max-tokens 260

Cost: 4 calls/run. ~120 calls over the tournament — pocket change on GPT-4o.
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


def main():
    ap = argparse.ArgumentParser(description="Generate Pundit Roundtable commentary")
    ap.add_argument("--model", default="gpt-4o")
    ap.add_argument("--max-tokens", type=int, default=260)
    ap.add_argument("--temperature", type=float, default=0.9)
    ap.add_argument("--placeholder", action="store_true",
                    help="write 'warming up' placeholder takes without calling the API")
    ap.add_argument("--generated", default=None,
                    help="ISO timestamp for the 'generated' field (CI passes one; avoids nondeterminism)")
    args = ap.parse_args()

    standings = load_json(os.path.join(DATA, "owner_standings.json"))
    daily = load_json(os.path.join(DATA, "daily_results.json"))

    # derive a source label from the data (falls back gracefully)
    src = "unknown"
    if daily and daily.get("days"):
        src = f"daily_results_{daily['days'][-1]['date']}"
    elif standings:
        src = standings.get("source", "unknown")

    pundits_out = []

    if args.placeholder:
        for p in PUNDITS:
            pundits_out.append({"name": p["name"], "take": PLACEHOLDER_TAKE,
                                "tone": p["tone"], "color": p["color"]})
    else:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            print("ERROR: OPENAI_API_KEY not set. Use --placeholder to write a stub instead.",
                  file=sys.stderr)
            sys.exit(1)
        user = USER_TEMPLATE.format(
            standings=json.dumps(standings, indent=2) if standings else "(no standings yet)",
            results=json.dumps(daily, indent=2) if daily else "(no results yet)",
            rosters=ROSTERS,
        )
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
            pundits_out.append({"name": p["name"], "take": take,
                                "tone": p["tone"], "color": p["color"]})
            print(f"  {p['name']}: {take[:70]}...")

    doc = {
        "generated": args.generated or "",
        "source": src,
        "pundits": pundits_out,
    }
    write_outputs(doc)


if __name__ == "__main__":
    main()
