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


def extract_shared(names):
    """One namespace for functions that call each other (branch_points
    -> ray_hits_main), with the module constants they lean on."""
    path = os.path.join(LIB, "pymep_connect_fixtures.py")
    with open(path) as f:
        src = f.read()
    ns = {"math": math, "MIN_LEN_FT": 50.0 / 304.8}
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.get_source_segment(src, node), path, "exec"),
                 ns)
    return ns


_NS = extract_shared(["ray_hits_main", "branch_points"])
ray_hits_main = _NS["ray_hits_main"]
branch_points_aimed = _NS["branch_points"]

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


class RayHitsMain(unittest.TestCase):
    """The node's facing (rotation) aims the branch at the main."""
    A = (0.0, 0.0, 10.0)
    B = (100.0, 0.0, 8.0)

    def test_forward_hit(self):
        # node at (30,40) facing SE at 45 deg: the ray meets y=0 at x=70
        hit = ray_hits_main((30.0, 40.0, 20.0), (1.0, -1.0), self.A,
                            self.B)
        self.assertIsNotNone(hit)
        self.assertAlmostEqual(hit[0], 70.0, places=9)
        self.assertAlmostEqual(hit[1], 0.0, places=9)
        self.assertAlmostEqual(hit[2], 0.7, places=9)

    def test_parallel_and_backwards_miss(self):
        self.assertIsNone(ray_hits_main((30.0, 40.0, 20.0), (1.0, 0.0),
                                        self.A, self.B))
        self.assertIsNone(ray_hits_main((30.0, 40.0, 20.0), (0.0, 1.0),
                                        self.A, self.B))

    def test_beyond_the_run_misses(self):
        # facing hits y=0 way past the far end
        self.assertIsNone(ray_hits_main((30.0, 40.0, 20.0), (3.0, -1.0),
                                        self.A, self.B))

    def test_small_overshoot_clamps_onto_the_run(self):
        # hits at x=101.25 (t=1.0125, inside the 5% grace) -> clamped
        hit = ray_hits_main((98.0, 6.5, 20.0), (0.5, -1.0), self.A,
                            self.B)
        self.assertIsNotNone(hit)
        self.assertAlmostEqual(hit[2], 1.0, places=9)
        self.assertAlmostEqual(hit[0], 100.0, places=9)


class AimedBranch(unittest.TestCase):

    def test_direction_sets_the_end(self):
        out = branch_points_aimed((30.0, 40.0, 20.0), (0.0, 0.0, 10.0),
                                  (100.0, 0.0, 8.0), 100.0, 0.5,
                                  direction=(1.0, -1.0))
        self.assertTrue(out["aimed"])
        self.assertAlmostEqual(out["end"][0], 70.0, places=9)
        self.assertAlmostEqual(out["end"][1], 0.0, places=9)
        # main Z at t=0.7 and the elbow back up the diagonal run
        self.assertAlmostEqual(out["end"][2], 10.0 - 1.4, places=9)
        run = math.hypot(70.0 - 30.0, 0.0 - 40.0)
        self.assertAlmostEqual(out["run_xy_ft"], run, places=9)
        self.assertAlmostEqual(out["bend"][2], 8.6 + run / 100.0,
                               places=9)

    def test_missing_ray_falls_back_to_projection(self):
        out = branch_points_aimed((30.0, 40.0, 20.0), (0.0, 0.0, 10.0),
                                  (100.0, 0.0, 8.0), 100.0, 0.5,
                                  direction=(0.0, 1.0))
        self.assertFalse(out["aimed"])
        self.assertEqual(out["end"][:2], (30.0, 0.0))


