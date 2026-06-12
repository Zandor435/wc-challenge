#!/usr/bin/env python3
"""Generate the STATIC SITE BANNER rotation pool with Nano Banana (Gemini 2.5 Flash Image).

Sibling to generate_pundit_avatars.py (the proven reference-face pattern) and
generate_match_covers.py (the editorial house style). This makes the pre-generated
decoration banners the homepage rotates through on every page load — site furniture,
NOT tied to any match result. The dynamic, match-day illustrations are a separate
script (generate_editorial_illustrations.py); both pools share the same display layer.

The hard requirement, and the thing the earlier clash-banner attempt failed at: every
banner that involves owners must show THE ACTUAL FACES from the WWE portraits, not
generic AI characters. We do that the way the pundit avatars do it — feed the real
portraits as multimodal reference images and pin the likeness hard in the prompt. The
clash banners gave up and composited with Pillow; here we try Gemini with references
first because these compositions (lineups, collages, painted covers) want generation,
not photo-collage. Any banner whose faces don't match is noted for retry / PIL fallback.

Reference faces:
    site/assets/portraits/wwe/<owner>_wwe.jpg   (zach, gunner, gayden, devin, rafe)
    assets/reference/Michael Bradley3.webp      (the mentor, banner f)

Each banner is pinned to one of five STYLE LANES (cinematic-realistic, fight-poster,
painted-editorial, stylized-composite, illustrated) baked into its prompt, so the pool
has deliberate visual variety. No text is baked in — the frontend overlays any labels.

Output: site/assets/banners/static/<file>.png   (1400x200, dark #0c0e14 aesthetic)
Output pattern: SKIP-IF-EXISTS by default (a static banner never changes once made; we
don't pay for it twice). Pass --force to regenerate, --only <id> for one, --list to plan.

Needs GEMINI_API_KEY in the environment (or in ~/.env).

Usage:
    GEMINI_API_KEY=... python generate_site_banners.py            # whole pool (skip existing)
    python generate_site_banners.py --only h_mustard_boy          # one banner
    python generate_site_banners.py --force                       # regenerate everything
    python generate_site_banners.py --list                        # print the plan, no API
    python generate_site_banners.py --dry-run --only g_brothers   # print one prompt, no API
"""
from __future__ import annotations

import argparse
import io
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))  # repo root (this script lives here)
WWE_DIR = os.path.join(ROOT, "site", "assets", "portraits", "wwe")
REF_DIR = os.path.join(ROOT, "assets", "reference")
OUT_DIR = os.path.join(ROOT, "site", "assets", "banners", "static")
MODEL = "gemini-2.5-flash-image"
BANNER_W, BANNER_H = 1400, 200  # the EXACT display size — a 7:1 cinematic letterbox
# The image model cannot natively paint a 7:1 frame; its widest output is 21:9 (~2.3:1).
# So we GENERATE at 21:9 (a wide head-and-shoulders composition) and FACE-AWARE crop a
# 7:1 slice centred on the detected faces — never a blind centre/stretch that squashes or
# guillotines heads. The prompt asks for breathing room above the heads so the slice keeps
# every whole face. Face detection is optional (OpenCV); without it we fall back to a
# top-biased crop so the nightly run never breaks.
ASPECT = "21:9"                 # the model's widest native frame (≈2.33:1)
FALLBACK_FACE_Y = 0.42          # crop centre (fraction of height) when no face is detected

# --------------------------------------------------------------------------- art direction
# Likeness clause — the whole point. These owners ARE specific people with specific WWE
# personas (face paint, mustaches, costumes). Keep the faces true to the references.
_LIKENESS = (
    "CRITICAL — USE THE ACTUAL FACES FROM THE REFERENCE PHOTOGRAPHS PROVIDED. Every "
    "person in this image must be immediately, unmistakably recognizable as the specific "
    "individual in their reference photo: same facial structure, eyes, nose, jawline, "
    "skin tone, hair, and their distinctive WWE wrestling-persona look (face paint, "
    "mustache, costume, hat exactly as shown in the reference). Do NOT invent generic "
    "faces, do NOT substitute stock models, do NOT prettify or homogenize them. If a "
    "reference shows face paint, reproduce that exact face paint. "
)

