#!/usr/bin/env python3
"""Generate the 10 OWNER CLASH banners with Nano Banana (Gemini 2.5 Flash Image).

Sibling to ../generate_pundit_avatars.py and ../email/generate_hero_image.py — same
SDK and call pattern — but this is a ONE-TIME batch, not part of the nightly cron.

Five owners -> ten unique pairings. For each pairing we render a WWE pay-per-view /
boxing-promo "fight poster": both owners' WWE portraits facing each other in a split
composition, each half tinted with that owner's draft colour. NO text is baked into the
image — the frontend overlays the match details (teams, time, group) on top.

Inputs (reference images, likeness ONLY — the output is a generated poster, not an edit):
    site/assets/portraits/wwe/<owner>_wwe.jpg

Output pattern: OVERWRITE by default (idempotent; rerun safely). Wide 1200x400 banners:
    site/assets/clash-banners/<a>-vs-<b>.png   (owners alphabetical, lowercase)

Needs GEMINI_API_KEY in the environment (or in ~/.env).

Usage:
    GEMINI_API_KEY=... python generate_clash_banners.py        # all 10 pairings
    python generate_clash_banners.py --only devin-vs-zach      # one pairing
    python generate_clash_banners.py --dry-run                 # print prompts, no API
"""
from __future__ import annotations

import argparse
import io
import itertools
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))  # repo root (this script lives here)
WWE_DIR = os.path.join(ROOT, "site", "assets", "portraits", "wwe")
OUT_DIR = os.path.join(ROOT, "site", "assets", "clash-banners")
MODEL = "gemini-2.5-flash-image"
BANNER_W, BANNER_H = 1200, 400  # wide fight-poster banner

# The five owners: draft colour + WWE persona (persona only flavours the prompt; no
# text is rendered). Portrait file is <key>_wwe.jpg under WWE_DIR.
OWNERS = {
    "zach":   {"name": "Zach",   "color": "#f4c430", "persona": "Mustard Boy"},
    "gunner": {"name": "Gunner", "color": "#2f6dff", "persona": "Bubba G"},
    "gayden": {"name": "Gayden", "color": "#28c060", "persona": "The Backpass Assassin"},
    "devin":  {"name": "Devin",  "color": "#f0743a", "persona": "Ghost Pepper"},
    "rafe":   {"name": "Rafe",   "color": "#a855f7", "persona": "The Noisemaker"},
}

# Shared art direction for every clash banner. A confrontational split fight poster —
# the two portraits driven to the edges, a charged seam down the middle, colour-coded
# halves. Deliberately leaves headroom/centre clear so the frontend can overlay text.
_STYLE = (
    "WIDE 3:1 landscape FIGHT POSTER, the visual language of a WWE pay-per-view promo "
    "crossed with a championship boxing fight card. SPLIT COMPOSITION down a dramatic "
    "central seam: ONE fighter anchored to the LEFT half, the OTHER fighter anchored to "
    "the RIGHT half, the two squared off and facing each other across the centre line, "
    "jaws set, confrontational, trash-talk energy. "
    "CRUCIAL FRAMING — read carefully: frame each fighter TIGHTLY from the upper chest UP "
    "only. Their HEADS ARE LARGE and fill the upper half of the banner, faces sharp, "
    "recognizable and prominent, eyes glaring across the seam at each other. This is a "
    "HEAD-AND-SHOULDERS face-off. Do NOT render full bodies, do NOT show legs, waist, "
    "abs or a bare torso/six-pack — if you show a body you have failed; lead with the "
    "FACES the way a real fight-card poster does. "
    "High-contrast cinematic spotlighting, smoke and arena haze, sparks and lens flare "
    "along the centre seam where the two colours collide. Bold, saturated, poster-like "
    "and instantly readable. Photographic/illustrative hybrid like a real promo poster, "
    "NOT a cartoon. ABSOLUTELY NO TEXT, NO LETTERING, NO WORDS, NO LOGOS, NO NUMBERS "
    "anywhere in the image — the frontend overlays all text later. Keep the central seam "
    "and the lower edge relatively uncluttered so overlaid text stays legible. "
)

_LIKENESS = (
    "CRITICAL: render each fighter as the SAME recognizable man as his reference photo — "
    "same face, build, hair and skin tone — so each is identifiable. Do NOT invent generic "
    "stock faces or swap in different people. "
)


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


def portrait_path(key):
    return os.path.join(WWE_DIR, f"{key}_wwe.jpg")


