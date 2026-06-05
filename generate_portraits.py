#!/usr/bin/env python3
"""Generate AI manager portraits with Nano Banana (Gemini 2.5 Flash Image).

Reads each manager's reference photo(s) from assets/reference/, asks
gemini-2.5-flash-image to render them as a famous soccer manager persona, and
saves variations to assets/portraits/ as <name>_1.jpg, <name>_2.jpg, ...

Needs GEMINI_API_KEY in the environment.

Usage:
    GEMINI_API_KEY=... python generate_portraits.py                 # all managers with refs
    python generate_portraits.py --only zach --max 1                # smoke test
    python generate_portraits.py --only devin                       # faceless persona

A manager with no reference photo on disk is skipped UNLESS it is marked
faceless (Devin / Ted Lasso), which generates from text alone.
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REF_DIR = os.path.join(HERE, "assets", "reference")
OUT_DIR = os.path.join(HERE, "assets", "portraits")
MODEL = "gemini-2.5-flash-image"

_LIKENESS = ("Preserve his real facial features, hair, skin tone, and body build "
             "from the reference photo(s) so he is clearly recognizable. ")

MANAGERS = {
    "zach": {
        "persona": "José Mourinho — 'The Special One'",
        "refs": ["zach_ref_pink.jpg", "zach_ref_mullet.jpg", "zach_ref_purple.jpg"],
        "faceless": False,
        "base": ("Photorealistic cinematic portrait. Render the man from the reference "
                 "photo(s) as a world-famous soccer manager in the mold of José Mourinho, "
                 "'The Special One'. He wears a dark navy wool overcoat over a crisp shirt, "
                 "arms crossed, with a smug, supremely confident smirk. " + _LIKENESS),
        "variations": [
            "Standing in a stadium tunnel before kickoff, dramatic moody side-lighting.",
            "At a press-conference podium crowded with microphones, leaning back smugly.",
            "On the touchline at night under bright floodlights, blurred crowd behind him.",
        ],
    },
    "gunner": {
        "persona": "Jesse Marsch — intense American coach abroad",
        "refs": ["gunner_ref.jpg"],
        "faceless": False,
        "base": ("Photorealistic gritty sports-photojournalism portrait. Render the man "
                 "from the reference photo(s) as an intense American soccer head coach in "
                 "the mold of Jesse Marsch, mid-shout and animated, pointing aggressively "
                 "toward the pitch, full of passion, wearing a team training jacket / "
                 "tracksuit. " + _LIKENESS),
        "variations": [
            "Caught mid-yell in pouring rain, finger jabbing toward the field.",
            "Clapping and barking instructions on a wet, floodlit sideline.",
            "Crouched on the touchline screaming, rain dripping off his jacket.",
        ],
    },
    "gayden": {
        "persona": "Pep Guardiola — tactical genius",
        "refs": ["gayden_ref.jpg"],
        "faceless": False,
        "base": ("Photorealistic premium editorial portrait. Render the man from the "
                 "reference photo(s) as a cerebral tactical-genius manager in the mold of "
                 "Pep Guardiola. He wears a fitted black turtleneck, stone-faced and "
                 "intense. Clean, minimal modern backdrop, soft even lighting. " + _LIKENESS),
        "variations": [
            "Hand thoughtfully touching his chin, plain dark-grey studio backdrop.",
            "Arms folded, focused intense stare, minimalist setting.",
            "Hands clasped behind his back, pacing a tunnel, deep in thought.",
        ],
    },
    "devin": {
        "persona": "Ted Lasso — wholesome, in over his head",
        "refs": ["devin_ref.jpg"],     # optional; faceless fallback if absent
        "faceless": True,
        "base": ("Photorealistic warm portrait of a friendly, wholesome, hopelessly "
                 "out-of-his-depth-but-lovable American soccer coach in the mold of Ted "
                 "Lasso: big genuine smile, enthusiastic thumbs up, neat mustache, blue "
                 "training tracksuit with a whistle. A generic friendly Caucasian man in "
                 "his mid-30s. Bright, optimistic, heart-warming."),
        "base_with_ref": ("Photorealistic warm portrait. Render the man from the reference "
                          "photo(s) as a wholesome, out-of-his-depth-but-lovable American "
                          "soccer coach in the mold of Ted Lasso: big genuine smile, "
                          "enthusiastic thumbs up, mustache, blue training tracksuit with a "
                          "whistle. Bright and optimistic. " + _LIKENESS),
        "variations": [
            "Two thumbs up, beaming, in a team locker room.",
            "On a sunny practice pitch, big grin, hands on hips.",
            "Holding a cup of tea, warm awkward smile, in the clubhouse.",
        ],
    },
}


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
    ap.add_argument("--only", choices=list(MANAGERS), help="generate just one manager")
    ap.add_argument("--max", type=int, default=3, help="max variations per manager")
    args = ap.parse_args()

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("ERROR: GEMINI_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    from google import genai
    client = genai.Client(api_key=key)
    os.makedirs(OUT_DIR, exist_ok=True)

    targets = [args.only] if args.only else list(MANAGERS)
    made, skipped = [], []
    for mgr in targets:
        cfg = MANAGERS[mgr]
        refs = load_refs(cfg)
        if not refs and not cfg["faceless"]:
            print(f"[skip] {mgr}: no reference photo in assets/reference/ "
                  f"(expected one of {cfg['refs']})")
            skipped.append(mgr)
            continue
        base = cfg.get("base_with_ref", cfg["base"]) if refs else cfg["base"]
        ref_note = f"with {len(refs)} reference photo(s)" if refs else "faceless (no reference)"
        print(f"[gen ] {mgr} as {cfg['persona']} — {ref_note}")

        for i, variation in enumerate(cfg["variations"][: args.max], 1):
            prompt = f"{base} {variation} Vertical portrait orientation, head and torso."
            contents = [prompt] + refs
            try:
                resp = client.models.generate_content(model=MODEL, contents=contents)
            except Exception as e:  # noqa: BLE001
                print(f"   {mgr}_{i}: API error: {e}", file=sys.stderr)
                continue
            saved = False
            for part in resp.candidates[0].content.parts:
                if getattr(part, "inline_data", None) and part.inline_data.data:
                    out = os.path.join(OUT_DIR, f"{mgr}_{i}.jpg")
                    with open(out, "wb") as f:
                        f.write(part.inline_data.data)
                    print(f"   wrote {out} ({len(part.inline_data.data)//1024} KB)")
                    made.append(out)
                    saved = True
                    break
            if not saved:
                txt = "".join(getattr(p, "text", "") or "" for p in resp.candidates[0].content.parts)
                print(f"   {mgr}_{i}: no image returned. {txt[:160]}", file=sys.stderr)

    print(f"\nDone. {len(made)} image(s) written to assets/portraits/.")
    if skipped:
        print(f"Skipped (no reference photo on disk): {', '.join(skipped)}")


if __name__ == "__main__":
    main()