class DropLast(unittest.TestCase):
    """Drop Pipe OFF: grade from the outlet, then drop onto the main."""

    def test_grade_first_then_drop(self):
        out = branch_points_aimed((30.0, 40.0, 20.0), (0.0, 0.0, 10.0),
                                  (100.0, 0.0, 8.0), 100.0, 0.5,
                                  drop_pipe=False)
        self.assertEqual(out["mode"], "drop_last")
        # the corner sits ABOVE the main: outlet z minus run/n
        self.assertEqual(out["bend"][:2], (30.0, 0.0))
        self.assertAlmostEqual(out["bend"][2], 20.0 - 40.0 / 100.0,
                               places=9)
        # the end is ON the main centreline; the drop spans the gap
        self.assertAlmostEqual(out["end"][2], 9.4, places=9)
        self.assertAlmostEqual(out["drop_ft"], 19.6 - 9.4, places=9)
        # upstream invert = the OUTLET (the run starts there)
        self.assertAlmostEqual(out["upstream_invert_m"],
                               (20.0 - 0.25) * FT, places=9)

    def test_invert_ignored_in_drop_last(self):
        on = branch_points_aimed((30.0, 40.0, 20.0), (0.0, 0.0, 10.0),
                                 (100.0, 0.0, 8.0), 100.0, 0.5,
                                 invert_m=5.0, drop_pipe=False)
        off = branch_points_aimed((30.0, 40.0, 20.0), (0.0, 0.0, 10.0),
                                  (100.0, 0.0, 8.0), 100.0, 0.5,
                                  drop_pipe=False)
        self.assertEqual(on["bend"], off["bend"])
        self.assertEqual(on["end"], off["end"])


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

    def test_keep_high_pins_upper_end(self):
        # keep the UPPER end (b at z=10): the lower end derives DOWN the
        # slope from it
        a2, b2 = regrade_main_ends((0, 0, 8.0), (100, 0, 10.0), 200.0,
                                   keep="high")
        self.assertEqual(b2, (100, 0, 10.0))
        self.assertEqual(a2[:2], (0, 0))
        self.assertAlmostEqual(a2[2], 10.0 - 0.5, places=9)

    def test_keep_high_other_orientation(self):
        a2, b2 = regrade_main_ends((0, 0, 9.0), (100, 0, 5.0), 100.0,
                                   keep="high")
        self.assertEqual(a2, (0, 0, 9.0))
        self.assertAlmostEqual(b2[2], 8.0, places=9)


node_categories = extract("node_categories")
node_families = extract("node_families")
node_types_in = extract("node_types_in")
search_node_rows = extract("search_node_rows")


def nrow(cat, fam, typ):
    return {"cat": cat, "fam": fam, "type": typ,
            "label": "{} : {} : {}".format(cat, fam, typ), "insts": []}


NODE_ROWS = [
    nrow("Plumbing Fixtures", "760.403.110 INDUSTRIAL DRAIN FG", "110"),
    nrow("Plumbing Fixtures", "760.403.110 INDUSTRIAL DRAIN FG", "160"),
    nrow("Plumbing Fixtures", "Gully Trapped", "Standard"),
    nrow("Generic Models", "Cylinder Chamber", "1200 dia"),
]


class NodePicker(unittest.TestCase):
    """category > family > type cascade, plus the search box."""

    def test_categories_sorted_and_unique(self):
        self.assertEqual(node_categories(NODE_ROWS),
                         ["Generic Models", "Plumbing Fixtures"])

    def test_families_scoped_to_category(self):
        self.assertEqual(node_families(NODE_ROWS, "Plumbing Fixtures"),
                         ["760.403.110 INDUSTRIAL DRAIN FG",
                          "Gully Trapped"])
        self.assertEqual(node_families(NODE_ROWS, "Generic Models"),
                         ["Cylinder Chamber"])

    def test_types_scoped_to_family(self):
        got = node_types_in(NODE_ROWS, "Plumbing Fixtures",
                            "760.403.110 INDUSTRIAL DRAIN FG")
        self.assertEqual([r["type"] for r in got], ["110", "160"])

    def test_search_matches_any_level_all_words(self):
        self.assertEqual(
            [r["type"] for r in search_node_rows(NODE_ROWS, "industrial")],
            ["110", "160"])
        # words may span category / family / type
        self.assertEqual(
            [r["type"] for r in search_node_rows(NODE_ROWS,
                                                 "generic 1200")],
            ["1200 dia"])
        self.assertEqual(search_node_rows(NODE_ROWS, "drain 160")[0]["type"],
                         "160")

    def test_search_is_case_insensitive_and_empty_falls_back(self):
        self.assertEqual(len(search_node_rows(NODE_ROWS, "GULLY")), 1)
        self.assertEqual(search_node_rows(NODE_ROWS, ""), [])
        self.assertEqual(search_node_rows(NODE_ROWS, "   "), [])

    def test_search_miss_is_empty(self):
        self.assertEqual(search_node_rows(NODE_ROWS, "ductwork"), [])


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
