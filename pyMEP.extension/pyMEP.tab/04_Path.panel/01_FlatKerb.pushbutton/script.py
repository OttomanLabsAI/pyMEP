# -*- coding: utf-8 -*-
"""Flat Kerb - lay kerb units along a picked line, laid LEVEL on the
terrain.

Same flow as Angled Kerb - dialog first (kerb FAMILY, UNIT LENGTH and
an optional LENGTH parameter), then pick the LINE and the TERRAIN -
but nothing is tilted: each unit is placed LEVEL at its bay's centre,
rotated only in plan to follow the line, and dropped onto the terrain
below it. On falling ground the units STEP down one at a time instead
of following the slope, which is how a flat-laid kerb actually sits,
so there is no angle parameter and no slope fit here.

It remembers its OWN family and unit length - a flat kerb is a
different product from an angled one.

Kerbs are placed, not tracked - re-run after the line or terrain
changes. IronPython 2.7 / Revit 2022-2026.
"""

__title__  = "Flat\nKerb"
__author__ = "Glent Group"

import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import pymep_kerb_run as KR

KR.run(KR.FLAT)
