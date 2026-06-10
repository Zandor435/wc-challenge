#!/usr/bin/env python3
"""Generate satirical "Pundit Takes" avatar variants with Nano Banana (Gemini 2.5 Flash Image).

Sibling to generate_portraits.py / generate_wwe_portraits.py, but for the PUNDIT TAKES
strip — the Babylon Bee-style satirical news cards. Each soccer-commentator pundit gets
several portrait variants the site rotates through (daily / per take); the joke is the
variety — same guy, a slightly different unflattering look every time.

Reads reference photos from assets/reference/ (likeness reference ONLY — outputs are
generated portraits, not edits of the source) and writes square PNG variants to:

    pundit_avatars/
      wynalda/  wynalda_v1.png  wynalda_v2.png ...
      dempsey/  dempsey_v1.png  ...
      lalas/    lalas_v1.png    ...
      donovan/  donovan_v1.png  ...

Output pattern: OVERWRITE by default (idempotent; rerun safely). Each variant index maps
to a fixed pose/bit, so re-running regenerates the same slot.

Outputs are square PNGs, upscaled/cropped to >=512x512 (they're cropped to circles and
scaled to ~36-48px on the site, but we keep resolution for 2x retina).

Needs GEMINI_API_KEY in the environment (or in ~/.env).

Usage:
    GEMINI_API_KEY=... python generate_pundit_avatars.py            # all pundits, all variants
    python generate_pundit_avatars.py --only wynalda                # one pundit
    python generate_pundit_avatars.py --only lalas --start 3 --count 1   # just lalas_v3
"""
from __future__ import annotations

import argparse
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REF_DIR = os.path.join(HERE, "assets", "reference")
OUT_DIR = os.path.join(HERE, "pundit_avatars")
MODEL = "gemini-2.5-flash-image"
MIN_SIZE = 512  # final square PNGs are at least this many px per side

# Likeness clause — these ARE real, recognizable people. Keep the face true; only the
# expression/pose/wardrobe and the exaggeration knob change.
_LIKENESS = ("CRITICAL: keep the same recognizable face as the reference photo(s) — same "
             "eyes, nose, mouth, jawline, eyebrows, face shape, hairline and skin tone — "
             "so he is immediately identifiable as that specific man. Do NOT invent a "
             "generic face or substitute a stock model. ")

# Shared art direction for EVERY pundit avatar. Photorealistic but pushed — a celebrity
# caricature that stopped just short of cartoon. The "unflattering magazine cover", not
# the "editorial cartoon".
_STYLE = ("Square 1:1 head-and-shoulders portrait, face centered and filling the frame, "
          "shot like a real TV-broadcast headshot or press photo — sharp studio lighting, "
          "suit and tie, a soccer broadcast desk or stadium tunnel softly blurred behind "
          "him. Photorealistic and high-resolution, magazine-cover quality. BUT pushed "
          "like a celebrity caricature that stopped JUST short of becoming a cartoon: "
          "exaggerate his most distinguishing features by about 15-20 percent so something "
          "is subtly, unflatteringly OFF about him — never a clean flattering glamour shot. "
          "Keep it photoreal, not an illustration or cartoon. ")

