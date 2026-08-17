#!/usr/bin/env python3
"""Unit tests for the pure mesh maths behind Drape Floor's geometry
fallback (stdlib only, no Revit): barycentric Z-at-plan-point and the
topmost / lowest pick across stacked triangles.

Run:  python3 tests/test_mesh.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "pyMEP.extension", "lib"))

import pymep_mesh as M

TRI = ((0.0, 0.0, 0.0), (10.0, 0.0, 10.0), (0.0, 10.0, 20.0))


class TriZAt(unittest.TestCase):
    def test_corners(self):
        self.assertAlmostEqual(M.tri_z_at(TRI, 0.0, 0.0), 0.0)
        self.assertAlmostEqual(M.tri_z_at(TRI, 10.0, 0.0), 10.0)
        self.assertAlmostEqual(M.tri_z_at(TRI, 0.0, 10.0), 20.0)

    def test_interior_interpolates(self):
        # centroid: mean of the corner Zs
        self.assertAlmostEqual(M.tri_z_at(TRI, 10.0 / 3, 10.0 / 3),
                               10.0, places=9)

    def test_edge_point_counts_as_inside(self):
        # floor boundaries often COINCIDE with terrain edges
        self.assertAlmostEqual(M.tri_z_at(TRI, 5.0, 0.0), 5.0)
        self.assertAlmostEqual(M.tri_z_at(TRI, 5.0, 5.0), 15.0)

    def test_outside_is_none(self):
        self.assertIsNone(M.tri_z_at(TRI, 11.0, 0.0))
        self.assertIsNone(M.tri_z_at(TRI, -1.0, -1.0))
        self.assertIsNone(M.tri_z_at(TRI, 6.0, 6.0))

    def test_plan_degenerate_sliver_is_none(self):
        # a vertical face projects to a line in plan
        tri = ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (5.0, 0.0, 8.0))
        self.assertIsNone(M.tri_z_at(tri, 5.0, 0.0))


class SurfaceZ(unittest.TestCase):
    LOW = ((0.0, 0.0, 1.0), (10.0, 0.0, 1.0), (0.0, 10.0, 1.0))
    HIGH = ((0.0, 0.0, 6.0), (10.0, 0.0, 6.0), (0.0, 10.0, 6.0))

    def test_topmost_default_and_lowest(self):
        tris = [self.LOW, self.HIGH]
        self.assertAlmostEqual(M.surface_z(tris, 2.0, 2.0), 6.0)
        self.assertAlmostEqual(M.surface_z(tris, 2.0, 2.0, lowest=True),
                               1.0)

    def test_none_when_nothing_under(self):
        self.assertIsNone(M.surface_z([self.LOW], 50.0, 50.0))
        self.assertIsNone(M.surface_z([], 1.0, 1.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