# Appended to banners that are mostly faces. The displayed banner is a 7:1 slice cut from
# the CENTRE of this wide frame, so heads must have breathing room above them or the slice
# clips their crowns. Keep faces large but centred, hair/hats fully inside, shoulders below.
_FACE_SAFE = (
    "CRITICAL FRAMING — the website shows a thin 7:1 strip cut from the VERTICAL CENTRE of "
    "this frame, so leave clear BREATHING ROOM of empty dark space ABOVE every head: do NOT "
    "jam heads, hair, hats or mohawks against the top edge. Place all faces in the central "
    "band, large and head-and-shoulders, every whole head (hair and all) plus the shoulders "
    "sitting comfortably inside the middle of the frame so the centre slice keeps each face "
    "WHOLE and uncut. "
)

# Shared baseline for EVERY banner: format, no-text rule, dark base. Style lane added
# per-banner. The model paints a WIDE 21:9 head-and-shoulders frame; the site displays the
# central 7:1 slice of it, so everything important must live in the central horizontal band.
_BASE = (
    "WIDE CINEMATIC BANNER, an extremely wide horizontal composition. The website displays "
    "an extremely WIDE 7:1 letterbox strip taken from the CENTRE of this frame, so compose "
    "for that: HEAD-AND-SHOULDERS framing only — NO full bodies, NO wasted space — with "
    "every face LARGE and in the CENTRAL horizontal band. Leave a little breathing room of "
    "empty dark space above the heads (do NOT push heads against the very top edge) and keep "
    "the shoulders toward the lower middle, so the centre slice contains every face WHOLE, "
    "none cut off. Every face, flag and bit of drama belongs in that central band. "
    "Dark, moody aesthetic on a near-black (#0c0e14) base so it sits seamlessly on a "
    "dark website. ABSOLUTELY NO TEXT, NO LETTERING, NO WORDS, NO LOGOS, NO SCORELINES "
    "baked into the image — the website overlays all labels. "
)

# The five style lanes, referenced by key in each banner spec.
LANES = {
    "cinematic": (
        "STYLE LANE — CINEMATIC REALISTIC: moody cinematic lighting, photographic, "
        "shallow depth of field, film grain, sports-documentary feel — the look of an "
        "HBO sports special or a prestige Netflix docuseries title card. Photoreal, not "
        "illustrated. "
    ),
    "fight_poster": (
        "STYLE LANE — FIGHT POSTER: a boxing / WWE pay-per-view promo poster. Hard split "
        "lighting, a dramatic face-to-face staredown, extreme high contrast, sweat-and-"
        "spotlight intensity, larger-than-life promo energy. "
    ),
    "painted": (
        "STYLE LANE — PAINTED EDITORIAL: a classic painted Sports Illustrated cover. "
        "Visible brushstrokes, watercolour-meets-oil texture, rich painterly rendering, "
        "the timeless hand-painted commemorative-poster feel. "
    ),
    "composite": (
        "STYLE LANE — STYLIZED COMPOSITE: an ESPN / FOX Sports broadcast graphic. Real "
        "cut-out portraits layered over flags, stadiums and textures, photo-collage with "
        "bold dramatic colour grading, glows and graphic energy — a power-rankings hero. "
    ),
    "illustrated": (
        "STYLE LANE — ILLUSTRATED: comic-book / graphic-novel energy. Bold inked "
        "linework, exaggerated heroic features, dynamic poses, speed lines, halftone "
        "punch, saturated cover-art colour. Stylized illustration, not a photo. "
    ),
}

# Appended to the 5-owner ENSEMBLE banners. The 7:1 centre slice only reads if the five
# faces are a single same-height row in the central band — no stacked back row, breathing
# room above the heads so none lose their crown to the crop.
_ENSEMBLE_ROW = (
    "ALL FIVE owners must be FULLY VISIBLE and clearly recognizable, arranged in a SINGLE "
    "HORIZONTAL ROW with every head on the SAME horizontal plane at the SAME height — NO "
    "front row and back row, nobody standing behind anyone, no face hidden, overlapped or "
    "cut off. Shoulders may overlap slightly for depth, but every face stays unobscured and "
    "LARGE. Five equal-billing faces in a clean line, left to right, spanning the full width "
    "of the wide strip, sitting in the central band with clear empty space above the heads. "
)

