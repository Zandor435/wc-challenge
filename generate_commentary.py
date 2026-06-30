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

{finished_block}

{matchup_block}

{task}

TONE RULES:
- 80% roast, 20% real context. One sentence of actual team form/context as setup, then the knife.
- State every prediction with absurd, unearned confidence.
- NEVER report a result for a match that has not been played. The head-to-head matchups above are \
UPCOMING — predict them, do not narrate them as finished. Only the FINISHED RESULTS block carries real \
scores; never invent any others.
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
# Pundit Takes (Babylon Bee-style satirical news strip) -> commentary.json
# `pundit_takes`. ONE deadpan take per pundit per day: a declarative fake-news
# headline + a one-line knife-twist subtitle. Distinct from the single rotating
# "Featured Analysis" card (`pundit`, generated above): this is the four-up news
# ticker the home page renders via renderPunditTakes(). The site identifies each
# pundit by SLUG, so we emit slugs (not display names) and add the date downstream.
# --------------------------------------------------------------------------- #
PUNDIT_TAKE_SLUGS = ["wynalda", "donovan", "dempsey", "lalas"]

PUNDIT_TAKES_SYSTEM = (
    "You are the satirical sports desk for the WC Challenge — a fantasy World Cup pool between "
    "five managers: Zach (\"Mustard Boy\"), Gunner (\"Bubba G\"), Gayden (\"The Backpass Assassin\"), "
    "Devin (\"Ghost Pepper\"), and Rafe (\"The Noisemaker\"). You write Babylon Bee-style fake-news "
    "headlines. Produce exactly ONE take from each of four pundits — Eric Wynalda (wynalda), Landon "
    "Donovan (donovan), Clint Dempsey (dempsey), Alexi Lalas (lalas) — in that order.\n\n"
    "HEADLINE — Babylon Bee deadpan:\n"
    "- Declarative and straight-faced. It must read like a REAL news headline; the joke is that it is "
    "played completely straight.\n"
    "- NO exclamation marks. No ALL-CAPS words. No emoji. No obvious puns or wordplay. 8-12 words.\n"
    "- 80% roast / 20% real context: the joke must ride on ONE true detail (a draft pick, a team, a "
    "standing, a win probability) so it reads as plausible news.\n"
    "SUBTITLE — one line, the knife twist: a dry fake quote or a deadpan stat that lands the joke.\n\n"
    "Always blame the MANAGER, never the players or bad luck. Use real first names or ring names. "
    "Each pundit keeps their attitude (Wynalda arrogant, Donovan backhanded, Dempsey laid-back, Lalas "
    "bombastic) but the FORMAT is uniform deadpan news — no first-person rants, just the headline.\n\n"
    "CRITICAL — NEVER FABRICATE RESULTS:\n"
    "- For FINISHED matches (listed under YESTERDAY'S RESULTS): write declarative headlines using the "
    "REAL scores provided. Do not invent any other finished match.\n"
    "- For UPCOMING matches (listed under TODAY'S UPCOMING): write PREDICTIONS as bold forecasts. Use "
    "future tense — \"will\", \"set to\", \"poised to\". NEVER past tense. NEVER invent a score.\n"
    "- If a team has NOT played yet, do NOT say they won or lost. You can reference their draft "
    "position, tier, or owner's standing — but NOT a match result that doesn't exist.\n\n"
    "Return ONLY a JSON object of this exact shape (no prose, no code fence):\n"
    '{"pundit_takes": [{"pundit": "wynalda", "headline": "...", "subtitle": "...", '
    '"match": "Team A vs Team B"}, {"pundit": "donovan", ...}, {"pundit": "dempsey", ...}, '
    '{"pundit": "lalas", ...}]}\n'
    "Do NOT include a date field; it is added downstream."
)

PUNDIT_TAKES_USER_TEMPLATE = """TODAY: {date}

THE FIVE MANAGERS (roast these by name / ring name — never the players):
- Zach "Mustard Boy" · Gunner "Bubba G" · Gayden "The Backpass Assassin" · Devin "Ghost Pepper"
- Rafe "The Noisemaker" — Gayden's 15-year-old son; knows nothing about soccer, drafted three teams that didn't even qualify

CURRENT STANDINGS:
{standings}

ROSTERS (each manager drafted 6 national teams across 4 tiers):
{rosters}

{finished_block}

{upcoming_block}

Write exactly FOUR takes — one each from wynalda, donovan, dempsey, lalas, IN THAT ORDER. Each is a \
deadpan Babylon Bee-style news headline (declarative, no exclamation marks, 8-12 words, reads like real \
news) plus a one-line subtitle that twists the knife. 80% roast / 20% real context — the roast must ride \
on one true detail from the standings, rosters, or today's matchups. Set "match" to the relevant fixture \
("Team A vs Team B") or a short topic (e.g. "Group F Preview"). Return ONLY the JSON object described."""

