#!/usr/bin/env python3
"""One-command pipeline: (optionally fetch) -> score -> write site/data.

Examples:
    # rebuild the site from the committed fake test results:
    python scripts/build.py --results data/fake_results.json

    # fetch live 2026 results from API-Football, then score:
    python scripts/build.py --fetch --season 2026

This is what a cron job / GitHub Action would call. After it runs, commit and
push site/data/*.json and Netlify redeploys automatically.
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(cmd):
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=HERE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=os.path.join("data", "fake_results.json"))
    ap.add_argument("--fetch", action="store_true", help="fetch live results first")
    ap.add_argument("--season", type=int, default=2026)
    args = ap.parse_args()

    results = args.results
    if args.fetch:
        results = os.path.join("data", "live_results.json")
        run([sys.executable, "fetch_results.py", "--season", str(args.season), "--out", results])

    run([sys.executable, "scoring.py", "--results", results,
         "--out-dir", os.path.join("site", "data")])
    print("\nBuild complete. Commit + push site/data/*.json to redeploy on Netlify.")


if __name__ == "__main__":
    main()
