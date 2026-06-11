#!/usr/bin/env python3
"""Generate per-match COVER art for today's fixtures with Nano Banana (Gemini 2.5 Flash Image).

Sibling to email/generate_hero_image.py — same SDK, call pattern and editorial
illustration style — but instead of ONE hero per email this makes ONE cover per
fixture happening today, for the homepage "Today's Matches" cards.

Runs nightly inside the GitHub workflow (continue-on-error). For every fixture on the
target date that involves AT LEAST ONE drafted team:
  - OWNER CLASH (both teams drafted, by DIFFERENT owners) -> SKIP. The frontend already
    has a pre-generated mashup banner (site/assets/clash-banners/<a>-vs-<b>.png) for those.
  - otherwise -> render a 1200x675 (16:9) editorial sports illustration of the two
    nations playing, matching the email-hero house style.

Output: site/assets/match-covers/day_{N}_match_{M}.png
  N = matchday number  = 1-based position of the date among ALL distinct fixture dates.
  M = match index      = 1-based position of the fixture within its day (matches.csv order,
                         counting every fixture that day so the frontend can recompute it).
The homepage computes the SAME (N, M) from matches.csv, so the names line up; missing
covers are handled gracefully on the frontend (the card just shows no art).

Output pattern: SKIP-IF-EXISTS by default (a fixture's art never changes once made, so we
don't pay for it twice). Pass --force to regenerate. OWNER-CLASH fixtures consume an M
index but never produce a file here.

GRACEFUL by design: a missing GEMINI_API_KEY, a missing dependency, or a per-match
generation failure is logged and skipped — the script NEVER raises, so the nightly
commit is never blocked.

Needs GEMINI_API_KEY in the environment (or in ~/.env).

Usage:
    GEMINI_API_KEY=... python generate_match_covers.py                 # today's slate
    python generate_match_covers.py --date 2026-06-13                  # a specific day
    python generate_match_covers.py --date 2026-06-13 --dry-run        # print plan, no API
    python generate_match_covers.py --force                            # ignore skip-if-exists
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))  # repo root (this script lives here)
DATA = os.path.join(ROOT, "data")
OUT_DIR = os.path.join(ROOT, "site", "assets", "match-covers")
MODEL = "gemini-2.5-flash-image"
COVER_W, COVER_H = 1200, 675  # 16:9, same as the email hero

# Shared art direction — deliberately the SAME house style as the email hero so the
# site and the newsletter feel like one publication. Bold stylized SI-cover art, not a
# photo, no text baked in (the card overlays teams/time/group).
_STYLE = (
    "Editorial sports-illustration cover art, WIDE 16:9 landscape composition. Bold "
    "saturated colours, dramatic high-contrast lighting, strong diagonal composition, "
    "thick confident linework and flat painterly shapes — the energy of a Sports "
    "Illustrated cover crossed with a sharp poster. Stylized and graphic, NOT "
    "photorealistic, NOT a photo, NOT a 3D render: an illustration. Soccer/World Cup "
    "iconography (the ball, stadium lights, jerseys, flags) used as bold graphic motifs. "
    "Cinematic, poster-like, instantly readable even at small size. Leave the "
    "composition uncluttered — NO text, NO lettering, NO words, NO scorelines, NO team "
    "names baked into the image (the site overlays all text). Dark moody backdrop so it "
    "sits on a near-black (#0c0e14) page. "
)


# --------------------------------------------------------------------------- IO
def load_env_key():
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    home_env = os.path.join(os.path.expanduser("~"), ".env")
    if os.path.exists(home_env):
        with open(home_env, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GEMINI_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def load_matches(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_owner_of(draft_path):
    """canonical team name -> owner, from draft_board.json."""
    with open(draft_path, encoding="utf-8") as f:
        owners = json.load(f)["owners"]
    out = {}
    for owner, teams in owners.items():
        for t in teams:
            out[t] = owner
    return out


def today_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ----------------------------------------------------------------- prompt
def build_prompt(team1, team2, group, phase):
    where = (f"Group {group} match" if (phase or "group").lower().startswith("group") and group
             else (phase or "knockout").replace("_", " ") + " match")
    return "\n\n".join([
        _STYLE,
        (f"THE MATCHUP to illustrate: {team1} versus {team2} — a {where} at the 2026 World "
         f"Cup. Represent BOTH nations as bold equal halves of the composition: their "
         f"national-team colours, flags and jersey motifs squaring off, the ball and "
         f"stadium drama between them. A want-to-watch fixture poster, no text."),
    ])


# ----------------------------------------------------------------- image
def to_cover_png(raw_bytes):
    """Center-crop the model output to 16:9 and resize to COVER_W x COVER_H; PNG bytes."""
    from PIL import Image
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    w, h = img.size
    target = COVER_W / COVER_H
    if w / h > target:                      # too wide -> trim sides
        new_w = int(h * target)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    else:                                   # too tall -> trim top/bottom
        new_h = int(w / target)
        top = (h - new_h) // 2
        img = img.crop((0, top, w, top + new_h))
    img = img.resize((COVER_W, COVER_H), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def generate_one(client, prompt):
    """Call the model with one retry on an empty response. Return raw image bytes or None."""
    for attempt in (1, 2):
        try:
            resp = client.models.generate_content(model=MODEL, contents=[prompt])
        except Exception as e:  # noqa: BLE001
            print(f"      API error (attempt {attempt}): {e}", file=sys.stderr)
            continue
        cand = (resp.candidates or [None])[0]
        content = getattr(cand, "content", None) if cand else None
        if content and getattr(content, "parts", None):
            for part in content.parts:
                if getattr(part, "inline_data", None) and part.inline_data.data:
                    return part.inline_data.data
            txt = "".join(getattr(p, "text", "") or "" for p in content.parts)
            print(f"      no image in response: {txt[:160]}", file=sys.stderr)
        else:
            reason = getattr(cand, "finish_reason", "unknown") if cand else "no candidates"
            print(f"      empty response (finish_reason={reason})", file=sys.stderr)
    return None


# --------------------------------------------------------------------------- plan
def plan_covers(rows, owner_of, target_date):
    """Return (matchday_number, [jobs]) for target_date.

    Each job: {m, team1, team2, group, phase, drafted, clash, out_name}.
    M counts EVERY fixture that day (matches.csv order) so the frontend can recompute it;
    only non-clash fixtures with >=1 drafted team are flagged for generation.
    """
    distinct_dates = sorted({r["date"] for r in rows if r.get("date")})
    if target_date not in distinct_dates:
        return None, []
    n = distinct_dates.index(target_date) + 1
    day_rows = [r for r in rows if r.get("date") == target_date]
    jobs = []
    for i, r in enumerate(day_rows, 1):
        t1, t2 = r["team1"].strip(), r["team2"].strip()
        o1, o2 = owner_of.get(t1), owner_of.get(t2)
        drafted = bool(o1 or o2)
        clash = bool(o1 and o2 and o1 != o2)
        jobs.append({
            "m": i, "team1": t1, "team2": t2,
            "group": r.get("group", "").strip(), "phase": (r.get("phase") or "group").strip(),
            "owner1": o1, "owner2": o2, "drafted": drafted, "clash": clash,
            "out_name": f"day_{n}_match_{i}.png",
        })
    return n, jobs


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Generate per-match cover art for today's fixtures")
    ap.add_argument("--date", default=today_iso(), help="fixture date YYYY-MM-DD (default: today UTC)")
    ap.add_argument("--matches", default=os.path.join(DATA, "matches.csv"))
    ap.add_argument("--draft", default=os.path.join(DATA, "draft_board.json"))
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--force", action="store_true", help="regenerate even if the cover exists")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and exit; no API calls, no files written")
    args = ap.parse_args()

    try:
        rows = load_matches(args.matches)
        owner_of = build_owner_of(args.draft)
    except (FileNotFoundError, KeyError, json.JSONDecodeError) as e:
        print(f"[match-covers] cannot read inputs ({e}) — nothing to do.", file=sys.stderr)
        return  # graceful: never block the workflow

    n, jobs = plan_covers(rows, owner_of, args.date)
    if not jobs:
        print(f"[match-covers] no fixtures on {args.date} — nothing to do.")
        return

    todo = [j for j in jobs if j["drafted"] and not j["clash"]]
    skipped_clash = [j for j in jobs if j["clash"]]
    skipped_undrafted = [j for j in jobs if not j["drafted"]]
    print(f"[match-covers] matchday {n} ({args.date}): {len(jobs)} fixture(s) — "
          f"{len(todo)} to render, {len(skipped_clash)} owner-clash (use banner), "
          f"{len(skipped_undrafted)} undrafted (no art).")
    for j in skipped_clash:
        a, b = sorted([j["owner1"], j["owner2"]])
        print(f"   clash  match {j['m']}: {j['team1']} v {j['team2']} -> "
              f"clash-banners/{a.lower()}-vs-{b.lower()}.png")

    if args.dry_run:
        for j in todo:
            print(f"   would render {j['out_name']}: {j['team1']} v {j['team2']} "
                  f"(Group {j['group']})")
        return

    if not todo:
        print("[match-covers] no covers to render today.")
        return

    key = load_env_key()
    if not key:
        print("[match-covers] GEMINI_API_KEY not set — skipping (frontend handles "
              "missing covers).", file=sys.stderr)
        return
    try:
        from google import genai
        from PIL import Image  # noqa: F401  (used in post-processing)
    except ImportError as e:
        print(f"[match-covers] missing dependency ({e}) — skipping.", file=sys.stderr)
        return

    client = genai.Client(api_key=key)
    os.makedirs(args.out_dir, exist_ok=True)
    made, skipped, failed = 0, 0, 0
    for j in todo:
        out_path = os.path.join(args.out_dir, j["out_name"])
        if os.path.exists(out_path) and not args.force:
            print(f"   skip (exists): {j['out_name']}")
            skipped += 1
            continue
        print(f"[gen ] {j['out_name']}: {j['team1']} v {j['team2']} (Group {j['group']})")
        prompt = build_prompt(j["team1"], j["team2"], j["group"], j["phase"])
        try:
            data = generate_one(client, prompt)
            if not data:
                print(f"   {j['out_name']}: no image returned, skipping.", file=sys.stderr)
                failed += 1
                continue
            png = to_cover_png(data)
            with open(out_path, "wb") as f:
                f.write(png)
            print(f"   wrote {out_path} ({len(png)//1024} KB)")
            made += 1
        except Exception as e:  # noqa: BLE001 — never let one match break the run
            print(f"   {j['out_name']}: failed ({e}), skipping.", file=sys.stderr)
            failed += 1

    print(f"\n[match-covers] done. {made} written, {skipped} already existed, {failed} failed.")


if __name__ == "__main__":
    main()
