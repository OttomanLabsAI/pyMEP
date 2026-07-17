# -*- coding: utf-8 -*-
"""Place Structures - place every BOX and CYLINDRICAL chamber from an
OttomanLabs utilities-dashboard export in one run.

Flow:
  1. Pick the dashboard export (.json) - a combined MODEL-*.json or a
     STRUCTS-*.json both work; the EXPORT buttons in the 3D viewer
     export whatever is currently in view, so isolate a layer/group
     first to place just that subset.
  2. Pick the layers to place, then map each layer to a workset - the
     same saved layer->workset map as Place Pipes pre-fills it (one
     confirm when it already covers every layer).
  3. Pick one FAMILY per shape present (boxes, cylinders) and map its
     L/W/H/DIA instance parameters; the family's vertical origin is
     auto-detected (base / top / mid-height) so the chamber lands with
     its sump, rim or centre at the right level.
  4. One TYPE per layer is duplicated from each picked type and named
     exactly after the layer. Every dimension and level is written to
     INSTANCE parameters; the structure name goes to Mark, the
     description to Comments, and each instance lands on its layer's
     workset.
"""

__title__  = "Place\nStructures"
__author__ = "Glent Group"

import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

from pymep_dashboard import run_place

run_place()
