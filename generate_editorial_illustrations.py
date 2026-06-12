#!/usr/bin/env python3
"""Generate the DYNAMIC editorial illustrations for the most recent match day.

The static banners (generate_site_banners.py) are fixed site furniture. THESE are
tied to what actually happened: after the nightly pipeline scores results and the
narrative/commentary engines run, this reads the day's story and paints 1-2 wide
banners of the biggest moment — a father-son grudge result, an upset, an elimination,
an owner-vs-owner clash, a coronation, whatever fired highest on the ladder. They drop
into the SAME rotation pool as the static banners, so the collection grows through the
tournament.

One engine, not a fork: the Gemini call, the 4:1 post-crop, the owner persona/colour
table and the five style lanes are all imported from generate_site_banners.py. This
script only adds the trigger detection and the per-trigger scene prompts.

Pipeline position: runs nightly INSIDE the kickoff gate, AFTER generate_commentary.py
(so commentary.json + tournament_recap.md are fresh) and BEFORE the email steps.

Inputs (read-only):
    site/data/narrative_state.json   ranks, points_today, head_to_head_log, notable_events, history
    site/data/daily_results.json     the day's matches (owner-clash + winner/loser detection)
    site/data/tournament_recap.md    Rome's column (the kayfabe catch-all)
    site/data/commentary.json        (loaded for completeness / future use)
    data/draft_board.json            owner -> teams (owner_of lookup)

Trigger ladder (first match wins; at most 2 illustrations a day):
    1 father-son result   2 upset   3 elimination   4 owner clash   5 lead change
    6 big day (8+ pts)    7 zero-point day   8 call Bradley   9 kayfabe callback (catch-all)

Style: each day's illustrations rotate through the five lanes by matchday index, so the
pool stays visually varied across the tournament.

Output: site/assets/banners/dynamic/day_{N}_{trigger}.png   (1400x350, no baked-in text)
Output pattern: SKIP-IF-EXISTS by default (a day's result never changes; don't re-pay).
Pass --force to regenerate.

GRACEFUL by design: a missing GEMINI_API_KEY, a missing dependency, preseason/empty data,
or a per-illustration failure is logged and skipped — the script NEVER raises, so the
nightly commit is never blocked.

Needs GEMINI_API_KEY in the environment (or in ~/.env).

Usage:
    GEMINI_API_KEY=... python generate_editorial_illustrations.py
    python generate_editorial_illustrations.py --dry-run     # print the plan + prompts, no API
    python generate_editorial_illustrations.py --force       # regenerate even if files exist
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))  # repo root (this script lives here)
DATA = os.path.join(HERE, "site", "data")
ROOT_DATA = os.path.join(HERE, "data")
OUT_DIR = os.path.join(HERE, "site", "assets", "banners", "dynamic")
STATIC_DIR = os.path.join(HERE, "site", "assets", "banners", "static")

# One engine: reuse the static generator's Gemini plumbing, art direction and owner table.
from generate_site_banners import (  # noqa: E402
    OWNERS, LANES, _BASE, _LIKENESS, _FACE_SAFE,
    load_env_key, generate_one, to_banner_png, ref_path, MODEL,
)

# Lane rotation order for the day-by-day variety (keys into LANES).
STYLE_ROTATION = ["cinematic", "fight_poster", "painted", "composite", "illustrated"]

# Owner -> WWE ring name (matches site/app.js WWE_NAMES + generate_commentary.py).
WWE_NAMES = {
    "Zach": "Mustard Boy", "Gunner": "Bubba G", "Gayden": "The Backpass Assassin",
    "Devin": "Ghost Pepper", "Rafe": "The Noisemaker",
}


# --------------------------------------------------------------------------- IO
def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""


# ----------------------------------------------------------------- small helpers
def fmt_num(n):
    v = round(float(n), 2)
    return str(int(v)) if v == int(v) else str(v)


def persona(name):
    """Owner display name -> (portrait key, look phrase, team colour)."""
    key = str(name).lower()
    o = OWNERS.get(key, {})
    return key, o.get("look", str(name)), o.get("color", "#ffffff")


def owner_phrase(name):
    """A rich inline description so Gemini anchors the face to the right persona."""
    _key, look, color = persona(name)
    ring = WWE_NAMES.get(name, name)
    return f'{name} ("{ring}" — {look}; team colour {color})'


def winner_loser(m):
    """(winner_team, loser_team) or (None, None) for a genuine draw."""
    hs, as_ = m.get("home_score"), m.get("away_score")
    if hs is None or as_ is None:
        return None, None
    if hs == as_:
        pw = (m.get("pen_home", 0) or 0) - (m.get("pen_away", 0) or 0)
        if pw == 0:
            return None, None
        return (m["home"], m["away"]) if pw > 0 else (m["away"], m["home"])
    return (m["home"], m["away"]) if hs > as_ else (m["away"], m["home"])


def match_line(m):
    sc = m.get("score")
    if sc:
        return f'{m.get("home")} {sc} {m.get("away")}'
    return f'{m.get("home")} {m.get("home_score")}-{m.get("away_score")} {m.get("away")}'


def owner_of_map(draft):
    return {team: o for o, teams in draft.items() for team in teams}


# --------------------------------------------------------------- trigger ladder
# Each detector returns a trigger dict or None:
#   {"slug", "refs": [ref keys], "scene": str, "face_safe": bool}
# Detectors run in priority order; the first two that fire are illustrated.
def detect_triggers(state, daily, recap, draft):
    owner_of = owner_of_map(draft)
    history = state.get("history", []) or []
    today_date = history[-1]["date"] if history else None
    days = daily.get("days", []) or []
    today_matches = days[-1].get("matches", []) if days else []
    owners_block = state.get("owners", {}) or {}
    events = state.get("notable_events", []) or []
    bradley_ok = os.path.exists(os.path.join(STATIC_DIR, "f_mentor.png"))

    fired = []

    # 1 — Father vs Son (Gayden vs Rafe head-to-head today)
    fs = None
    for e in state.get("head_to_head_log", []) or []:
        if e.get("date") != today_date:
            continue
        if e.get("result") == "win":
            parties = {e.get("winner"), e.get("loser")}
        elif e.get("result") == "draw":
            parties = set(e.get("owners", []))
        else:
            parties = set()
        if parties == {"Gayden", "Rafe"}:
            fs = e
            break
    if fs:
        ctx = f'({fs.get("via", "")}{", " if fs.get("via") else ""}{fs.get("match", "")})'
        if fs.get("result") == "win" and fs.get("winner") == "Gayden":
            scene = (f'{owner_phrase("Gayden")} has just beaten his teenage son '
                     f'{owner_phrase("Rafe")} head-to-head {ctx}. Show Gayden reclining smugly '
                     f'in a leather recliner reading a newspaper, utterly relaxed and satisfied, '
                     f'while behind him Rafe fumes and seethes, furious at the loss. A father '
                     f'lording the win over his son.')
        elif fs.get("result") == "win" and fs.get("winner") == "Rafe":
            scene = (f'{owner_phrase("Rafe")} has just beaten his own father '
                     f'{owner_phrase("Gayden")} head-to-head {ctx}. Show Rafe standing '
                     f'triumphantly OVER a fallen Gayden, pointing down at him in a classic WWE '
                     f'victory pose — the teenager finally on top. Gayden is down and humbled.')
        else:
            scene = (f'{owner_phrase("Gayden")} and his teenage son {owner_phrase("Rafe")} fought '
                     f'to a stalemate head-to-head {ctx}. Show father and son locked eyeball to '
                     f'eyeball in a tense standoff, neither giving an inch.')
        fired.append({"slug": "father_son", "refs": ["gayden", "rafe"],
                      "scene": scene, "face_safe": True})

    # 2 — Upset (tier-gap upset bonus fired today)
    ups = [e for e in events if e.get("type") == "upset" and e.get("date") == today_date]
    if ups:
        e = max(ups, key=lambda x: x.get("bonus", 0) or 0)
        win_owner, win_team, lose_team = e.get("owner"), e.get("team"), e.get("beat")
        lose_owner = owner_of.get(lose_team)
        if lose_owner:
            scene = (f'A massive upset: {win_team} (owned by {win_owner}) has shocked {lose_team}. '
                     f'Show {owner_phrase(lose_owner)} staring up at the sky in stunned disbelief, '
                     f'hands on his head, as the flag of {win_team} flies triumphantly behind him.')
            refs = [persona(lose_owner)[0]]
        else:
            scene = (f'A massive upset: {owner_phrase(win_owner)} watched his {win_team} shock '
                     f'{lose_team}. Show him roaring in triumph, arms raised, the flag of '
                     f'{win_team} flying huge behind him.')
            refs = [persona(win_owner)[0]]
        fired.append({"slug": "upset", "refs": refs, "scene": scene, "face_safe": True})

    # 3 — Elimination (a drafted team knocked out today)
    elims = [e for e in events if e.get("type") == "elimination"
             and e.get("date") == today_date and e.get("owner")]
    if elims:
        e = elims[0]
        owner, team, rnd = e["owner"], e["team"], e.get("round", "knockouts")
        scene = (f'{owner_phrase(owner)} mourns his {team}, eliminated in the {rnd}. Show him in '
                 f'a sombre black suit holding a bouquet of flowers at a graveside, head bowed, '
                 f'the flag of {team} draped over the headstone. Funeral eulogy mood, grey rain.')
        fired.append({"slug": "elimination", "refs": [persona(owner)[0]],
                      "scene": scene, "face_safe": True})

    # 4 — Owner clash (two owners' teams met today)
    for m in today_matches:
        o1, o2 = owner_of.get(m.get("home")), owner_of.get(m.get("away"))
        if o1 and o2 and o1 != o2:
            w, l = winner_loser(m)
            if w is not None:
                wo, lo = owner_of[w], owner_of[l]
                scene = (f'Owner-versus-owner clash: {w} ({wo}) beat {l} ({lo}), {match_line(m)}. '
                         f'Show {owner_phrase(wo)} standing tall with both arms raised in victory '
                         f'while {owner_phrase(lo)} slumps in defeat beside him. A stadium '
                         f'scoreboard glows behind them showing the result.')
                refs = [persona(wo)[0], persona(lo)[0]]
            else:
                scene = (f'Owner-versus-owner clash ended level, {match_line(m)}: {m["home"]} '
                         f'({o1}) drew {m["away"]} ({o2}). Show {owner_phrase(o1)} and '
                         f'{owner_phrase(o2)} nose to nose, neither able to claim bragging '
                         f'rights, a scoreboard glowing behind them.')
                refs = [persona(o1)[0], persona(o2)[0]]
            fired.append({"slug": "owner_clash", "refs": refs, "scene": scene, "face_safe": True})
            break

    # 5 — Lead change (the rank-1 owner climbed into first today)
    leader = next((o for o, info in owners_block.items() if info.get("rank") == 1), None)
    if leader and (owners_block[leader].get("rank_change_from_prev_day", 0) or 0) > 0:
        prev_leader = None
        if len(history) >= 2:
            prev_leader = next((o for o, r in history[-2].get("ranks", {}).items() if r == 1), None)
        scene = (f'The lead has changed hands: {owner_phrase(leader)} is the NEW points leader')
        if prev_leader and prev_leader != leader:
            scene += (f', overtaking {owner_phrase(prev_leader)}. Show {leader} seated on a golden '
                      f'throne as a crown settles onto his head, while {prev_leader} walks away '
                      f'dethroned into the shadows — the crown transferring.')
            refs = [persona(leader)[0], persona(prev_leader)[0]]
        else:
            scene += ('. Show him seated on a golden throne as a crown settles onto his head, '
                      'newly crowned king of the standings.')
            refs = [persona(leader)[0]]
        fired.append({"slug": "lead_change", "refs": refs, "scene": scene, "face_safe": True})

    # 6 — Big day (an owner banked 8+ points today)
    big = [(o, info.get("points_today", 0) or 0) for o, info in owners_block.items()
           if (info.get("points_today", 0) or 0) >= 8]
    if big:
        o, pts = max(big, key=lambda x: x[1])
        scene = (f'{owner_phrase(o)} banked a huge {fmt_num(pts)} points in a single day. Show him '
                 f'striding through a storm of confetti in a packed, roaring stadium, arms spread '
                 f'wide, soaking in the adulation. Pure triumphant celebration.')
        fired.append({"slug": "big_day", "refs": [persona(o)[0]], "scene": scene, "face_safe": True})

    # 7 — Zero-point day (an owner had teams play but scored nothing)
    played_owners = []
    for m in today_matches:
        for t in (m.get("home"), m.get("away")):
            o = owner_of.get(t)
            if o and o not in played_owners:
                played_owners.append(o)
    zeros = [o for o in played_owners if (owners_block.get(o, {}).get("points_today", 0) or 0) == 0]
    if zeros:
        o = zeros[0]
        scene = (f'A washout: {owner_phrase(o)} had teams in action today but scored ZERO points. '
                 f'Show him slumped alone on an empty substitutes bench, head buried in his hands, '
                 f'a vast empty darkened stadium stretching out behind him. Dejection and silence.')
        fired.append({"slug": "zero_day", "refs": [persona(o)[0]], "scene": scene, "face_safe": True})

    # 8 — Call Bradley (Gayden lost ANY match today; only if the mentor banner exists)
    if bradley_ok:
        gayden_teams = set(draft.get("Gayden", []))
        lost_match = None
        for m in today_matches:
            _w, l = winner_loser(m)
            if l and l in gayden_teams:
                lost_match = m
                break
        if lost_match:
            scene = ('A wrestler named Gayden — a sinister manager in black-and-white KISS-style '
                     'face paint with a tall black mohawk and spiked black leather, lit in green '
                     '(#28c060); NOT in any reference photo, invent him from this description — '
                     f'has just lost ({match_line(lost_match)}) and is panicking. Show Gayden on '
                     'the LEFT clutching a phone to his ear, wide-eyed and frantic. On the RIGHT, '
                     'his mentor Michael Bradley, the man in the REFERENCE PHOTO — render his face '
                     'EXACTLY: a BALD head (no hair) and a short reddish-ginger beard, in a dark '
                     'suit — holds his own phone and shakes his head in slow, disappointed '
                     'disapproval. A split-screen phone call.')
            fired.append({"slug": "call_bradley", "refs": ["bradley2"],
                          "scene": scene, "face_safe": True})

    # 9 — Kayfabe callback (catch-all): illustrate the wildest beat of Rome's column
    if recap:
        counts = {o: recap.count(o) + recap.count(WWE_NAMES.get(o, o)) for o in draft}
        owner = max(counts, key=counts.get) if any(counts.values()) else (leader or next(iter(draft), None))
        if owner:
            scene = ("Below is the latest installment of Jim Rome's satirical column about a "
                     "fantasy World Cup pool. Read it, pick the SINGLE most dramatic, absurd or "
                     "funny moment in it, and illustrate THAT one moment as a wide banner. Feature "
                     f'{owner_phrase(owner)} — use the face from the reference photo — as the '
                     f"central character.\n\n--- ROME'S COLUMN ---\n{recap[:1400]}")
            fired.append({"slug": "kayfabe", "refs": [persona(owner)[0]],
                          "scene": scene, "face_safe": False})

    return fired


# ----------------------------------------------------------------- generation
def load_ref_images(keys):
    from PIL import Image
    imgs, missing = [], []
    for k in keys:
        p = ref_path(k)
        if os.path.exists(p):
            imgs.append(Image.open(p))
        else:
            missing.append(p)
    return imgs, missing


def build_prompt(trig, lane):
    parts = [_BASE + LANES[lane], trig["scene"], _LIKENESS]
    if trig.get("face_safe"):
        parts.append(_FACE_SAFE)
    return "\n\n".join(parts)


def matchday_number(state, daily):
    ph = state.get("phase", {}) or {}
    n = ph.get("matchdays_played")
    if not n:
        n = len(daily.get("days", []) or [])
    return int(n or 0)


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Generate the day's dynamic editorial illustrations")
    ap.add_argument("--state", default=os.path.join(DATA, "narrative_state.json"))
    ap.add_argument("--daily", default=os.path.join(DATA, "daily_results.json"))
    ap.add_argument("--recap", default=os.path.join(DATA, "tournament_recap.md"))
    ap.add_argument("--commentary", default=os.path.join(DATA, "commentary.json"))
    ap.add_argument("--draft", default=os.path.join(ROOT_DATA, "draft_board.json"))
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--force", action="store_true", help="regenerate even if the file exists")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan + prompts and exit; no API calls, no files written")
    args = ap.parse_args()

    state = load_json(args.state) or {}
    daily = load_json(args.daily) or {"days": []}
    draft = (load_json(args.draft) or {}).get("owners", {})
    recap = read_text(args.recap)
    _commentary = load_json(args.commentary) or {}  # loaded for completeness / future scenes

    # Preseason / empty guard — nothing has happened yet, so there is nothing to illustrate.
    if state.get("source") == "preseason" or state.get("phase", {}).get("is_preseason") \
            or not state.get("history") or not daily.get("days"):
        print("[editorial] preseason / no results yet — nothing to illustrate.")
        return
    if not draft:
        print("[editorial] no draft board — nothing to illustrate.", file=sys.stderr)
        return

    n = matchday_number(state, daily)
    fired = detect_triggers(state, daily, recap, draft)
    selected = fired[:2]  # first match wins, max two a day

    if not selected:
        print("[editorial] no triggers fired today — nothing to illustrate.")
        return

    print(f"[editorial] matchday {n}: {len(fired)} trigger(s) fired, illustrating "
          f"{len(selected)}: {', '.join(t['slug'] for t in selected)}")

    if args.dry_run:
        for k, trig in enumerate(selected):
            lane = STYLE_ROTATION[(n - 1 + k) % len(STYLE_ROTATION)]
            print("=" * 80)
            print(f"day_{n}_{trig['slug']}.png  [{lane}]  refs: {', '.join(trig['refs'])}")
            print("-" * 80)
            print(build_prompt(trig, lane))
        return

    # --- live generation: graceful at every step, never raise -------------------
    key = load_env_key()
    if not key:
        print("[editorial] GEMINI_API_KEY not set — skipping (frontend handles missing art).",
              file=sys.stderr)
        return
    try:
        from google import genai
        from PIL import Image  # noqa: F401  (used in ref loading / post-processing)
    except ImportError as e:
        print(f"[editorial] missing dependency ({e}) — skipping.", file=sys.stderr)
        return

    client = genai.Client(api_key=key)
    os.makedirs(args.out_dir, exist_ok=True)
    made, skipped, failed = 0, 0, 0
    for k, trig in enumerate(selected):
        lane = STYLE_ROTATION[(n - 1 + k) % len(STYLE_ROTATION)]
        out_path = os.path.join(args.out_dir, f"day_{n}_{trig['slug']}.png")
        if os.path.exists(out_path) and not args.force:
            print(f"   skip (exists): day_{n}_{trig['slug']}.png")
            skipped += 1
            continue
        refs, missing = load_ref_images(trig["refs"])
        if missing:
            print(f"   [warn] {trig['slug']}: missing ref(s) {missing}", file=sys.stderr)
        print(f"[gen ] day_{n}_{trig['slug']}.png  [{lane}]  refs: {', '.join(trig['refs'])}")
        try:
            data = generate_one(client, [build_prompt(trig, lane)] + refs)
            if not data:
                print(f"   {trig['slug']}: no image returned, skipping.", file=sys.stderr)
                failed += 1
                continue
            png = to_banner_png(data)
            with open(out_path, "wb") as f:
                f.write(png)
            print(f"   wrote {out_path} ({len(png)//1024} KB)")
            made += 1
        except Exception as e:  # noqa: BLE001 — never let one illustration break the run
            print(f"   {trig['slug']}: failed ({e}), skipping.", file=sys.stderr)
            failed += 1

    print(f"\n[editorial] done. {made} written, {skipped} already existed, {failed} failed.")


if __name__ == "__main__":
    main()
