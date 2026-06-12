#!/usr/bin/env python3
"""Build site/data/banner_manifest.json — the flat list of banners the homepage rotates.

The frontend (site/app.js renderBanner) picks ONE random banner from this manifest on
every page load. Two pools feed it:
    site/assets/banners/static/    16-ish pre-generated decoration banners (fixed)
    site/assets/banners/dynamic/   match-day editorial illustrations (grows nightly)

This script just scans both directories and writes the union as web paths relative to
the site root (what the browser fetches), e.g. "assets/banners/static/d_collage.png".
Static first, then dynamic, each sorted — so the file is stable/diff-friendly and the
nightly commit only changes it when a new dynamic banner actually appears.

Pipeline position: runs nightly AFTER generate_editorial_illustrations.py so any new
dynamic banner joins the pool the same night it is created. Stdlib only; never raises.

Output pattern: OVERWRITE by default (idempotent; a fresh projection of the two dirs).

Usage:
    python build_banner_manifest.py
    python build_banner_manifest.py --pretty     # human-readable (one path per line)
"""
from __future__ import annotations

import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
BANNERS_ROOT = os.path.join(HERE, "site", "assets", "banners")
OUT = os.path.join(HERE, "site", "data", "banner_manifest.json")

# Web paths are relative to the site root (site/), which is where index.html is served.
POOLS = ["static", "dynamic"]
EXTS = (".png", ".jpg", ".jpeg", ".webp")


def scan_pool(pool):
    d = os.path.join(BANNERS_ROOT, pool)
    if not os.path.isdir(d):
        return []
    files = [f for f in os.listdir(d) if f.lower().endswith(EXTS)]
    return [f"assets/banners/{pool}/{f}" for f in sorted(files)]


def main():
    ap = argparse.ArgumentParser(description="Build the banner rotation manifest")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--pretty", action="store_true", help="indent the JSON for readability")
    args = ap.parse_args()

    banners = []
    for pool in POOLS:
        banners.extend(scan_pool(pool))

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(banners, f, indent=2 if args.pretty else None, ensure_ascii=False)
        f.write("\n")

    static_n = sum(1 for b in banners if "/static/" in b)
    dynamic_n = len(banners) - static_n
    print(f"Wrote {args.out} — {len(banners)} banner(s): {static_n} static, {dynamic_n} dynamic.")


if __name__ == "__main__":
    main()
