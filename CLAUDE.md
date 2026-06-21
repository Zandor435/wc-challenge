# Build Principles

Architectural learnings carried out of this project. **These are patterns for future
builds, not documentation of this one** — read them before starting the next data
pipeline + site, and apply the ones that fit.

---

## Architecture

- **One repo, one engine, two entry points.** A calibrator (offline tuning) and a live
  pipeline are two faces of the *same* simulation core, not two projects. Put the engine
  in one shared package; make the calibrator and the live runner thin entry points over
  it. The calibrator's output (the tuned config) should *be* the live runner's input.
- **Never vendor a fork of your own code.** Copying the engine into a second project so it
  can "have its own copy" guarantees silent drift — the calibrator proves rule set X is
  fair while the site quietly runs X′. If two things need the same logic, they import the
  same module.
- **Static site renders JSON, computes nothing.** All logic lives in Python scripts that
  emit JSON; the site only reads and displays. Cheap to host, trivial to debug, and the
  data is inspectable without running the app.

## Data

- **One canonical format. Pick JSON or CSV, convert only at the edges.** Don't maintain
  `tiers.csv` and `tiers.json` as parallel truths — pick one as canonical, convert at
  ingestion. Parallel formats drift and double the edit surface.
- **Overwrite by default, cumulative by exception — and label which pattern each file
  uses.** Most outputs should be fully regenerated each run (idempotent, safe to rerun).
  A few must accumulate (timelines, prediction logs, narrative state). State the pattern
  in each file's header so no one clobbers an accumulating file with a regenerating one.
- **Test data lives in a separate, gitignored folder, fed via a `--test` flag.** Never
  commit fixtures into the files production reads — a cron firing early will score fake
  data into the real baseline.

## Automation

- **Defensive guards on every scheduled step.** `--skip-if-empty` so a pre-data cron
  doesn't append duplicate/empty baselines; **date gates** so pre-season runs can't
  overwrite real artifacts (self-removing once the date passes); `continue-on-error` on
  non-critical steps so an enrichment hiccup (quota, flaky API) never blocks the core
  commit. Assume the job will run at 5am when the data isn't there yet — design for it.

## Content & narrative

- **Stateful content needs one voice; stateless content benefits from rotation.** A
  running column/recap that builds on itself reads wrong if the persona changes between
  installments — pin it to one voice. Independent one-off blurbs feel richer with rotation.
- **Narrative-state richness = commentary quality.** Generated commentary is only as good
  as the structured state it reads (streaks, head-to-heads, notable events, themes).
  Invest in the state builder first; the prose layer is cheap once the state is rich.

## Frontend & layout

- **Design the page layout before building features**, lock **nav order before building
  pages**, and build **mobile nav from the start**. Retrofitting structure after features
  exist is far more expensive than deciding it up front.

## Workflow & process

- **Branch for big layout changes.** Large structural moves go on a branch, not straight
  to main.
- **One CC thread per set of overlapping files — and never two threads in one working
  tree.** Don't run parallel threads that edit the same files; serialize overlapping work
  into a single thread. This is not theoretical: during the banner build, a second thread
  shared this checkout and, mid-task, kept writing files, committing, switching branches,
  and finally merging `banners` into `main` — all under the first thread's feet. In a
  *shared* working tree the damage modes are concrete: a `git switch` by one thread flips
  the other's branch (you commit to the wrong one); concurrent commits race; a file being
  written gets clobbered (one `Write` was blocked for exactly this reason). It only ended
  clean because the merge happened to reconcile correctly — luck, not design. If you must
  parallelize, give each thread its **own git worktree** (or its own clone), never the same
  directory.
- **Sync the repo before every build**, and **always name the "do not touch" files in the
  prompt.** Explicit guardrails prevent an agent from helpfully rewriting something stable.

## Definition of Done

"Done" means committed, pushed to `origin/main` (or an open PR), and confirmed on the
remote. Local-only work is not done. Before reporting a task as complete, verify:

1. `git status` — nothing relevant left unstaged
2. `git log origin/main..HEAD` — empty (everything pushed) or PR is open
3. If the change affects the live site, confirm the Pages deploy succeeded

## Testing & launch

- **CI consistency test: assert the calibrator and the live engine produce identical
  output for a fixed fixture set.** This is the single check that catches engine drift the
  moment it happens.
- **Run a full pipeline smoke test before launch.** Bypass the gates, trigger real API
  calls, and confirm every step end-to-end — fetch → score → derive → render — produces
  what the site expects. Don't discover a broken step on the day real data arrives.
