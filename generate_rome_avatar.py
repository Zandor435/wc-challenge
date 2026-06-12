#!/usr/bin/env python3
"""Generate Jim Rome's circular avatar with Nano Banana (Gemini 2.5 Flash Image).

Rome anchors THE COLUMN (the rolling narrative on the homepage); he is NOT one of the
four Pundit Takes voices, but his avatar must sit in the SAME visual language as the
pundit headshots (generate_pundit_avatars.py) so it reads as the same broadcast set —
a square, photoreal-but-pushed celebrity-caricature headshot, cropped to a circle on
the site at ~36px.

Reference (likeness ONLY — the output is a generated portrait, not an edit of the source):
    assets/reference/JimRomesDailyJG16x9_Medium.webp   (left side = his head/shoulders;
    the show-logo lettering on the right is cropped off before it's handed to the model)

Output: OVERWRITE by default (idempotent). Square PNG, >=512px:
    site/assets/portraits/pundits/rome/rome.png

Needs GEMINI_API_KEY in the environment (or in ~/.env).

Usage:
    python generate_rome_avatar.py
    python generate_rome_avatar.py --dry-run   # print the prompt, no API call
"""
from __future__ import annotations

import argparse
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.join(HERE, "assets", "reference", "JimRomesDailyJG16x9_Medium.webp")
OUT = os.path.join(HERE, "site", "assets", "portraits", "pundits", "rome", "rome.png")
MODEL = "gemini-2.5-flash-image"
MIN_SIZE = 512

# Same likeness + style clauses as the pundit-avatar generator, so Rome matches the set.
_LIKENESS = ("CRITICAL: keep the same recognizable face as the reference photo — same "
             "eyes, nose, mouth, jawline, eyebrows, face shape, hairline, the greying "
             "brown hair and the trim greying goatee — so he is immediately identifiable "
             "as that specific man. Do NOT invent a generic face or substitute a stock model. ")

_STYLE = ("Square 1:1 head-and-shoulders portrait, face centered and filling the frame, "
          "shot like a real TV-broadcast headshot or press photo — sharp studio lighting, "
          "suit and tie, a sports-talk broadcast desk or studio softly blurred behind him. "
          "Photorealistic and high-resolution, magazine-cover quality. BUT pushed like a "
          "celebrity caricature that stopped JUST short of becoming a cartoon: exaggerate "
          "his most distinguishing features by about 15-20 percent so something is subtly, "
          "knowingly OFF about him. Keep it photoreal, not an illustration or cartoon. ")

# Rome's persona: the king of sports-talk radio. Supreme, quotable, smirking confidence.
_PROMPT = (_STYLE +
           "This is sports-talk host Jim Rome, rendered as supremely confident and "
           "self-assured — the undisputed king of the broadcast. Sharp, well-tailored "
           "modern suit jacket over an open-collar shirt; a knowing, slightly smug smirk; "
           "chin level, locked unblinking eye contact, total 'I own this desk and this "
           "show, and you're going to quote me' energy. Exaggerate the smirk and the "
           "self-satisfied confidence. " + _LIKENESS)


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


def load_ref():
    """Open the reference and crop to his head/shoulders (the left ~half of the 16:9
    promo image) so the show lettering on the right doesn't bleed into the prompt."""
    from PIL import Image
    img = Image.open(REF).convert("RGB")
    w, h = img.size
    return img.crop((int(w * 0.02), 0, int(w * 0.52), h))


def to_square_png(raw_bytes):
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
    ap = argparse.ArgumentParser(description="Generate Jim Rome's avatar")
    ap.add_argument("--dry-run", action="store_true", help="print the prompt and exit")
    args = ap.parse_args()

    if args.dry_run:
        print(_PROMPT)
        return

    if not os.path.exists(REF):
        print(f"ERROR: reference not found: {REF}", file=sys.stderr)
        sys.exit(1)

    key = load_env_key()
    if not key:
        print("ERROR: GEMINI_API_KEY not set (env or ~/.env).", file=sys.stderr)
        sys.exit(1)

    from google import genai
    client = genai.Client(api_key=key)

    ref = load_ref()
    print("[gen ] Jim Rome avatar (1 reference photo)")
    data = generate_one(client, [_PROMPT, ref])
    if not data:
        print("   failed.", file=sys.stderr)
        sys.exit(1)
    png = to_square_png(data)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "wb") as f:
        f.write(png)
    print(f"   wrote {OUT} ({len(png)//1024} KB)")


if __name__ == "__main__":
    main()
