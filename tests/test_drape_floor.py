#!/usr/bin/env python3
"""Unit tests for Drape Floor's pure sampling helpers (stdlib only,
no Revit). Extracted by AST from the pushbutton script - the module
imports the Revit API and cannot import under CPython.

Covers the complex-floor (footpath) handling: resampling a
tessellated full-circle edge at the typed spacing, the even-odd
point-in-loops test that keeps openings empty, and the inward nudge
that rescues boundary points the slab rejects on curved edges.

Run:  python3 tests/test_drape_floor.py
"""

import ast
import io
import math
import os
import unittest

SRC_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "pyMEP.extension", "pyMEP.tab", "03_Topography.panel",
    "02_DrapeFloor.pushbutton", "script.py")


class _XYZ(object):
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = x, y, z


ns = {"math": math, "XYZ": _XYZ}
_src = io.open(SRC_PATH, encoding="utf-8").read()
for node in ast.parse(_src).body:
    if isinstance(node, ast.FunctionDef) and node.name in (
            "resample_poly", "point_in_polys", "nudge_inward"):
        exec(compile(ast.get_source_segment(_src, node), SRC_PATH,
                     "exec"), ns)
    elif isinstance(node, ast.Assign):
        try:
            exec(compile(ast.get_source_segment(_src, node), SRC_PATH,
                         "exec"), ns)
        except Exception:
            pass

resample_poly = ns["resample_poly"]
point_in_polys = ns["point_in_polys"]
nudge_inward = ns["nudge_inward"]


class ResamplePoly(unittest.TestCase):
    """A tessellated curve resampled at the typed spacing - the edge
    gets a point at every length, e.g. every 5000 mm."""

    def test_every_spacing_along_a_line(self):
        pts = [(float(i), 0.0, 0.0) for i in range(11)]   # 10 long
        out = resample_poly(pts, 3.0)
        self.assertEqual([p[0] for p in out], [0.0, 3.0, 6.0, 9.0, 10.0])

    def test_short_run_keeps_both_ends(self):
        pts = [(float(i), 0.0, 0.0) for i in range(5)]    # 4 long
        out = resample_poly(pts, 100.0)
        self.assertEqual([p[0] for p in out], [0.0, 4.0])

    def test_empty_and_single(self):
        self.assertEqual(resample_poly([], 5.0), [])
        self.assertEqual(resample_poly([(1.0, 2.0, 3.0)], 5.0),
                         [(1.0, 2.0, 3.0)])

    def test_spacing_measured_in_3d(self):
        # rising polyline: 3D length counts, not the plan length
        pts = [(0.0, 0.0, 0.0), (3.0, 0.0, 4.0), (6.0, 0.0, 8.0)]
        out = resample_poly(pts, 5.0)                     # each leg = 5
        self.assertEqual(len(out), 3)


class PointInPolys(unittest.TestCase):
    """Even-odd across loops: openings toggle back OUT."""

    OUTER = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
    HOLE = [(4.0, 4.0), (6.0, 4.0), (6.0, 6.0), (4.0, 6.0)]

    def test_inside_outside_and_hole(self):
        polys = [self.OUTER, self.HOLE]
        self.assertTrue(point_in_polys(2.0, 2.0, polys))
        self.assertFalse(point_in_polys(5.0, 5.0, polys))   # in the hole
        self.assertFalse(point_in_polys(12.0, 5.0, polys))  # outside


class NudgeInward(unittest.TestCase):
    """A boundary point the slab rejects gets retried ~50 mm inside."""

    OUTER = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]

    def test_edge_point_lands_inside(self):
        q = nudge_inward(_XYZ(0.0, 5.0, 7.0), [self.OUTER], step=0.5)
        self.assertIsNotNone(q)
        self.assertTrue(point_in_polys(q.X, q.Y, [self.OUTER]))
        self.assertEqual(q.Z, 7.0)

    def test_far_outside_gives_none(self):
        self.assertIsNone(
            nudge_inward(_XYZ(50.0, 50.0, 0.0), [self.OUTER], step=0.5))


if __name__ == "__main__":
    unittest.main(verbosity=2)
