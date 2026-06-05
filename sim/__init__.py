"""Vendored Monte Carlo simulation engine for the WC Challenge daily win-probability.

This package is a self-contained copy of the reusable pieces of the preseason
scoring-balance engine (originally the standalone "World Cup Pool" project):

  * match_model.py  - Elo-style group/knockout outcome model (unchanged)
  * bracket.py      - official 2026 R32->Final bracket tree (unchanged)
  * third_place.py  - Annex C third-place allocation lookup (path adapted to data/)
  * engine.py       - NEW forward-simulation layer that locks actual results and
                      simulates only the remaining matches, scoring per rebalanced_v3.

It is vendored (rather than imported from the sibling project) so the GitHub
Action can run win_probability.py with no external dependencies beyond numpy.
The match model and bracket are byte-for-byte the preseason logic, so a zero-
results run reproduces the 20k preseason baseline within Monte Carlo noise.
"""