PUNDITS = {
    # ERIC WYNALDA — arrogant, pompous, smartest-guy-in-the-room. Exaggerate jaw, squint
    # lines, the perpetual "I'm better than you" micro-expression.
    "wynalda": {
        "name": "Eric Wynalda",
        "refs": ["wynalda.webp", "wynalda 3.webp"],
        "base": (_STYLE + "This is sports pundit Eric Wynalda, rendered as arrogant, "
                 "pompous and self-important — a man utterly convinced he is the smartest "
                 "person in every room, and his face shows it. Exaggerate his jaw "
                 "definition, the squint lines around his eyes, and the perpetual "
                 "condescending 'I'm better than you' micro-expression. " + _LIKENESS),
        "variations": [
            "Smug closed-mouth half-smile, chin slightly raised so he is looking down his "
            "nose at the camera. Perfectly styled hair, a bold power tie.",
            "Caught mid-lecture, one eyebrow raised high in disbelief like he cannot "
            "believe he has to explain this to you. Exaggerate the condescension.",
            "Arms crossed over his chest, tight-lipped superiority, radiating 'I played "
            "professionally and you didn't' energy.",
            "Fake-laughing at someone else's bad take — mouth open in a laugh that "
            "absolutely does not reach his cold eyes.",
            "Leaning back in a broadcast chair, hands laced behind his head, smug "
            "king-of-the-world posture.",
        ],
    },
    # CLINT DEMPSEY — street, gritty, rapper-who-does-soccer-commentary (he really did
    # record rap tracks). Exaggerate brow ridge, jawline, the permanent scowl.
    "dempsey": {
        "name": "Clint Dempsey",
        "refs": ["dempsey.webp", "demps.webp", "clint-dempsey-727x727-1.webp"],
        "base": (_STYLE + "This is sports pundit Clint Dempsey, rendered with a street, "
                 "gritty, rapper-who-happens-to-do-soccer-commentary duality — broadcast "
                 "desk meets recording studio. He looks like he could headbutt you at any "
                 "moment. Exaggerate his heavy brow ridge, his hard jawline, and his "
                 "permanent scowl. " + _LIKENESS),
        "variations": [
            "Standard broadcast headshot but a thick gold chain is visible above his "
            "collar. Dead-serious unblinking stare.",
            "Wearing a hoodie underneath a blazer, a slight scowl, like he might leave "
            "the desk to fight someone.",
            "Backwards cap, stubble heavier than usual, an earpiece in — looks like he is "
            "about to drop a freestyle and a halftime take at the same time.",
            "Clean broadcast look but the expression is pure 'I don't want to be here', "
            "with zero media-trained smile.",
            "Slightly shadowy moody lighting, intense direct eye contact — looks like a "
            "rap album cover that accidentally ended up on a sports network.",
        ],
    },
    # ALEXI LALAS — famous '94 World Cup red mane + goatee. The bit: he keeps showing up
    # with a different ridiculous facial-hair era. Exaggerate hair redness, facial-hair
    # volume, and the gap between his sharp suit and his chaotic hair.
    "lalas": {
        "name": "Alexi Lalas",
        "refs": ["lalas.webp", "young lalas.webp", "young lalas2.webp",
                 "Alexi-Lalas_World-Cup-Qualifier_1040x585-2-300x257.webp"],
        "base": (_STYLE + "This is sports pundit Alexi Lalas. The running joke is his "
                 "facial hair: he keeps turning up in a different absurd era of red hair "
                 "and beard. Exaggerate the REDNESS of his hair, the VOLUME of whatever "
                 "facial hair is present, and the comedic gap between how sharp and "
                 "put-together his modern suit is versus how chaotic his hair/beard is. "
                 + _LIKENESS),
        "variations": [
            "Full 1994 mode — enormous curly bright-red hair and a long red goatee — but "
            "wearing a sharp modern suit and tie. A time traveler at the broadcast desk.",
            "Modern clean-cut Lalas but with an unexplained waxed handlebar mustache. "
            "Otherwise completely professional.",
            "A buzz cut up top but a massive red ZZ-Top beard flowing down. Suit and tie, "
            "completely serious deadpan expression.",
            "Current-era Lalas — short reddish hair, normally-trim beard — except the "
            "beard has gotten slightly out of control, about two months past a trim, and "
            "he hasn't noticed.",
            "Fully bald on top but the '94 red curls are back on the sides as a "
            "skullet/mullet, his goatee braided. He clearly thinks he looks great.",
        ],
    },
    # LANDON DONOVAN — vain about his hair, widely joked to wear a piece. Every variant =
    # a different obviously-bad hair situation, worn with total oblivious confidence.
    "donovan": {
        "name": "Landon Donovan",
        "refs": ["donovan.webp", "donovan hair.webp", "donavan w tupay.webp"],
        "base": (_STYLE + "This is sports pundit Landon Donovan. The running joke is his "
                 "HAIR — he is vain about it and it is widely speculated he wears a "
                 "hairpiece, and it never quite works. Every version has a different, "
                 "obviously-bad hair situation that he wears with total oblivious "
                 "confidence — he is trying SO hard and it is not working. Exaggerate the "
                 "hairline mismatch, the slightly-too-perfect hair texture against his "
                 "natural skin, and his confident expression despite the obvious situation "
                 "on top of his head. " + _LIKENESS),
        "variations": [
            "A toupee sitting slightly too far forward on his head, its color not quite "
            "matching his sideburns. He is smiling like nothing is wrong.",
            "Wind has caught the hairpiece and it is lifting up at one edge. He is "
            "mid-sentence and hasn't noticed yet.",
            "A different toupee — too dark, too thick, too young for his face. He looks "
            "25 from the forehead up and 40 from the eyebrows down.",
            "He has gone the shaved-head route, but you can see the faint tan line and "
            "glue residue where the piece usually sits. Trying to own it and failing.",
            "The most expensive, highest-quality hairpiece yet — but styled in completely "
            "the wrong era: full 1980s news-anchor blow-dry on a modern broadcast set. He "
            "finally spent the money and still got it wrong.",
        ],
    },
}


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