# Per-owner persona descriptors — help the model anchor each reference to the right look,
# and the team colour the brief assigns each owner.
OWNERS = {
    "zach":   {"name": "Zach",   "color": "#f4c430",
               "look": "a golden Hulk-Hogan-style wrestler in a yellow tank top and yellow "
                       "headband, with a big blonde handlebar moustache (\"Mustard Boy\")"},
    "gunner": {"name": "Gunner", "color": "#2f6dff",
               "look": "a flamboyant Macho-Man-meets-rhinestone-cowboy in a gold-and-purple "
                       "sequined fringe jacket, bedazzled cowboy hat and tinted sunglasses, "
                       "with a goatee (\"Bubba G\")"},
    "gayden": {"name": "Gayden", "color": "#28c060",
               "look": "a sinister wrestler in black-and-white Demolition-style face paint "
                       "with a black mohawk and spiked black leather"},
    "devin":  {"name": "Devin",  "color": "#f0743a",
               "look": "an Ultimate-Warrior-style wrestler in vivid neon multicolour face "
                       "paint with colourful arm tassels and blonde hair (\"Ghost Pepper\")"},
    "rafe":   {"name": "Rafe",   "color": "#a855f7",
               "look": "the youngest competitor, a lean young man with a blonde mullet "
                       "(\"The Noisemaker\")"},
}