def build_prompt(a, b):
    """a, b are owner keys (a is the LEFT/alphabetically-first half)."""
    A, B = OWNERS[a], OWNERS[b]
    return "\n\n".join([
        _STYLE,
        (f"LEFT FIGHTER: {A['name']} (wrestling persona \"{A['persona']}\"), his entire "
         f"half of the poster bathed and tinted in his signature colour {A['color']} — "
         f"colored spotlights, smoke and rim-lighting in that hue. He is the FIRST "
         f"reference image."),
        (f"RIGHT FIGHTER: {B['name']} (wrestling persona \"{B['persona']}\"), his entire "
         f"half of the poster bathed and tinted in his signature colour {B['color']} — "
         f"colored spotlights, smoke and rim-lighting in that hue. He is the SECOND "
         f"reference image."),
        (f"The two colours ({A['color']} on the left, {B['color']} on the right) clash "
         f"violently down the central seam. Make it a want-to-watch grudge-match poster."),
        _LIKENESS,
    ])


def to_banner_png(raw_bytes):
    """Center-crop the model output to 3:1 and resize to BANNER_W x BANNER_H; PNG bytes."""
    from PIL import Image
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    w, h = img.size
    target = BANNER_W / BANNER_H
    if w / h > target:                      # too wide -> trim sides
        new_w = int(h * target)
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    else:                                   # too tall -> keep the TOP (faces live up
        new_h = int(w / target)             # there); trim mostly from the bottom, with
        top = int((h - new_h) * 0.12)       # a small margin so the very top edge isn't
        img = img.crop((0, top, w, top + new_h))  # clipped.
    img = img.resize((BANNER_W, BANNER_H), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def generate_one(client, contents):
    """Call the model with one retry on an empty response. Return raw image bytes or None."""
    for attempt in (1, 2):
        try:
            resp = client.models.generate_content(model=MODEL, contents=contents)
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


def all_pairings():
    """10 alphabetical owner pairings as (a, b) key tuples."""
    return list(itertools.combinations(sorted(OWNERS), 2))


def main():
    pairings = all_pairings()
    slugs = [f"{a}-vs-{b}" for a, b in pairings]
    ap = argparse.ArgumentParser(description="Generate the 10 owner-clash fight banners")
    ap.add_argument("--only", choices=slugs, help="generate just one pairing, e.g. devin-vs-zach")
    ap.add_argument("--dry-run", action="store_true",
                    help="print prompts and exit; do not call Gemini or touch files")
    args = ap.parse_args()

    targets = [tuple(args.only.split("-vs-"))] if args.only else pairings

    if args.dry_run:
        for a, b in targets:
            print(f"\n===== {a}-vs-{b} =====\n{build_prompt(a, b)}")
        return

    key = load_env_key()
    if not key:
        print("ERROR: GEMINI_API_KEY not set (env or ~/.env).", file=sys.stderr)
        sys.exit(1)

    from google import genai
    from PIL import Image
    client = genai.Client(api_key=key)
    os.makedirs(OUT_DIR, exist_ok=True)

    made, failed = [], []
    for a, b in targets:
        slug = f"{a}-vs-{b}"
        pa, pb = portrait_path(a), portrait_path(b)
        if not os.path.exists(pa) or not os.path.exists(pb):
            print(f"[skip] {slug}: missing portrait ({pa if not os.path.exists(pa) else pb})",
                  file=sys.stderr)
            failed.append(slug)
            continue
        refs = [Image.open(pa), Image.open(pb)]
        print(f"[gen ] {slug}  ({OWNERS[a]['name']} {OWNERS[a]['color']} vs "
              f"{OWNERS[b]['name']} {OWNERS[b]['color']})")
        data = generate_one(client, [build_prompt(a, b)] + refs)
        if not data:
            print(f"   {slug}: failed, skipping.", file=sys.stderr)
            failed.append(slug)
            continue
        png = to_banner_png(data)
        out = os.path.join(OUT_DIR, f"{slug}.png")
        with open(out, "wb") as f:
            f.write(png)
        print(f"   wrote {out} ({len(png)//1024} KB)")
        made.append(slug)

    print(f"\nDone. {len(made)}/{len(targets)} banner(s) written under site/assets/clash-banners/.")
    if failed:
        print(f"Failed/skipped: {', '.join(failed)}", file=sys.stderr)


if __name__ == "__main__":
    main()
