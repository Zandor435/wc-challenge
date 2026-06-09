#!/usr/bin/env python3
"""Generate WWE-style full-body promo portraits with Nano Banana (Gemini 2.5 Flash Image).

Companion to generate_portraits.py, but for the manager BIO PAGE only. Each owner
is rendered as a larger-than-life pro-wrestling superstar in the visual language of
WWE.com hero images: dramatic rim lighting, smoke/pyro, dark background, full body.

Reads reference photos from assets/reference/ and writes variations to
assets/portraits/wwe/ as <name>_wwe_1.jpg, <name>_wwe_2.jpg, ... plus a canonical
<name>_wwe.jpg (a copy of variation 1) that the bio page points at by default.

Needs GEMINI_API_KEY in the environment (or in ~/.env).

Usage:
    GEMINI_API_KEY=... python generate_wwe_portraits.py            # all owners
    python generate_wwe_portraits.py --only zach --max 1           # smoke test
    python generate_wwe_portraits.py --canonical-only              # re-pick _wwe.jpg from _1
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REF_DIR = os.path.join(HERE, "assets", "reference")
OUT_DIR = os.path.join(HERE, "assets", "portraits", "wwe")
MODEL = "gemini-2.5-flash-image"

_LIKENESS = ("CRITICAL: keep the EXACT same face as the reference photo(s) — same eyes, "
             "nose, mouth, jawline, eyebrows, face shape, hairline, skin tone and overall "
             "likeness — so he is immediately recognizable as that specific man. Do NOT "
             "invent a generic face or substitute a stock model; this is a portrait of "
             "THIS exact person in costume, only the wardrobe and setting change. ")

# Shared WWE.com hero-image art direction (The Rock / Cena / Cody Rhodes / Lesnar).
_WWE = ("Full-body cinematic pro-wrestling promotional portrait in the style of a "
        "WWE.com superstar hero image. Dramatic theatrical rim lighting and hard "
        "key light, swirling stage smoke and bursts of pyrotechnic sparks, deep near-"
        "black background, high contrast, larger-than-life heroic energy, shot on a "
        "long lens. Tall vertical 3:4 portrait, the figure filling the frame head to "
        "boots. Photorealistic, ultra-detailed, magazine-cover quality. ")

OWNERS = {
    "zach": {
        "persona": "Hulk Hogan",
        "refs": ["zach_ref_pink.jpg"],
        "base": (_WWE + "Render the man from the reference photo(s) as a Hulk Hogan-"
                 "style wrestling icon: a bright yellow tank top with a torn neckline, a "
                 "yellow bandana tied around his head, a big bushy blonde handlebar "
                 "horseshoe mustache, deeply tanned and broad-shouldered, fully clothed. "
                 + _LIKENESS),
        "variations": [
            "Cupping one hand to his ear, head cocked toward a roaring crowd, mouth open in a shout.",
            "Both arms raised in a double bicep flex, yellow tank top straining, defiant roaring stare.",
            "Pointing a finger straight at the camera with a wild grin, gripping the collar of the yellow tank top with the other hand.",
        ],
    },
    "gunner": {
        "persona": "Macho Man Randy Savage",
        "refs": ["gunner_ref.jpg"],
        "base": (_WWE + "Render the man from the reference photo(s) as a Macho Man "
                 "Randy Savage-style wrestling icon: a flamboyant sparkling sequined "
                 "robe with tassels, a wide cowboy hat, oversized flashy sunglasses, "
                 "loud and charismatic swagger, pointing dramatically at the camera. "
                 + _LIKENESS),
        "variations": [
            "Arms thrown wide flaring open the glittering robe, jaw set, pointing at the viewer.",
            "Leaning in close snarling into the camera, sunglasses catching the pyro glare, finger jabbed forward.",
        ],
    },
    "gayden": {
        "persona": "Legion of Doom / Road Warrior",
        "refs": ["gayden_ref.jpg"],
        "base": (_WWE + "Render the man from the reference photo(s) as a Legion of "
                 "Doom / Road Warrior-style wrestling icon: massive black spiked metal "
                 "shoulder pads, aggressive black-and-silver face paint streaked across "
                 "his eyes, a sharp mohawk, studded leather, hulking and intimidating, "
                 "menacing scowl. " + _LIKENESS),
        "variations": [
            "Fists clenched at his sides, shoulders squared, glaring straight down the lens through the smoke.",
            "One spiked shoulder dropped toward the camera mid-roar, arms flexed, pyro erupting behind him.",
            "Arms crossed over his chest, chin lowered, a cold menacing stare straight into the camera.",
        ],
    },
    "devin": {
        "persona": "Ultimate Warrior",
        "refs": ["devin_ref.jpg"],
        "base": (_WWE + "Render the man from the reference photo(s) as an Ultimate "
                 "Warrior-style wrestling icon: bold full-face neon war paint, bright "
                 "tassels/streamers tied around both biceps, gripping the ropes, "
                 "sprinting and flexing with completely unhinged wild-eyed intensity, "
                 "muscles straining, pure adrenaline. " + _LIKENESS),
        "variations": [
            "Mid-sprint bursting toward the camera, both arms flexed overhead, screaming, tassels flying.",
            "Gripping and shaking the ring ropes, war-painted face contorted in a feral wide-eyed roar.",
        ],
    },
    # Rafe has no reference photo on disk: he is a text-only ("faceless") character,
    # a scrappy swamp kid rather than a likeness of a real person.
    "rafe": {
        "persona": "Jake 'The Snake' Roberts",
        "refs": [],
        "faceless": True,
        "base": (_WWE + "Render a skinny, wiry, scrappy 15-year-old teenage boy as a "
                 "Jake 'The Snake' Roberts-style wrestling icon. He is lean and sinewy — "
                 "NOT muscular, jacked, or adult; a scrappy lightweight kid. He has a "
                 "shaggy 1980s mullet and a large live snake (a thick python/boa) draped "
                 "heavily around his neck and shoulders. His expression is unhinged, "
                 "fearless confidence with zero fear in his eyes — the look of a swamp kid "
                 "who catches wild lizards and snakes with his bare hands and has just "
                 "strolled into a league he knows nothing about and isn't the least bit "
                 "worried about it. Simple dark wrestling attire. "
                 "Render a generic teenage boy's face (do NOT use any reference photo). "),
        "variations": [
            "Holding the snake's head up toward the camera with a calm, dead-eyed unhinged stare, the snake's body coiled across his shoulders.",
            "Lifting the snake overhead with both hands, head tilted back, wild fearless grin, smoke and sparks behind him.",
            "Snake draped around his neck, arms loose at his sides, fearless thousand-yard stare straight down the lens through the smoke.",
        ],
    },
}


def load_env_key():
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    # Fall back to ~/.env (KEY=VALUE lines), matching the user's setup.
    home_env = os.path.join(os.path.expanduser("~"), ".env")
    if os.path.exists(home_env):
        with open(home_env, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GEMINI_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def load_refs(cfg):
    from PIL import Image
    imgs = []
    for name in cfg["refs"]:
        p = os.path.join(REF_DIR, name)
        if os.path.exists(p):
            imgs.append(Image.open(p))
    return imgs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=list(OWNERS), help="generate just one owner")
    ap.add_argument("--start", type=int, default=1,
                    help="first output index; use to avoid overwriting existing variations")
    ap.add_argument("--count", type=int, default=None,
                    help="how many variations to generate (default: all poses)")
    ap.add_argument("--no-canonical", action="store_true",
                    help="do not (re)write <name>_wwe.jpg (lets the user pick later)")
    ap.add_argument("--canonical-only", action="store_true",
                    help="just (re)copy <name>_wwe_1.jpg to <name>_wwe.jpg")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    targets = [args.only] if args.only else list(OWNERS)

    if args.canonical_only:
        for mgr in targets:
            v1 = os.path.join(OUT_DIR, f"{mgr}_wwe_1.jpg")
            if os.path.exists(v1):
                shutil.copyfile(v1, os.path.join(OUT_DIR, f"{mgr}_wwe.jpg"))
                print(f"   {mgr}_wwe.jpg <- {mgr}_wwe_1.jpg")
        return

    key = load_env_key()
    if not key:
        print("ERROR: GEMINI_API_KEY not set (env or ~/.env).", file=sys.stderr)
        sys.exit(1)

    from google import genai
    client = genai.Client(api_key=key)

    made, skipped = [], []
    for mgr in targets:
        cfg = OWNERS[mgr]
        refs = load_refs(cfg)
        if not refs and not cfg.get("faceless"):
            print(f"[skip] {mgr}: no reference photo in assets/reference/ "
                  f"(expected one of {cfg['refs']})")
            skipped.append(mgr)
            continue
        ref_note = f"{len(refs)} reference photo(s)" if refs else "faceless (no reference)"
        print(f"[gen ] {mgr} as {cfg['persona']} — {ref_note}")

        poses = cfg["variations"]
        count = args.count if args.count is not None else len(poses)
        for j in range(count):
            variation = poses[j % len(poses)]
            idx = args.start + j  # output filename index <name>_wwe_<idx>.jpg
            prompt = f"{cfg['base']} {variation}"
            contents = [prompt] + refs
            try:
                resp = client.models.generate_content(model=MODEL, contents=contents)
            except Exception as e:  # noqa: BLE001
                print(f"   {mgr}_{idx}: API error: {e}", file=sys.stderr)
                continue
            cand = (resp.candidates or [None])[0]
            content = getattr(cand, "content", None) if cand else None
            if not content or not getattr(content, "parts", None):
                reason = getattr(cand, "finish_reason", "unknown") if cand else "no candidates"
                print(f"   {mgr}_{idx}: empty response (finish_reason={reason}); retrying once...",
                      file=sys.stderr)
                try:
                    resp = client.models.generate_content(model=MODEL, contents=contents)
                    cand = (resp.candidates or [None])[0]
                    content = getattr(cand, "content", None) if cand else None
                except Exception as e:  # noqa: BLE001
                    print(f"   {mgr}_{idx}: retry API error: {e}", file=sys.stderr)
                if not content or not getattr(content, "parts", None):
                    print(f"   {mgr}_{idx}: still empty, skipping.", file=sys.stderr)
                    continue
            saved = False
            for part in content.parts:
                if getattr(part, "inline_data", None) and part.inline_data.data:
                    out = os.path.join(OUT_DIR, f"{mgr}_wwe_{idx}.jpg")
                    with open(out, "wb") as f:
                        f.write(part.inline_data.data)
                    print(f"   wrote {out} ({len(part.inline_data.data)//1024} KB)")
                    made.append(out)
                    if idx == 1 and not args.no_canonical:  # canonical default = variation 1
                        shutil.copyfile(out, os.path.join(OUT_DIR, f"{mgr}_wwe.jpg"))
                    saved = True
                    break
            if not saved:
                txt = "".join(getattr(p, "text", "") or "" for p in content.parts)
                print(f"   {mgr}_{idx}: no image returned. {txt[:160]}", file=sys.stderr)

    print(f"\nDone. {len(made)} image(s) written to assets/portraits/wwe/.")
    if skipped:
        print(f"Skipped (no reference photo on disk): {', '.join(skipped)}")


if __name__ == "__main__":
    main()
