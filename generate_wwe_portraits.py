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

_LIKENESS = ("Preserve his real face, facial features, hair, skin tone, and body "
             "build from the reference photo(s) so he is unmistakably recognizable as "
             "the same man. ")

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
                 "style wrestling icon: bright yellow tank top mid-tear-away (ripping "
                 "it off his chest), a yellow bandana tied around his head, a big bushy "
                 "blonde handlebar horseshoe mustache, deeply tanned and muscular. "
                 + _LIKENESS),
        "variations": [
            "Cupping one hand to his ear, head cocked toward a roaring crowd, mouth open in a shout.",
            "Both arms flexed in a double bicep pose, shirt shredded across his chest, defiant stare.",
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
        "refs": ["gayden_ref.jpg", "gayden_ref2.jpg"],
        "base": (_WWE + "Render the man from the reference photo(s) as a Legion of "
                 "Doom / Road Warrior-style wrestling icon: massive black spiked metal "
                 "shoulder pads, aggressive black-and-silver face paint streaked across "
                 "his eyes, a sharp mohawk, studded leather, hulking and intimidating, "
                 "menacing scowl. " + _LIKENESS),
        "variations": [
            "Fists clenched at his sides, shoulders squared, glaring straight down the lens through the smoke.",
            "One spiked shoulder dropped toward the camera mid-roar, arms flexed, pyro erupting behind him.",
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
    ap.add_argument("--max", type=int, default=2, help="variations per owner")
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
        if not refs:
            print(f"[skip] {mgr}: no reference photo in assets/reference/ "
                  f"(expected one of {cfg['refs']})")
            skipped.append(mgr)
            continue
        print(f"[gen ] {mgr} as {cfg['persona']} — {len(refs)} reference photo(s)")

        for i, variation in enumerate(cfg["variations"][: args.max], 1):
            prompt = f"{cfg['base']} {variation}"
            contents = [prompt] + refs
            try:
                resp = client.models.generate_content(model=MODEL, contents=contents)
            except Exception as e:  # noqa: BLE001
                print(f"   {mgr}_{i}: API error: {e}", file=sys.stderr)
                continue
            cand = (resp.candidates or [None])[0]
            content = getattr(cand, "content", None) if cand else None
            if not content or not getattr(content, "parts", None):
                reason = getattr(cand, "finish_reason", "unknown") if cand else "no candidates"
                print(f"   {mgr}_{i}: empty response (finish_reason={reason}); retrying once...",
                      file=sys.stderr)
                try:
                    resp = client.models.generate_content(model=MODEL, contents=contents)
                    cand = (resp.candidates or [None])[0]
                    content = getattr(cand, "content", None) if cand else None
                except Exception as e:  # noqa: BLE001
                    print(f"   {mgr}_{i}: retry API error: {e}", file=sys.stderr)
                if not content or not getattr(content, "parts", None):
                    print(f"   {mgr}_{i}: still empty, skipping.", file=sys.stderr)
                    continue
            saved = False
            for part in content.parts:
                if getattr(part, "inline_data", None) and part.inline_data.data:
                    out = os.path.join(OUT_DIR, f"{mgr}_wwe_{i}.jpg")
                    with open(out, "wb") as f:
                        f.write(part.inline_data.data)
                    print(f"   wrote {out} ({len(part.inline_data.data)//1024} KB)")
                    made.append(out)
                    if i == 1:  # canonical default = variation 1
                        shutil.copyfile(out, os.path.join(OUT_DIR, f"{mgr}_wwe.jpg"))
                    saved = True
                    break
            if not saved:
                txt = "".join(getattr(p, "text", "") or "" for p in content.parts)
                print(f"   {mgr}_{i}: no image returned. {txt[:160]}", file=sys.stderr)

    print(f"\nDone. {len(made)} image(s) written to assets/portraits/wwe/.")
    if skipped:
        print(f"Skipped (no reference photo on disk): {', '.join(skipped)}")


if __name__ == "__main__":
    main()