def load_refs(cfg):
    from PIL import Image
    imgs = []
    for name in cfg["refs"]:
        p = os.path.join(REF_DIR, name)
        if os.path.exists(p):
            imgs.append(Image.open(p))
        else:
            print(f"   [warn] missing reference photo: {name}", file=sys.stderr)
    return imgs


def to_square_png(raw_bytes):
    """Center-crop the model output to a square and upscale to >=MIN_SIZE; return PNG bytes."""
    from PIL import Image
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    w, h = img.size
    side = min(w, h)
    left, top = (w - side) // 2, (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    if side < MIN_SIZE:
        img = img.resize((MIN_SIZE, MIN_SIZE), Image.LANCZOS)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=list(PUNDITS), help="generate just one pundit")
    ap.add_argument("--start", type=int, default=1,
                    help="first variant index (the N in <name>_vN.png)")
    ap.add_argument("--count", type=int, default=None,
                    help="how many variants to generate (default: all 5 bits)")
    args = ap.parse_args()

    key = load_env_key()
    if not key:
        print("ERROR: GEMINI_API_KEY not set (env or ~/.env).", file=sys.stderr)
        sys.exit(1)

    from google import genai
    client = genai.Client(api_key=key)

    targets = [args.only] if args.only else list(PUNDITS)
    made = []
    for pid in targets:
        cfg = PUNDITS[pid]
        refs = load_refs(cfg)
        if not refs:
            print(f"[skip] {pid}: no reference photos found in assets/reference/")
            continue
        out_sub = os.path.join(OUT_DIR, pid)
        os.makedirs(out_sub, exist_ok=True)

        poses = cfg["variations"]
        count = args.count if args.count is not None else len(poses)
        print(f"[gen ] {cfg['name']} ({pid}) — {len(refs)} reference photo(s), {count} variant(s)")
        for j in range(count):
            idx = args.start + j
            variation = poses[(idx - 1) % len(poses)]
            prompt = f"{cfg['base']} POSE/LOOK FOR THIS VARIANT: {variation}"
            data = generate_one(client, [prompt] + refs)
            if not data:
                print(f"   {pid}_v{idx}: failed, skipping.", file=sys.stderr)
                continue
            png = to_square_png(data)
            out = os.path.join(out_sub, f"{pid}_v{idx}.png")
            with open(out, "wb") as f:
                f.write(png)
            print(f"   wrote {out} ({len(png)//1024} KB)")
            made.append(out)

    print(f"\nDone. {len(made)} avatar(s) written under pundit_avatars/.")


if __name__ == "__main__":
    main()
