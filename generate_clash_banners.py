#!/usr/bin/env python3
"""Generate the 10 OWNER CLASH banners by COMPOSITING the real WWE portraits.

Earlier this script asked Gemini (Nano Banana) to *paint* a fight poster from the two
owners' WWE photos as references. That route kept inventing generic faces — the output
was unmistakably "AI poster", not "these two guys". The hard requirement here is that the
banner is recognizably the two owners, so we drop generation entirely and COMPOSITE their
actual portraits with Pillow/numpy. Less painterly, but the faces are guaranteed correct.

Approach (no API, deterministic, idempotent):
  * Head-and-shoulders crop of each owner's WWE portrait (the portraits are full-body on
    near-black backgrounds with spark/smoke FX; we keep the face + chest).
  * Left owner anchored to the LEFT half, right owner to the RIGHT half of a 1400x200
    canvas, blended across a soft central seam.
  * Each half washed in that owner's draft colour via a SCREEN-blended colour gradient —
    because the backgrounds are near-black, screen lights the background/FX in the owner's
    hue while leaving the bright faces readable (so the tint never muddies the likeness).
  * A bright collision flare down the centre seam, plus a vignette. NO text is baked in —
    the frontend overlays teams/time/group on top.

Inputs:
    site/assets/portraits/wwe/<owner>_wwe.jpg

Output pattern: OVERWRITE by default (idempotent; rerun safely). Wide 1400x200 banners:
    site/assets/clash-banners/<a>-vs-<b>.png   (owners alphabetical, lowercase)

No API key required — this is pure local image compositing.

Usage:
    python generate_clash_banners.py                      # all 10 pairings
    python generate_clash_banners.py --only devin-vs-zach # one pairing
    python generate_clash_banners.py --list               # list pairings and exit
"""
from __future__ import annotations

import argparse
import itertools
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.abspath(__file__))  # repo root (this script lives here)
WWE_DIR = os.path.join(ROOT, "site", "assets", "portraits", "wwe")
OUT_DIR = os.path.join(ROOT, "site", "assets", "clash-banners")

BANNER_W, BANNER_H = 1400, 200   # the EXACT banner-slot display size (7:1 cinematic strip)
HALF_W = 740                     # each fighter's image width (overlaps the seam)
SEAM_X = BANNER_W // 2           # central collision line
OVERLAP = (2 * HALF_W) - BANNER_W  # = 80px feathered blend zone at the seam

# The five owners. `color` is the draft colour the half is washed in. `face` is the
# vertical centre of the head as a fraction of portrait height; `band` is where that
# face should sit within the cropped head-and-shoulders band (0 = top, 1 = bottom).
# Tuned by eye against each portrait so the face lands in the upper-middle of the half.
OWNERS = {
    "zach":   {"name": "Zach",   "color": "#f4c430", "face": 0.14, "band": 0.40},
    "gunner": {"name": "Gunner", "color": "#2f6dff", "face": 0.17, "band": 0.40},
    "gayden": {"name": "Gayden", "color": "#28c060", "face": 0.26, "band": 0.52},
    "devin":  {"name": "Devin",  "color": "#f0743a", "face": 0.15, "band": 0.42},
    "rafe":   {"name": "Rafe",   "color": "#a855f7", "face": 0.16, "band": 0.40},
}


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def portrait_path(key):
    return os.path.join(WWE_DIR, f"{key}_wwe.jpg")


def head_band(key):
    """Crop a head-and-shoulders band from an owner's portrait and return it as a
    HALF_W x BANNER_H RGB image (the face placed per the owner's `band` fraction)."""
    cfg = OWNERS[key]
    img = Image.open(portrait_path(key)).convert("RGB")
    w, h = img.size
    aspect = HALF_W / BANNER_H                 # 3.7 : 1 band (wide 7:1 strip, two halves)

    # Widest band of the target aspect that fits, centred horizontally.
    bw = w
    bh = bw / aspect
    if bh > h:                                 # portrait too short -> limit by height
        bh = h
        bw = bh * aspect
    bw, bh = int(round(bw)), int(round(bh))
    left = (w - bw) // 2

    face_y = cfg["face"] * h
    top = int(round(face_y - cfg["band"] * bh))
    top = max(0, min(top, h - bh))             # clamp inside the image

    band = img.crop((left, top, left + bw, top + bh))
    return band.resize((HALF_W, BANNER_H), Image.LANCZOS)


def colour_wash(base, rgb, side):
    """SCREEN-blend a colour gradient onto `base` (a HxWx3 float array, 0-255).
    `side` is 'left' or 'right'; the wash is strongest at the OUTER edge and fades to
    nothing at the centre seam, so the faces near centre stay clean. Returns a new array."""
    h, w, _ = base.shape
    xs = np.linspace(0.0, 1.0, w)             # 0 at left edge, 1 at right edge
    if side == "left":
        g = np.clip(1.0 - xs / 0.52, 0.0, 1.0)   # strong far-left -> 0 near centre
    else:
        g = np.clip((xs - 0.48) / 0.52, 0.0, 1.0)  # 0 near centre -> strong far-right
    g = (g ** 1.25)[None, :, None]            # ease the falloff; broadcast to HxWx1
    colour = np.array(rgb, dtype=np.float32)[None, None, :]
    layer = colour * g                        # colour scaled by per-column strength
    # screen: 255 - (255-base)(255-layer)/255
    return 255.0 - (255.0 - base) * (255.0 - layer) / 255.0


