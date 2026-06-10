#!/usr/bin/env python3
"""Generate the WC Challenge commentary via the OpenAI API.

Same pattern as fetch_results.py: stdlib only (urllib), reads the committed
JSON, calls an HTTP API, writes the output.

Two products, both written here:

1. Today's Pundit (STATELESS) -> commentary.json
   ONE take per run from the day's rotating voice (Wynalda -> Donovan -> Dempsey
   -> Lalas -> repeat, indexed by the matchday). The character bio is the system
   prompt; the user message carries today's head-to-head matchups (drafted teams
   that face each other, cross-referenced from data/matches.csv), the standings,
   and the owner WWE personas. Regenerated cold every run. Written to the repo
   root AND site/data/commentary.json as {"pundit": {...}}.

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
    python generate_commentary.py --date 2026-06-13   # force the pundit's "today"
    python generate_commentary.py --model gpt-4o --max-tokens 500

Cost: 2 calls/run (1 pundit + 1 column). ~150 calls over the tournament —
pocket change on GPT-4o.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "site", "data")
MATCHES_CSV = os.path.join(HERE, "data", "matches.csv")
OPENAI_URL = "https://api.openai.com/v1/chat/completions"

# Owner -> WWE ring name (matches site/bios.html). Used so the pundit trash-talks
# managers by their persona but cites stats under their real names.
WWE_NAMES = {
    "Zach": "Mustard Boy",
    "Gunner": "Bubba G",
    "Gayden": "The Backpass Assassin",
    "Devin": "Ghost Pepper",
    "Rafe": "The Noisemaker",
}

# Fixed draft board, pasted into every prompt (matches data/draft_board.json).
ROSTERS = """- Zach: Brazil (T1) | Switzerland, Austria (T2) | Ghana, Czechia (T3) | Saudi Arabia (T4)
- Gunner: France (T1) | Senegal, Morocco (T2) | Egypt, Canada (T3) | DR Congo (T4)
- Gayden: England (T1) | Japan, Ivory Coast (T2) | Korea Republic, Turkey (T3) | Jordan (T4)
- Devin: Spain (T1) | USA, Norway (T2) | South Africa, Bosnia (T3) | Haiti (T4)
- Rafe: Germany (T1) | Ecuador, Mexico (T2) | Tunisia, Scotland (T3) | Uzbekistan (T4)"""

SINGLE_PUNDIT_USER = """You are writing TODAY'S single pundit column for the WC Challenge fantasy World Cup pool.