# Deadpan stand-ins for --placeholder / API-failure (date is stamped in at runtime).
PLACEHOLDER_PUNDIT_TAKES = [
    {"pundit": "wynalda", "match": "League Preview",
     "headline": "Analyst Declares Draft Already Over Before Opening Whistle",
     "subtitle": "'I have seen enough,' says man who has watched zero qualifiers"},
    {"pundit": "donovan", "match": "League Preview",
     "headline": "Manager Commended For Bold Plan Of Hoping His Teams Score",
     "subtitle": "Sources confirm no contingency exists beyond that"},
    {"pundit": "dempsey", "match": "League Preview",
     "headline": "Owner Insists Last-Place Projection Is Exactly Where He Wants To Be",
     "subtitle": "Model gives the strategy a four percent chance of working"},
    {"pundit": "lalas", "match": "League Preview",
     "headline": "Pundit Demands All Five Managers Explain Themselves To His Face",
     "subtitle": "Reportedly, not one of them can"},
]

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

# The Rome column's output structure is specified in rome_column_template.md at the
# repo root — that file is canonical, so editing it changes Rome's format without
# touching this script. We load it at generation time and inject it into the system
# prompt; the embedded fallback below keeps the column from silently reverting to
# free-form prose if the file is ever missing.
ROME_TEMPLATE_PATH = os.path.join(HERE, "rome_column_template.md")
ROME_FORMAT_FALLBACK = """# Jim Rome Column — Output Template

## Structure
- One section per owner, sorted by CURRENT RANK (1st -> last).
- Header per owner: `OWNER NAME — [pts] pts · [rank] · [one-line verdict/hot take]`
- Body: 2-3 sentences of Rome-voice color commentary per owner. Max 4 sentences.
- Bold any personal attacks, roasts, or direct shots at the manager. Ring names
  encouraged in the bolded lines; real first names in unbolded body text.
- Optional cross-owner narrative section with its own named header (e.g. THE
  FATHER-SON SAGA), only when a storyline genuinely spans multiple owners.

## Rules
- Jim Rome energy — confident, opinionated, sports-talk-radio.
- No emoji, no icon fonts. Skimmable — readers find their owner in 2 seconds.
- Ring names: Mustard Boy (Zach), Bubba G (Gunner), The Backpass Assassin (Gayden),
  Ghost Pepper (Devin), The Noisemaker (Rafe)."""


def load_rome_template():
    """The Rome column format spec. rome_column_template.md is the source of truth;
    fall back to the embedded copy if the file is missing."""
    return read_text(ROME_TEMPLATE_PATH) or ROME_FORMAT_FALLBACK

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

# Voice + behavior. The OUTPUT STRUCTURE is appended at call time from
# rome_column_template.md (see rome_system_prompt) so the format stays canonical.
JIM_ROME_VOICE = (
    "You are Jim Rome covering the WC Challenge fantasy World Cup pool. You are given "
    "your previous column, today's results, updated standings, and structured narrative "
    "context (streaks, themes, notable events, win probabilities). Write the next "
    "installment. Build on running themes — escalate what's working, drop what's gone "
    "stale. You are ALSO given standing rivalries and season storylines — weave them in "
    "and escalate them, above all the father-vs-son blood feud between Gayden and his "
    "15-year-old son Rafe. Reference specific results and point totals. Be opinionated "
    "about each owner's trajectory."
)

# Concrete markdown rules layered on top of the template so the output renders
# correctly on the site (which parses the recap as GitHub-flavored markdown).
ROME_FORMAT_NOTES = (
    "- Output GitHub-flavored markdown, the column body only — no title, no byline, no preamble.\n"
    "- One section per owner. SORT the sections by current rank, 1st place first down to last.\n"
    "- Render each owner header as a markdown H3 in this exact shape, using the REAL points "
    "and rank from the standings:\n"
    "  `### OWNER NAME — N pts · 1st · one-line verdict`\n"
    "- Under each header, write 2-4 sentences of Rome-voice commentary. Wrap any personal "
    "attack, roast, or direct shot at the manager in markdown bold (**like this**); ring names "
    "belong in the bolded shots, real first names in the plain body text.\n"
    "- Include a cross-owner narrative section ONLY when a storyline genuinely spans multiple "
    "owners (e.g. the father-vs-son saga); give it its own `###` header.\n"
    "- No emoji, no icon fonts."
)