def vignette(size, strength=0.45):
    """Return an 'L' mask that's bright in the centre and dark at the edges/corners."""
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    cx, cy = w / 2.0, h / 2.0
    d = np.sqrt(((xx - cx) / cx) ** 2 + ((yy - cy) / cy) ** 2)  # 0 centre -> ~1.4 corner
    m = np.clip(1.0 - strength * (d ** 2.2), 0.0, 1.0)
    return Image.fromarray((m * 255).astype(np.uint8), "L")


def seam_flare():
    """A bright vertical collision flare centred on the seam, as an RGB array to screen on."""
    yy, xx = np.mgrid[0:BANNER_H, 0:BANNER_W].astype(np.float32)
    dx = np.abs(xx - SEAM_X)
    core = np.exp(-(dx ** 2) / (2 * 34.0 ** 2)) * 0.85   # hot core (softened)
    glow = np.exp(-(dx ** 2) / (2 * 110.0 ** 2)) * 0.45  # wider soft glow
    # fade the flare toward the very top/bottom so it reads as a beam, not a bar
    vy = np.sin(np.clip(yy / BANNER_H, 0, 1) * np.pi) ** 0.5
    inten = np.clip((core + glow) * vy, 0.0, 1.0)[:, :, None]
    warm = np.array([255, 245, 225], dtype=np.float32)[None, None, :]
    return inten * warm


def build_banner(a, b):
    """Composite the a-vs-b banner (a = left, b = right). Returns a PIL RGB image."""
    A, B = OWNERS[a], OWNERS[b]

    # --- 1. lay the two head-and-shoulders crops onto the canvas, feathered at the seam.
    canvas = Image.new("RGB", (BANNER_W, BANNER_H), (8, 8, 12))
    left_img = head_band(a)
    right_img = head_band(b)

    canvas.paste(left_img, (0, 0))            # left fills x[0..640]

    # feather the right image's inner (left) edge across the overlap so the two blend.
    mask = Image.new("L", (HALF_W, BANNER_H), 255)
    md = ImageDraw.Draw(mask)
    for x in range(OVERLAP):
        md.line([(x, 0), (x, BANNER_H)], fill=int(255 * (x / OVERLAP)))
    canvas.paste(right_img, (BANNER_W - HALF_W, 0), mask)   # right fills x[660..1400]

    arr = np.asarray(canvas, dtype=np.float32)

    # --- 2. colour-wash each half (screen blend, strongest at the outer edges).
    arr = colour_wash(arr, hex_rgb(A["color"]), "left")
    arr = colour_wash(arr, hex_rgb(B["color"]), "right")

    # --- 3. collision flare down the seam (screen blend).
    flare = seam_flare()
    arr = 255.0 - (255.0 - arr) * (255.0 - flare) / 255.0

    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")

    # --- 4. vignette (multiply by the centre-bright mask) + a touch of contrast pop.
    vmask = vignette((BANNER_W, BANNER_H)).filter(ImageFilter.GaussianBlur(40))
    varr = np.asarray(out, dtype=np.float32) * (np.asarray(vmask, dtype=np.float32)[:, :, None] / 255.0)
    out = Image.fromarray(np.clip(varr, 0, 255).astype(np.uint8), "RGB")
    return out


def all_pairings():
    """10 alphabetical owner pairings as (a, b) key tuples."""
    return list(itertools.combinations(sorted(OWNERS), 2))


def main():
    pairings = all_pairings()
    slugs = [f"{a}-vs-{b}" for a, b in pairings]
    ap = argparse.ArgumentParser(description="Composite the 10 owner-clash fight banners")
    ap.add_argument("--only", choices=slugs, help="generate just one pairing, e.g. devin-vs-zach")
    ap.add_argument("--list", action="store_true", help="list pairings and exit")
    args = ap.parse_args()

    if args.list:
        print("\n".join(slugs))
        return

    targets = [tuple(args.only.split("-vs-"))] if args.only else pairings
    os.makedirs(OUT_DIR, exist_ok=True)

    made, failed = [], []
    for a, b in targets:
        slug = f"{a}-vs-{b}"
        pa, pb = portrait_path(a), portrait_path(b)
        if not os.path.exists(pa) or not os.path.exists(pb):
            miss = pa if not os.path.exists(pa) else pb
            print(f"[skip] {slug}: missing portrait ({miss})", file=sys.stderr)
            failed.append(slug)
            continue
        print(f"[gen ] {slug}  ({OWNERS[a]['name']} {OWNERS[a]['color']} vs "
              f"{OWNERS[b]['name']} {OWNERS[b]['color']})")
        img = build_banner(a, b)
        out = os.path.join(OUT_DIR, f"{slug}.png")
        img.save(out, format="PNG")
        print(f"   wrote {out} ({os.path.getsize(out)//1024} KB)")
        made.append(slug)

    print(f"\nDone. {len(made)}/{len(targets)} banner(s) written under site/assets/clash-banners/.")
    if failed:
        print(f"Failed/skipped: {', '.join(failed)}", file=sys.stderr)


if __name__ == "__main__":
    main()