TODAY: {date}  (rotation #{rotation})
THE VOICE TODAY IS YOU: {pundit_name} — personality: {personality}

THE FIVE MANAGERS — use their WWE ring names when trash-talking, real names for stats:
- Zach — "Mustard Boy"
- Gunner — "Bubba G"
- Gayden — "The Backpass Assassin"
- Devin — "Ghost Pepper"
- Rafe — "The Noisemaker" (Gayden's 15-year-old son; knows nothing about soccer, pure chaos — his first three draft picks didn't even qualify)

CURRENT STANDINGS:
{standings}

ROSTERS (each manager drafted 6 national teams across 4 tiers):
{rosters}

{matchup_block}

{task}

TONE RULES:
- 80% roast, 20% real context. One sentence of actual team form/context as setup, then the knife.
- State every prediction with absurd, unearned confidence.
- Trash-talk managers by their WWE ring names; use real first names when citing stats or standings.
- Stay fully in character as {pundit_name} ({personality}).
- Write ONE flowing take. Lead with your single hardest-hitting sentence (it becomes the bolded headline). \
No title, no byline, no bullet points — just the column body."""

# task lines swapped in depending on whether any owner-vs-owner games are on today
TASK_H2H = (
    "For EACH head-to-head matchup above, write 3-4 sentences: one line of real team "
    "form/context as setup, then the roast connecting it to that manager's draft logic and "
    "persona, then a prediction stated with absurd confidence."
)
TASK_TEMP = (
    "There are no head-to-head matchups today, so deliver a LEAGUE TEMPERATURE CHECK instead: "
    "who's rising, who's cooked, who should be worried. Roast at least two managers by name and "
    "reference real standings and point totals."
)

# Shared rules prepended to every pundit's system prompt (from the spec).
SHARED_RULES = """ALL PUNDITS FOLLOW THESE RULES:
- The five owners (Zach, Gunner, Gayden, Devin, Rafe) are referred to as "managers." They are personally responsible for everything — good and bad.
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
            "You are Eric Wynalda providing commentary on a fantasy World Cup pool between five "
            "managers: Zach, Gunner, Gayden, Devin, and Rafe. Each drafted 6 national teams across 4 tiers.\n\n"
            "Your voice: You are the most arrogant man in American soccer. You played in a World Cup. "
            "You've been in locker rooms. These five managers have not. You frame every opinion as a "
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
            "You are Landon Donovan providing commentary on a fantasy World Cup pool between five managers: "
            "Zach, Gunner, Gayden, Devin, and Rafe. Each drafted 6 national teams across 4 tiers.\n\n"
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
            "You are Clint Dempsey providing commentary on a fantasy World Cup pool between five managers: "
            "Zach, Gunner, Gayden, Devin, and Rafe. Each drafted 6 national teams across 4 tiers.\n\n"
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
            "You are Alexi Lalas providing commentary on a fantasy World Cup pool between five managers: "
            "Zach, Gunner, Gayden, Devin, and Rafe. Each drafted 6 national teams across 4 tiers.\n\n"
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

# --------------------------------------------------------------------------- #
# Standing rivalries + season storylines fed to the stateful Rome voices (the
# rolling column AND the analytics pull-quotes). The FRAMING is authored and
# season-long; the NUMBERS are not. Every percentage/rank is pulled live from
# narrative_state.json at generation time (build_storylines below) so the threads
# never cite a stale projection — the win/champ odds move every run and the prose
# moves with them. Update the framing when the cast or the storylines change
# (e.g. when the 5th owner Rafe arrived); the numbers take care of themselves.
# --------------------------------------------------------------------------- #
RIVALRIES = """STANDING RIVALRIES (weave these in; the marquee one is brand new):
- Gayden ("The Backpass Assassin") vs. Rafe ("The Noisemaker") — FATHER vs. SON.
  This is THE marquee storyline. Rafe is Gayden's own 15-year-old kid, who walked
  straight into his father's league and is gunning for him. Thanksgiving-dinner
  implications. The Backpass Assassin built a monster in his own house and now has to
  share a table with it all summer. Hammer this constantly."""


def _pct(x):
    """Format a 0..1 probability as 'NN.N%', or '—' if missing/unparseable."""
    try:
        return f"{float(x) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _ordinal(n):
    """1 -> '1st', 2 -> '2nd', ... (or '—' if not an int)."""
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "—"
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# Used verbatim when narrative_state.json is missing/empty (no live numbers to read).
STORYLINES_FALLBACK = """SEASON STORYLINES (the threads to draw from and escalate):
- Rafe is the youngest owner (15), knows nothing about soccer, yet the sim still has him
  competitive. The chaos agent refuses to be a punchline.
- Rafe's draft-disaster origin is evergreen comedy: his first three picks — Nigeria,
  Poland, Jamaica — NONE qualified for the World Cup. He got told, shrugged, drafted six more.
- Gayden now fights a TWO-FRONT war: his existing rivalries AND his own son inside his
  own league.
- Gunner ("Bubba G") slipped from runaway favorite the instant Rafe crashed the league —
  the favorite got less favorite, and a teenager who can't name a single player did it to him.
- Devin ("Ghost Pepper") carries heavy champion odds despite trailing on win probability —
  he might own the trophy but lose the league."""


def build_storylines(narrative):
    """Render the season storylines with LIVE numbers from narrative_state.json.

    The beats are authored; every percentage and rank is read from the current
    state at generation time so Rome never cites a stale projection. The 'best
    champion %' beat is guarded against the state file's 2-dp rounding by checking
    Devin against the actual max in state (a co-leader still satisfies 'best').
    Falls back to the authored, number-free copy if the state is unavailable."""
    owners = (narrative or {}).get("owners", {})
    if not owners:
        return STORYLINES_FALLBACK

    rafe = owners.get("Rafe", {})
    gunner = owners.get("Gunner", {})
    devin = owners.get("Devin", {})

    rafe_win, rafe_champ = _pct(rafe.get("win_probability")), _pct(rafe.get("champion_probability"))
    gunner_win = _pct(gunner.get("win_probability"))
    devin_champ = _pct(devin.get("champion_probability"))

    # "2nd in win %" is about the projection, not points — rank owners by win prob.
    win_order = sorted(owners, key=lambda o: -(owners[o].get("win_probability") or 0))
    devin_win_rank = _ordinal(win_order.index("Devin") + 1) if "Devin" in win_order else "—"

    # Guard the superlative against 2-dp ties: Devin need only match the max champ odds.
    champ_vals = [b.get("champion_probability") for b in owners.values()
                  if b.get("champion_probability") is not None]
    dc = devin.get("champion_probability")
    devin_best = bool(champ_vals) and dc is not None and dc == max(champ_vals)
    devin_phrase = "owns the best champion odds" if devin_best else "carries heavy champion odds"

    return f"""SEASON STORYLINES (the threads to draw from and escalate; the numbers below are LIVE from the current model state):
- Rafe is the youngest owner (15), knows nothing about soccer, and the sim STILL has him
  competitive at {rafe_win} win / {rafe_champ} champion. The chaos agent refuses to be a punchline.
- Rafe's draft-disaster origin is evergreen comedy: his first three picks — Nigeria,
  Poland, Jamaica — NONE qualified for the World Cup. He got told, shrugged, drafted six more.
- Gayden now fights a TWO-FRONT war: his existing rivalries AND his own son inside his
  own league.
- Gunner ("Bubba G") is the model's favorite at {gunner_win} to win it all — but he slipped
  the instant Rafe crashed the league. The favorite got less favorite, and a teenager who
  can't name a single player did it to him.
- Devin ("Ghost Pepper") {devin_phrase} ({devin_champ}) despite sitting {devin_win_rank} in
  win probability — he might own the trophy but lose the league."""

JIM_ROME_SYSTEM = (
    "You are Jim Rome covering the WC Challenge. Here is your previous column. "
    "Here are today's results, updated standings, and narrative context including "
    "streaks, themes, and notable events. Write the next installment. Build on "
    "running themes — escalate what's working, drop what's gone stale. "
    "You are ALSO given a set of standing rivalries and season storylines — weave them "
    "in and escalate them, above all the father-vs-son blood feud between Gayden and his "
    "15-year-old son Rafe. "
    "Reference specific results. Be opinionated about each owner's trajectory. "
    "Keep it to 200-300 words."
)

RECAP_USER_TEMPLATE = """PREVIOUS COLUMN (your last installment):
{previous}

{rivalries}

{storylines}

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

# --------------------------------------------------------------------------- #
# Analytics pull-quotes: four one-liner roasts, one per analytics-page section,
# written to commentary.json as `rome_analytics_quotes` (ordered:
# Scoreboard, Draft Report Card, Rivalries, The Model). The analytics page reads
# this array to fill its Rome callout boxes, falling back to its own hardcoded
# placeholders when the array is absent. Fed the same narrative state as the
# recap, including the new matchday_point_history / h2h_differential /
# dependency_index fields so the roasts can cite real analytics.
# --------------------------------------------------------------------------- #
ANALYTICS_SECTIONS = ["The Scoreboard", "Draft Report Card", "Rivalries", "The Model"]

PLACEHOLDER_ANALYTICS_QUOTES = [
    "The numbers don't lie, but your draft does.",
    "You spent a first-round pick on THAT? Bold strategy, Cotton.",
    "Somebody in this pool is getting bullied. Check the tape.",
    "The sim called it. You're just here for the receipts.",
]

ANALYTICS_SYSTEM = (
    "You are Jim Rome writing pull-quotes for the WC Challenge analytics page. "
    "You are given the structured narrative state (standings, per-matchday point "
    "history, head-to-head point differentials, Tier-1 dependency, streaks, and "
    "events). Write exactly FOUR one-liner roasts — one per analytics section — "
    "each reacting to the single most interesting data point in that section. "
    "Max 15 words each. Punchy, opinionated, name names. No hashtags, no quotes "
    "around the lines. Return ONLY a JSON object with keys "
    '"scoreboard", "draft", "rivalries", "model" mapping to the four strings.'
)

ANALYTICS_USER_TEMPLATE = """{rivalries}

{storylines}

NARRATIVE STATE (structured — standings, matchday_point_history, \
h2h_differential, dependency_index, streaks, notable events):
{state}

UPDATED STANDINGS:
{standings}

ROSTERS:
{rosters}

The four analytics sections and what each shows:
- The Scoreboard: win probability, luck vs. projection, max points remaining, who's eliminated.
- Draft Report Card: points per tier slot, points-by-tier breakdown, Tier-1 dependency.
- Rivalries: head-to-head point differentials between owners.
- The Model: how accurate the sim's match predictions were.

Write the four one-liners now (<=15 words each). Return ONLY the JSON object \
with keys scoreboard, draft, rivalries, model."""


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


def load_matches():
    """Read data/matches.csv -> list of row dicts (empty list if missing)."""
    try:
        with open(MATCHES_CSV, encoding="utf-8") as f:
            return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(f)]
    except FileNotFoundError:
        return []


def fixture_dates(rows):
    """Sorted unique fixture dates (ISO strings sort lexicographically)."""
    return sorted({r["date"] for r in rows if r.get("date")})


def resolve_today(args, daily, dates):
    """The date the pundit is speaking on.

    --date wins. Otherwise preview the next unplayed fixture date (the day after
    the latest scored results); pre-tournament, preview opening day; if the
    schedule is exhausted, fall back to the last results day."""
    if args.date:
        return args.date
    last_result = daily["days"][-1]["date"] if daily and daily.get("days") else None
    if last_result:
        upcoming = [d for d in dates if d > last_result]
        return upcoming[0] if upcoming else last_result
    if dates:
        return dates[0]
    return (args.generated or "")[:10]


def rotation_index(today, dates):
    """How far into the schedule 'today' is — drives the daily pundit rotation."""
    if today in dates:
        return dates.index(today)
    return sum(1 for d in dates if d < today)   # still advances on non-fixture days


def todays_h2h(rows, today):
    """Owner-vs-owner fixtures on 'today' (both sides drafted by a manager)."""
    out = []
    for r in rows:
        if r.get("date") != today:
            continue
        o1, o2 = r.get("team1_owner"), r.get("team2_owner")
        if o1 and o2:
            out.append(r)
    return out


def format_h2h(h2h):
    """One bullet per owner-vs-owner game, with WWE personas and real context."""
    lines = []
    for r in h2h:
        o1, o2 = r["team1_owner"], r["team2_owner"]
        w1 = WWE_NAMES.get(o1, o1)
        w2 = WWE_NAMES.get(o2, o2)
        loc = " · ".join(x for x in [r.get("group") and f"Group {r['group']}", r.get("venue")] if x)
        lines.append(f'- {r["team1"]} ({o1} / "{w1}") vs {r["team2"]} ({o2} / "{w2}")'
                     + (f"  [{loc}]" if loc else ""))
    return "\n".join(lines)


def generate_pundit(args, api_key, standings, pundit, h2h, today, rotation):
    """One take from the day's rotating voice -> pundit dict for commentary.json."""
    base = {"name": pundit["name"], "tone": pundit["tone"], "color": pundit["color"]}
    if args.placeholder:
        return {**base, "take": PLACEHOLDER_TAKE}

    if h2h:
        matchup_block = ("TODAY'S HEAD-TO-HEAD MATCHUPS (two managers' drafted teams "
                         "facing each other):\n" + format_h2h(h2h))
        task = TASK_H2H
    else:
        matchup_block = ("TODAY'S HEAD-TO-HEAD MATCHUPS: none — no two managers' teams "
                         "play each other today.")
        task = TASK_TEMP

    user = SINGLE_PUNDIT_USER.format(
        date=today or "(date unknown)",
        rotation=rotation + 1,
        pundit_name=pundit["name"],
        personality=pundit["tone"],
        standings=json.dumps(standings, indent=2) if standings else "(no standings yet)",
        rosters=ROSTERS,
        matchup_block=matchup_block,
        task=task,
    )
    try:
        take = call_openai(api_key, args.model, pundit["system"], user,
                           args.max_tokens, args.temperature)
    except urllib.error.HTTPError as e:
        print(f"  {pundit['name']}: API error {e.code} — using placeholder", file=sys.stderr)
        take = PLACEHOLDER_TAKE
    except Exception as e:  # noqa: BLE001
        print(f"  {pundit['name']}: {e} — using placeholder", file=sys.stderr)
        take = PLACEHOLDER_TAKE
    print(f"  {pundit['name']} (rotation #{rotation + 1}): {take[:70]}...")
    return {**base, "take": take}


def generate_recap(args, api_key, standings, daily, narrative, previous):
    """Jim Rome's next installment, built on the previous column + narrative state."""
    if args.placeholder:
        return PLACEHOLDER_RECAP

    today = latest_day(daily)
    user = RECAP_USER_TEMPLATE.format(
        previous=previous or "(none yet — this is your preseason preview, written before any matches are played)",
        rivalries=RIVALRIES,
        storylines=build_storylines(narrative),
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


def _parse_analytics_quotes(raw):
    """Pull the four ordered strings out of the model's JSON reply.

    Tolerant of code fences and stray prose around the object. Returns a 4-item
    list (Scoreboard, Draft, Rivalries, Model) or None if it can't be parsed."""
    keys = ["scoreboard", "draft", "rivalries", "model"]
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    quotes = [str(obj.get(k, "")).strip() for k in keys]
    return quotes if all(quotes) else None


def generate_analytics_quotes(args, api_key, standings, narrative):
    """Four one-liner analytics roasts -> ordered list for rome_analytics_quotes."""
    if args.placeholder:
        return list(PLACEHOLDER_ANALYTICS_QUOTES)

    user = ANALYTICS_USER_TEMPLATE.format(
        rivalries=RIVALRIES,
        storylines=build_storylines(narrative),
        state=json.dumps(narrative, indent=2) if narrative else "(no narrative state available)",
        standings=json.dumps(standings, indent=2) if standings else "(no standings yet)",
        rosters=ROSTERS,
    )
    try:
        raw = call_openai(api_key, args.model, ANALYTICS_SYSTEM, user,
                          args.analytics_max_tokens, args.temperature)
    except urllib.error.HTTPError as e:
        print(f"  Analytics quotes: API error {e.code} — using placeholders", file=sys.stderr)
        return list(PLACEHOLDER_ANALYTICS_QUOTES)
    except Exception as e:  # noqa: BLE001
        print(f"  Analytics quotes: {e} — using placeholders", file=sys.stderr)
        return list(PLACEHOLDER_ANALYTICS_QUOTES)

    quotes = _parse_analytics_quotes(raw)
    if not quotes:
        print("  Analytics quotes: unparseable reply — using placeholders", file=sys.stderr)
        return list(PLACEHOLDER_ANALYTICS_QUOTES)
    for sec, q in zip(ANALYTICS_SECTIONS, quotes):
        print(f"  Analytics [{sec}]: {q}")
    return quotes


def main():
    ap = argparse.ArgumentParser(description="Generate Pundit Roundtable + Jim Rome narrative")
    ap.add_argument("--model", default="gpt-4o")
    ap.add_argument("--max-tokens", type=int, default=500, help="max tokens for the pundit take")
    ap.add_argument("--date", default=None,
                    help="force the pundit's 'today' (YYYY-MM-DD); default derives from the schedule")
    ap.add_argument("--recap-max-tokens", type=int, default=600,
                    help="max tokens for the Jim Rome rolling column (~200-300 words)")
    ap.add_argument("--analytics-max-tokens", type=int, default=220,
                    help="max tokens for the four analytics pull-quotes (JSON reply)")
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
        # Identify today, the day's rotating voice, and any owner-vs-owner games.
        rows = load_matches()
        dates = fixture_dates(rows)
        today = resolve_today(args, daily, dates)
        rotation = rotation_index(today, dates)
        pundit = PUNDITS[rotation % len(PUNDITS)]
        h2h = todays_h2h(rows, today)
        print(f"  Today: {today or '(unknown)'} · voice: {pundit['name']} · "
              f"{len(h2h)} head-to-head matchup(s)")
        pundit_out = generate_pundit(args, api_key, standings, pundit, h2h, today, rotation)
        # Four one-liner analytics roasts for the analytics page's Rome callouts,
        # fed the same (now richer) narrative state as the rolling column.
        analytics_quotes = generate_analytics_quotes(args, api_key, standings, narrative)
        write_outputs({
            "generated": args.generated or "",
            "source": src,
            "date": today,
            "rotation": rotation,
            "pundit": pundit_out,
            "rome_analytics_quotes": analytics_quotes,
        })

    if do_recap:
        recap = generate_recap(args, api_key, standings, daily, narrative, previous_recap)
        write_recap(recap)
        print(f"  Jim Rome: {recap.strip()[:70]}...")


if __name__ == "__main__":
    main()
