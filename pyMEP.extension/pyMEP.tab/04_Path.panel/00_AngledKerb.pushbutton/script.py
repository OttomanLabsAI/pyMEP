# -*- coding: utf-8 -*-
"""Angled Kerb - lay kerb units along a picked line, TILTED onto the
terrain.

One dialog first: the kerb FAMILY (searchable), the UNIT LENGTH, the
ANGLE parameter, an optional LENGTH parameter and the SLOPE FIT tick.
Then pick the LINE and the TERRAIN. The units lay END-TO-END from the
line's start - each sits at its bay's CENTRE, rotated to the line's
plan direction there (curved lines get the curve's tangent), ray-cast
straight down onto the terrain. The terrain's slope ALONG the line at
each unit is written to the angle parameter as -90..+90 degrees
(positive climbs in the line's direction); the length parameter (when
named) receives the unit's length, so the LAST unit comes up short
instead of overhanging.

For units laid LEVEL instead of tilted, use Flat Kerb.

Kerbs are placed, not tracked - re-run after the line or terrain
changes. IronPython 2.7 / Revit 2022-2026.
"""

__title__  = "Angled\nKerb"
__author__ = "Glent Group"

import sys

for _mod in [m for m in list(sys.modules.keys()) if m.startswith("pymep_")]:
    del sys.modules[_mod]

import pymep_kerb_run as KR

KR.run(KR.ANGLED)