def rome_system_prompt():
    """Assemble Rome's system prompt: voice + the canonical output template
    (rome_column_template.md) + concrete markdown rules. Built per run so edits to
    the template file flow straight through without touching this script."""
    return (
        JIM_ROME_VOICE
        + "\n\nFOLLOW THIS OUTPUT TEMPLATE EXACTLY:\n\n"
        + load_rome_template()
        + "\n\nFORMATTING NOTES:\n"
        + ROME_FORMAT_NOTES
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

Write the next installment of your column now, following the OUTPUT TEMPLATE from your \
instructions: one section per owner sorted by current rank (1st to last), an \
`### OWNER NAME — N pts · rank · verdict` header for each (real points and rank from the \
standings above), 2-4 sentences of commentary under each with personal shots in **bold**, \
and an optional cross-owner narrative section only if a storyline spans multiple owners. \
Build on the running themes, reference specific results and point totals, and stay \
opinionated about each manager's trajectory. Output the column body only — no title, no byline."""

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


def format_finished_results(daily):
    """The latest scored day's matches as 'Team A X-Y Team B [owners]' lines.

    Returns (date, block) or None pre-tournament. These are the ONLY real scores
    the pundits ever see — the source of truth that stops them inventing outcomes."""
    day = latest_day(daily)
    if not day or not day.get("matches"):
        return None
    lines = []
    for m in day["matches"]:
        score = m.get("score") or (
            f'{m.get("home")} {m.get("home_score")}-{m.get("away_score")} {m.get("away")}')
        # A level knockout decided on penalties is NOT a draw — spell out who
        # advanced so the pundits never call it one (the score alone reads 1-1).
        if m.get("decided_by") == "penalties" and m.get("winner"):
            pk = (f' ({m["pen_home"]}-{m["pen_away"]} pens)'
                  if m.get("pen_home") is not None and m.get("pen_away") is not None
                  else "")
            score += f' — {m["winner"]} won on penalties{pk}, {_loser(m)} eliminated'
        pts = m.get("points") or {}
        tag = ", ".join(f"{o} {p:+g}" for o, p in pts.items())
        lines.append(f"- {score}" + (f"  [{tag} pts]" if tag else ""))
    return day["date"], "\n".join(lines)


def _loser(m):
    """The eliminated side of a penalty-decided knockout."""
    return m["away"] if m.get("winner") == m["home"] else m["home"]


def todays_fixtures(rows, today):
    """All matches scheduled on 'today' — owner-vs-owner or not (unplayed)."""
    return [r for r in rows if r.get("date") == today]


def format_fixtures(fixtures):
    """One line per upcoming match, with owner/persona where a side is drafted."""
    def side(team, owner):
        if owner:
            return f'{team} ({owner} / "{WWE_NAMES.get(owner, owner)}")'
        return f"{team} (undrafted)"
    lines = []
    for r in fixtures:
        loc = " · ".join(x for x in [
            r.get("group") and f"Group {r['group']}", r.get("venue"), r.get("time_et")] if x)
        lines.append(f'- {side(r["team1"], r.get("team1_owner"))} '
                     f'vs {side(r["team2"], r.get("team2_owner"))}'
                     + (f"  [{loc}]" if loc else ""))
    return "\n".join(lines)


def context_blocks(daily, rows, today):
    """The finished-results + upcoming-fixtures blocks shared by both pundit prompts.

    Labels explicitly what has been PLAYED (real scores) vs what is UPCOMING (predict
    only) so the model never reports an outcome for a match that hasn't happened."""
    finished = format_finished_results(daily)
    if finished:
        fdate, fblock = finished
        finished_block = (f"YESTERDAY'S RESULTS (FINISHED — {fdate}, real scores; use these, "
                          f"do NOT invent any others):\n{fblock}")
    else:
        finished_block = ("YESTERDAY'S RESULTS (FINISHED): none yet — the tournament hasn't "
                          "kicked off. Do NOT report any match outcomes.")

    fixtures = todays_fixtures(rows, today)
    if fixtures:
        upcoming_block = (f"TODAY'S UPCOMING MATCHES (NOT PLAYED — {today}; do NOT report "
                          f"outcomes or scores, predictions only):\n{format_fixtures(fixtures)}")
    else:
        upcoming_block = (f"TODAY'S UPCOMING MATCHES ({today}): none scheduled — lean on the "
                          "standings, rosters, and each manager's draft logic instead.")
    return finished_block, upcoming_block


def generate_pundit(args, api_key, standings, pundit, h2h, today, rotation, daily, rows):
    """One take from the day's rotating voice -> pundit dict for commentary.json."""
    base = {"name": pundit["name"], "tone": pundit["tone"], "color": pundit["color"]}
    if args.placeholder:
        return {**base, "take": PLACEHOLDER_TAKE}

    finished_block, _ = context_blocks(daily, rows, today)

    if h2h:
        matchup_block = ("TODAY'S HEAD-TO-HEAD MATCHUPS (UPCOMING — not played; predict, "
                         "do not report outcomes):\n" + format_h2h(h2h))
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
        finished_block=finished_block,
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


def _placeholder_takes(today):
    """The four deadpan stand-ins, stamped with today's date."""
    return [{**t, "date": today or ""} for t in PLACEHOLDER_PUNDIT_TAKES]


def _parse_pundit_takes(raw, today):
    """Pull the four takes out of the model's JSON reply.

    Tolerant of code fences and stray prose. Accepts either {"pundit_takes": [...]}
    or a bare array. Keeps only entries with a known pundit slug and a headline,
    normalizes the fields, and stamps the date. Returns a list or None if nothing
    usable parsed."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        text = text[start:end + 1]
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    arr = obj.get("pundit_takes") if isinstance(obj, dict) else obj
    if not isinstance(arr, list):
        return None

    out = []
    for t in arr:
        if not isinstance(t, dict):
            continue
        slug = str(t.get("pundit", "")).strip().lower()
        headline = str(t.get("headline", "")).strip()
        if slug not in PUNDIT_TAKE_SLUGS or not headline:
            continue
        out.append({
            "pundit": slug,
            "headline": headline,
            "subtitle": str(t.get("subtitle", "")).strip(),
            "match": str(t.get("match", "")).strip(),
            "date": today or "",
        })
    return out or None


def generate_pundit_takes(args, api_key, standings, today, daily, rows):
    """Four Babylon Bee-style deadpan takes (one per pundit) -> pundit_takes array.

    ONE API call returns all four; on any failure or unparseable reply we fall back
    to the deadpan placeholders so the home-page strip always has something to show.

    Fed FINISHED results (real scores) and UPCOMING fixtures as separate, clearly
    labeled blocks so the model writes results for played matches and predictions
    for unplayed ones — never inventing an outcome that hasn't happened."""
    if args.placeholder:
        return _placeholder_takes(today)

    finished_block, upcoming_block = context_blocks(daily, rows, today)

    user = PUNDIT_TAKES_USER_TEMPLATE.format(
        date=today or "(date unknown)",
        standings=json.dumps(standings, indent=2) if standings else "(no standings yet)",
        rosters=ROSTERS,
        finished_block=finished_block,
        upcoming_block=upcoming_block,
    )
    try:
        raw = call_openai(api_key, args.model, PUNDIT_TAKES_SYSTEM, user,
                          args.takes_max_tokens, args.temperature)
    except urllib.error.HTTPError as e:
        print(f"  Pundit takes: API error {e.code} — using placeholders", file=sys.stderr)
        return _placeholder_takes(today)
    except Exception as e:  # noqa: BLE001
        print(f"  Pundit takes: {e} — using placeholders", file=sys.stderr)
        return _placeholder_takes(today)

    takes = _parse_pundit_takes(raw, today)
    if not takes:
        print("  Pundit takes: unparseable reply — using placeholders", file=sys.stderr)
        return _placeholder_takes(today)
    for t in takes:
        print(f"  Take [{t['pundit']}]: {t['headline']}")
    return takes


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
        return call_openai(api_key, args.model, rome_system_prompt(), user,
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
    ap.add_argument("--takes-max-tokens", type=int, default=450,
                    help="max tokens for the four Babylon Bee-style pundit takes (JSON reply)")
    ap.add_argument("--date", default=None,
                    help="force the pundit's 'today' (YYYY-MM-DD); default derives from the schedule")
    ap.add_argument("--recap-max-tokens", type=int, default=900,
                    help="max tokens for the Jim Rome rolling column (per-owner sections + "
                         "optional cross-owner narrative; headroom to avoid mid-section truncation)")
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
        pundit_out = generate_pundit(args, api_key, standings, pundit, h2h, today,
                                     rotation, daily, rows)
        # Four Babylon Bee-style deadpan takes (one per pundit) for the home-page
        # "Pundit Takes / The Wire" strip, rendered by renderPunditTakes().
        pundit_takes = generate_pundit_takes(args, api_key, standings, today, daily, rows)
        # Four one-liner analytics roasts for the analytics page's Rome callouts,
        # fed the same (now richer) narrative state as the rolling column.
        analytics_quotes = generate_analytics_quotes(args, api_key, standings, narrative)
        write_outputs({
            "generated": args.generated or "",
            "source": src,
            "date": today,
            "rotation": rotation,
            "pundit": pundit_out,
            "pundit_takes": pundit_takes,
            "rome_analytics_quotes": analytics_quotes,
        })

    if do_recap:
        recap = generate_recap(args, api_key, standings, daily, narrative, previous_recap)
        write_recap(recap)
        print(f"  Jim Rome: {recap.strip()[:70]}...")


if __name__ == "__main__":
    main()
