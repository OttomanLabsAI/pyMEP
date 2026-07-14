# -*- coding: utf-8 -*-
"""Place Boxes - place every BOX (rectangular) chamber from an
OttomanLabs utilities-dashboard export.

Flow:
  1. Pick the FAMILY to use (asked first).
  2. Pick the dashboard export (.json) - use the EXPORT button in the
     3D viewer; it exports whatever is currently in view, so isolate a
     layer/group first to place just that subset.
  3. Pick a workset (host level resolves automatically, as the pipe tools).
  4. One TYPE per layer is duplicated from the picked type and named
     exactly after the layer. Every dimension and level is written to
     INSTANCE parameters (Length/Width/Height, rim, sump, depth); the
     structure name goes to Mark, the description to Comments.
"""

__title__  = "Place Boxes\n(Dashboard)"
__author__ = "Glent Group"

import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pymep_dashboard import run_place

run_place("box")