def _owner_roster(keys):
    """Bullet list describing each owner in `keys`, in the order their refs are passed."""
    lines = []
    for i, k in enumerate(keys, 1):
        o = OWNERS[k]
        lines.append(f"  Reference photo {i} = {o['name']}: {o['look']}; team colour {o['color']}.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- banner specs
# Each banner: id (== filename stem), lane, list of reference keys (owner keys, or the
# special "bradley"), and a `scene` describing the composition. Faces come from the refs.
def _lineup_scene(keys):
    return ("All five pool owners standing shoulder to shoulder in a row, arms crossed, "
            "staring dead at the camera — a team-photo power shot. Each man lit from his "
            "side in his own team colour so the row reads as a band of five coloured "
            "spotlights (Zach gold, Gunner blue, Gayden green, Devin orange, Rafe purple).\n"
            + _ENSEMBLE_ROW + "\n" + _owner_roster(keys))


ALL5 = ["zach", "gunner", "gayden", "devin", "rafe"]

BANNERS = [
    {"id": "b_lineup_realistic", "lane": "cinematic", "refs": ALL5, "face_safe": True,
     "scene": _lineup_scene(ALL5)},
    {"id": "b2_lineup_illustrated", "lane": "illustrated", "refs": ALL5, "face_safe": True,
     "scene": _lineup_scene(ALL5) + "\nRender it with bold exaggerated comic energy."},
    {"id": "c_trophy_painted", "lane": "painted", "refs": ALL5,
     "scene": ("All five pool owners reaching in from different angles toward a single "
               "golden FIFA World Cup trophy at the centre of the frame — five hands "
               "converging, but the composition makes clear only ONE hand will actually "
               "lift it. Tension, desire, destiny. Faces ringed around the glowing "
               "trophy.\n" + _owner_roster(ALL5))},
    {"id": "c2_trophy_composite", "lane": "composite", "refs": ALL5, "face_safe": True,
     "scene": ("The five owners' real portraits cut out and arranged in a SINGLE ROW across "
               "the wide strip, a glowing golden World Cup trophy rising small at the centre "
               "BEHIND and between them (never covering a face), national-flag textures "
               "blended into the dark background, broadcast glows and graphic energy.\n"
               + _ENSEMBLE_ROW + "\n" + _owner_roster(ALL5))},
    {"id": "d_collage", "lane": "composite", "refs": ALL5, "face_safe": True,
     "scene": ("A power-rankings hero graphic: the five owners' faces side by side in one "
               "clean row across the wide strip, team flags woven through the dark "
               "background behind them, sports-media 'who's on top' energy, layered "
               "textures and a dramatic colour grade.\n"
               + _ENSEMBLE_ROW + "\n" + _owner_roster(ALL5))},
    {"id": "e_father_son_realistic", "lane": "fight_poster", "refs": ["gayden", "rafe"],
     "scene": ("Gayden and Rafe NOSE TO NOSE in an intense face-to-face staredown filling "
               "the frame. Hard split lighting: green (#28c060) raking across Gayden on "
               "the left, purple (#a855f7) on Rafe on the right. A father-son grudge "
               "match.\n" + _owner_roster(["gayden", "rafe"]))},
    {"id": "e2_father_son_illustrated", "lane": "illustrated", "refs": ["gayden", "rafe"],
     "scene": ("Gayden and Rafe nose to nose, the same father-son staredown, but as a "
               "comic-book cover: exaggerated furious expressions, dramatic inked shadows, "
               "an electric 'WHO WILL SURVIVE' splash-page energy (NO actual text).\n"
               + _owner_roster(["gayden", "rafe"]))},
    {"id": "f_mentor", "lane": "cinematic", "refs": ["bradley2"], "face_safe": True,
     "scene": ("Two men in a film-room / strategy-whiteboard setting, X's-and-O's "
               "diagrams softly lit behind them. A serious, intimate coaching moment — a "
               "mentor and his protege.\n"
               "ON THE RIGHT — Michael Bradley, the man in the REFERENCE PHOTO provided: "
               "render his face EXACTLY as the reference — a BALD head (no hair) and a "
               "short reddish-ginger beard — wearing a sharp dark suit, calm and "
               "authoritative, one arm around the younger man's shoulders.\n"
               "ON THE LEFT — a wrestler named Gayden (NOT in any reference photo; invent "
               "him from this description): a sinister wrestler in black-and-white "
               "KISS-style face paint with a tall black mohawk and spiked black leather, "
               "lit in green (#28c060), looking up to his mentor.")},
    {"id": "g_brothers", "lane": "fight_poster", "refs": ["zach", "gayden"], "face_safe": True,
     "scene": ("Zach and Gayden NOSE TO NOSE in a furious staredown — a family blood feud. "
               "Hard split lighting: gold (#f4c430) on Zach on the left, green (#28c060) "
               "on Gayden on the right. Brothers at war.\n"
               + _owner_roster(["zach", "gayden"]))},
    {"id": "h_mustard_boy", "lane": "painted", "refs": ["zach"],
     "scene": ("Zach alone, heroic, golden light washing over him, the flags of Brazil, "
               "Switzerland, Austria, Ghana, Czechia and Saudi Arabia fanning out behind "
               "him like a peacock's tail. A painted magazine-cover framing, his gold "
               "(#f4c430) the dominant note.\n" + _owner_roster(["zach"]))},
    {"id": "i_bubba_g", "lane": "composite", "refs": ["gunner"],
     "scene": ("Gunner radiating frontrunner confidence, the flag of FRANCE dominant and "
               "largest right behind him, with the flags of Senegal, Morocco, Egypt, "
               "Canada and DR Congo layered smaller behind that, a packed stadium in the "
               "background. His blue (#2f6dff) in the grade.\n" + _owner_roster(["gunner"]))},
    {"id": "j_backpass", "lane": "cinematic", "refs": ["gayden"], "face_safe": True,
     "scene": ("Gayden scheming in shadow — dark, brooding, calculating. The flag of "
               "ENGLAND dominant behind him, with the flags of Japan, Ivory Coast, Korea "
               "Republic, Turkey and Jordan layered in the shadows behind that. Intrigue, "
               "low key light, green (#28c060) edge light.\n" + _owner_roster(["gayden"]))},
    {"id": "k_ghost_pepper", "lane": "composite", "refs": ["devin"],
     "scene": ("Devin in full patriot celebration energy, the flag of the USA biggest and "
               "waving behind him, the flags of Spain, Norway, South Africa, Bosnia and "
               "Haiti layered behind that. Bold red-white-and-blue colour grade, "
               "fireworks, triumph.\n" + _owner_roster(["devin"]))},
    {"id": "l_noisemaker", "lane": "illustrated", "refs": ["rafe"], "face_safe": True,
     "scene": ("Rafe, the youngest competitor, shot from a dramatic low upward angle so he "
               "towers — chip-on-the-shoulder underdog energy. The flag of GERMANY draped "
               "over his shoulder, the flags of Ecuador, Mexico, Tunisia, Scotland and "
               "Uzbekistan behind him. His purple (#a855f7) electric in the linework.\n"
               + _owner_roster(["rafe"]))},
    {"id": "m_usa", "lane": "composite", "refs": ["devin"],
     "scene": ("USMNT celebration energy — stylized sports-photography feel, golden-hour "
               "lighting, a big American flag waving, jubilant national-team triumph. "
               "Devin (reference photo 1) woven into the celebration, his colour "
               "(#f0743a) tagged subtly through the composition rather than dominant.\n"
               + _owner_roster(["devin"]))},
]

# Bradley reference — the better of the two photos (frontal, clear features). The brief
# says: if no Bradley photo exists, skip f_mentor. It exists, so we use it.
BRADLEY_REF = "Michael Bradley3.webp"


# --------------------------------------------------------------------------- IO / API
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


def ref_path(key):
    """Resolve a reference key to a file path (owner portrait, or a Bradley photo)."""
    if key == "bradley":
        return os.path.join(REF_DIR, BRADLEY_REF)
    if key == "bradley2":
        return os.path.join(REF_DIR, "michael bradley2.webp")
    return os.path.join(WWE_DIR, f"{key}_wwe.jpg")


def load_refs(keys):
    """Open the reference photos for a banner as PIL images, in order. Returns (imgs, missing)."""
    from PIL import Image
    imgs, missing = [], []
    for k in keys:
        p = ref_path(k)
        if os.path.exists(p):
            imgs.append(Image.open(p))
        else:
            missing.append(p)
    return imgs, missing


def build_prompt(spec):
    parts = [_BASE + LANES[spec["lane"]], spec["scene"], _LIKENESS]
    if spec.get("face_safe"):
        parts.append(_FACE_SAFE)
    return "\n\n".join(parts)


def _detect_face_band(img):
    """Return (top_px, bot_px) of the band spanning every detected face (with a little
    margin for hair + shoulders), or None if OpenCV is missing or no face is found.
    Robust to the wide 21:9 ensemble shots: we union ALL face boxes so a five-owner row
    is kept whole, not centred on one face."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    try:
        arr = np.asarray(img)
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40))
        if len(faces) == 0:
            return None
        fh = float(np.mean([h for (_x, _y, _w, h) in faces]))
        top = min(y for (_x, y, _w, _h) in faces) - 0.55 * fh   # headroom: hair / hats / mohawks
        bot = max(y + h for (_x, _y, _w, h) in faces) + 0.50 * fh  # shoulders
        return top, bot
    except Exception:  # noqa: BLE001 — any cv2 hiccup -> heuristic fallback
        return None


def to_banner_png(raw_bytes):
    """Normalise the wide model output to EXACTLY BANNER_W x BANNER_H (7:1).

    The model paints a 21:9 head-and-shoulders frame; we take a 7:1 horizontal slice
    centred on the FACES (via OpenCV face detection) so every head stays whole — no
    squashing (the old stretch flattened faces) and no blind centre crop (that cut heads
    at the eyes). If no face is found / OpenCV is absent, fall back to a top-biased crop
    (faces sit high in these compositions). Returns PNG bytes."""
    from PIL import Image
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    w, h = img.size
    target = BANNER_W / BANNER_H

    if w / h > target:                      # already wider than 7:1 -> trim sides, centred
        new_w = int(round(h * target))
        left = (w - new_w) // 2
        img = img.crop((left, 0, left + new_w, h))
    else:                                   # too tall -> take a 7:1 band centred on the faces
        new_h = min(h, int(round(w / target)))
        band = _detect_face_band(img)
        cy = (band[0] + band[1]) / 2 if band else h * FALLBACK_FACE_Y
        top = int(round(cy - new_h / 2))
        top = max(0, min(top, h - new_h))   # clamp inside the image
        img = img.crop((0, top, w, top + new_h))

    img = img.resize((BANNER_W, BANNER_H), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def wide_config():
    """The GenerateContentConfig that forces the model's widest native frame (21:9), or
    None if the SDK/types aren't importable (then the model returns its default ~1:1 and
    to_banner_png still crops to 7:1 — just from a squarer source)."""
    try:
        from google.genai import types
        return types.GenerateContentConfig(image_config=types.ImageConfig(aspect_ratio=ASPECT))
    except Exception:  # noqa: BLE001
        return None


def generate_one(client, contents, config=None):
    """Call the model with one retry on an empty response. Return raw image bytes or None.
    `config` (e.g. wide_config()) pins the output aspect ratio when supplied."""
    for attempt in (1, 2):
        try:
            resp = client.models.generate_content(model=MODEL, contents=contents, config=config)
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


# --------------------------------------------------------------------------- main
def main():
    ids = [b["id"] for b in BANNERS]
    ap = argparse.ArgumentParser(description="Generate the static site banner rotation pool")
    ap.add_argument("--only", choices=ids, help="generate just one banner by id")
    ap.add_argument("--force", action="store_true", help="regenerate even if the file exists")
    ap.add_argument("--list", action="store_true", help="list the planned banners and exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the prompt(s) and exit; no API calls, no files written")
    ap.add_argument("--out-dir", default=OUT_DIR)
    args = ap.parse_args()

    targets = [b for b in BANNERS if b["id"] == args.only] if args.only else BANNERS

    if args.list:
        for b in targets:
            print(f"{b['id']:26s} [{b['lane']:12s}] refs: {', '.join(b['refs'])}")
        print(f"\n{len(targets)} banner(s). Output -> {args.out_dir}")
        return

    if args.dry_run:
        for b in targets:
            print("=" * 80)
            print(f"{b['id']}  [{b['lane']}]  refs: {', '.join(b['refs'])}")
            print("-" * 80)
            print(build_prompt(b))
        return

    key = load_env_key()
    if not key:
        print("ERROR: GEMINI_API_KEY not set (env or ~/.env).", file=sys.stderr)
        sys.exit(1)

    try:
        from google import genai
        from PIL import Image  # noqa: F401  (used in post-processing / ref loading)
    except ImportError as e:
        print(f"ERROR: missing dependency ({e}).", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=key)
    os.makedirs(args.out_dir, exist_ok=True)

    made, skipped, failed = [], [], []
    for b in targets:
        out_path = os.path.join(args.out_dir, f"{b['id']}.png")
        if os.path.exists(out_path) and not args.force:
            print(f"   skip (exists): {b['id']}.png")
            skipped.append(b["id"])
            continue

        refs, missing = load_refs(b["refs"])
        if missing:
            print(f"[skip] {b['id']}: missing reference(s) {missing}", file=sys.stderr)
            failed.append(b["id"])
            continue

        print(f"[gen ] {b['id']:26s} [{b['lane']:12s}] {len(refs)} ref(s): {', '.join(b['refs'])}")
        prompt = build_prompt(b)
        try:
            data = generate_one(client, [prompt] + refs, config=wide_config())
            if not data:
                print(f"   {b['id']}: no image returned, skipping.", file=sys.stderr)
                failed.append(b["id"])
                continue
            png = to_banner_png(data)
            with open(out_path, "wb") as f:
                f.write(png)
            print(f"   wrote {out_path} ({len(png)//1024} KB)")
            made.append(b["id"])
        except Exception as e:  # noqa: BLE001 — never let one banner break the batch
            print(f"   {b['id']}: failed ({e}), skipping.", file=sys.stderr)
            failed.append(b["id"])

    print(f"\nDone. {len(made)} written, {len(skipped)} already existed, {len(failed)} failed.")
    if made:
        print(f"  written : {', '.join(made)}")
    if failed:
        print(f"  FAILED  : {', '.join(failed)}", file=sys.stderr)


if __name__ == "__main__":
    main()
