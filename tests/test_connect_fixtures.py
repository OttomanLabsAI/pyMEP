#!/usr/bin/env python3
"""Unit tests for the fixture-to-main branch geometry (stdlib only, no
Revit needed). ``branch_points`` in lib/pymep_connect_fixtures.py is
extracted by AST and checked against the contract:

  - AUTO (invert None): the branch end sits ON the main's centreline at
    the plan-nearest point (Z interpolated along the main's own slope -
    'where it currently is'), and the elbow derives back UP the 1:n
    slope at the fixture's XY;
  - FIXED invert: the elbow centreline sits at invert + D/2 and the far
    end derives DOWN the slope;
  - the plan projection clamps to the main's segment;
  - the reported upstream invert is centreline minus D/2, in metres.

Run:  python3 tests/test_connect_fixtures.py
"""

import ast
import math
import os
import unittest

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "pyMEP.extension", "lib")


def extract(name):
    path = os.path.join(LIB, "pymep_connect_fixtures.py")
    with open(path) as f:
        src = f.read()
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            ns = {"math": math}
            exec(compile(ast.get_source_segment(src, node), path, "exec"), ns)
            return ns[name]
    raise AssertionError(name + " not found")


branch_points = extract("branch_points")
main_gradient = extract("main_gradient")
regrade_main_ends = extract("regrade_main_ends")
plan_dist_to_segment = extract("plan_dist_to_segment")

FT = 304.8 / 1000.0   # m per ft


class AutoInvert(unittest.TestCase):
    """invert_m=None - keep it where it currently is."""

    def test_end_on_sloped_main_and_bend_up_the_slope(self):
        # main falls from z=10 at a=(0,0) to z=8 at b=(100,0); fixture at
        # (30, 40): plan-nearest point is (30, 0), main Z there = 9.4
        out = branch_points((30.0, 40.0, 20.0), (0.0, 0.0, 10.0),
                            (100.0, 0.0, 8.0), 100.0, 0.5)
        self.assertEqual(out["end"][:2], (30.0, 0.0))
        self.assertAlmostEqual(out["end"][2], 9.4, places=9)
        self.assertAlmostEqual(out["run_xy_ft"], 40.0, places=9)
        # elbow at the fixture XY, main Z + run/slope
        self.assertEqual(out["bend"][:2], (30.0, 40.0))
        self.assertAlmostEqual(out["bend"][2], 9.4 + 40.0 / 100.0, places=9)
        self.assertAlmostEqual(out["drop_ft"], 20.0 - 9.8, places=9)

    def test_reported_invert_is_centreline_minus_half_dia_in_m(self):
        out = branch_points((30.0, 40.0, 20.0), (0.0, 0.0, 10.0),
                            (100.0, 0.0, 8.0), 100.0, 0.5)
        self.assertAlmostEqual(out["upstream_invert_m"],
                               (9.8 - 0.25) * FT, places=9)

    def test_projection_clamps_to_segment(self):
        # fixture beyond the main's b end: projection clamps to b
        out = branch_points((150.0, 10.0, 20.0), (0.0, 0.0, 10.0),
                            (100.0, 0.0, 8.0), 100.0, 0.5)
        self.assertEqual(out["end"][:2], (100.0, 0.0))
        self.assertAlmostEqual(out["end"][2], 8.0, places=9)

    def test_fixture_directly_over_the_main(self):
        out = branch_points((50.0, 0.0, 20.0), (0.0, 0.0, 10.0),
                            (100.0, 0.0, 10.0), 100.0, 0.5)
        self.assertAlmostEqual(out["run_xy_ft"], 0.0, places=9)
        self.assertAlmostEqual(out["bend"][2], out["end"][2], places=9)

    def test_no_slope_means_level_branch(self):
        for n in (0, None):
            out = branch_points((30.0, 40.0, 20.0), (0.0, 0.0, 10.0),
                                (100.0, 0.0, 10.0), n, 0.5)
            self.assertAlmostEqual(out["bend"][2], out["end"][2], places=9)


class FixedInvert(unittest.TestCase):
    """invert_m set - the elbow level is pinned, the end derives."""

    def test_bend_at_invert_plus_half_dia_end_down_the_slope(self):
        inv_m = 2.0
        out = branch_points((30.0, 40.0, 20.0), (0.0, 0.0, 10.0),
                            (100.0, 0.0, 8.0), 100.0, 0.5, invert_m=inv_m)
        bend_z = inv_m * 1000.0 / 304.8 + 0.25
        self.assertAlmostEqual(out["bend"][2], bend_z, places=9)
        self.assertAlmostEqual(out["end"][2], bend_z - 40.0 / 100.0,
                               places=9)
        # reported invert round-trips exactly
        self.assertAlmostEqual(out["upstream_invert_m"], inv_m, places=9)

    def test_end_xy_still_on_the_main(self):
        out = branch_points((30.0, 40.0, 20.0), (0.0, 0.0, 10.0),
                            (100.0, 0.0, 8.0), 100.0, 0.5, invert_m=2.0)
        self.assertEqual(out["end"][:2], (30.0, 0.0))


class MainGradient(unittest.TestCase):

    def test_reads_current_fall(self):
        # 100 plan run, 2 fall -> 1:50
        self.assertAlmostEqual(
            main_gradient((0, 0, 10.0), (100, 0, 8.0)), 50.0, places=9)

    def test_level_main_is_none(self):
        self.assertIsNone(main_gradient((0, 0, 10.0), (100, 0, 10.0)))


class RegradeMain(unittest.TestCase):

    def test_low_end_stays_high_end_derives(self):
        # b is the low end: it must not move; a rises to low + run/n
        a2, b2 = regrade_main_ends((0, 0, 10.0), (100, 0, 8.0), 200.0)
        self.assertEqual(b2, (100, 0, 8.0))
        self.assertEqual(a2[:2], (0, 0))
        self.assertAlmostEqual(a2[2], 8.0 + 100.0 / 200.0, places=9)

    def test_level_main_second_end_treated_as_low(self):
        a2, b2 = regrade_main_ends((0, 0, 10.0), (100, 0, 10.0), 100.0)
        self.assertEqual(b2, (100, 0, 10.0))
        self.assertAlmostEqual(a2[2], 11.0, places=9)

    def test_other_end_low(self):
        a2, b2 = regrade_main_ends((0, 0, 5.0), (100, 0, 9.0), 100.0)
        self.assertEqual(a2, (0, 0, 5.0))
        self.assertAlmostEqual(b2[2], 6.0, places=9)


class NearestSegment(unittest.TestCase):

    def test_plan_distance_clamps_and_measures(self):
        self.assertAlmostEqual(
            plan_dist_to_segment((50, 7, 99), (0, 0, 0), (100, 0, 5)),
            7.0, places=9)
        # beyond the end: distance to the endpoint
        self.assertAlmostEqual(
            plan_dist_to_segment((103, 4, 0), (0, 0, 0), (100, 0, 0)),
            5.0, places=9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
