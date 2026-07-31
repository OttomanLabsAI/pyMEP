#!/usr/bin/env python3
"""Unit tests for the Lines to Pipes network solver (stdlib only, no
Revit).

The pure half of lib/pymep_lines_to_pipes.py: segment intersection,
duplicate-line dropping, the junction graph with overshoot trimming,
Dijkstra depths and the run/tee/elbow plan. Extracted by AST (the
module imports the Revit API and cannot import under CPython).

The geometry vocabulary mirrors the user's real HEL18 drawing: laterals
CROSS their main with a little overshoot, duplicates are drawn on top
of each other, and the odd line touches nothing at all.

Run:  python3 tests/test_lines_to_pipes.py
"""

import ast
import io
import math
import os
import unittest

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "pyMEP.extension", "lib")
SRC_PATH = os.path.join(LIB, "pymep_lines_to_pipes.py")

ns = {"math": math}
_src = io.open(SRC_PATH, encoding="utf-8").read()
for node in ast.parse(_src).body:
    if isinstance(node, ast.FunctionDef):
        exec(compile(ast.get_source_segment(_src, node), SRC_PATH, "exec"),
             ns)
    elif isinstance(node, ast.Assign):
        try:
            exec(compile(ast.get_source_segment(_src, node), SRC_PATH,
                         "exec"), ns)
        except Exception:
            pass

_intersect = ns["_intersect"]
drop_duplicates = ns["drop_duplicates"]
build_network = ns["build_network"]
assign_depths = ns["assign_depths"]
nearest_node = ns["nearest_node"]
solve = ns["solve"]
node_z_m = ns["node_z_m"]


# A small network in mm, drawn the way the HEL18 model draws:
#   trunk    (0,0) -> (30000,0)
#   lateral  (10000,-800) -> (10000,12000)   crosses the trunk, 800 overshoot
#   corner   (30000,0) -> (30000,20000)      end-to-end elbow with the trunk
#   twin     (5000,0) -> (18000,0)           drawn ON the trunk (duplicate)
#   loner    (50000,50000) -> (50000,60000)  touches nothing
TRUNK = ((0.0, 0.0), (30000.0, 0.0))
LATERAL = ((10000.0, -800.0), (10000.0, 12000.0))
CORNER = ((30000.0, 0.0), (30000.0, 20000.0))
TWIN = ((5000.0, 0.0), (18000.0, 0.0))
LONER = ((50000.0, 50000.0), (50000.0, 60000.0))
LINES = [TRUNK, LATERAL, CORNER, TWIN, LONER]


def solved(pick=(0.0, 0.0)):
    return solve(list(LINES), pick, 200.0)


class Intersect(unittest.TestCase):

    def test_proper_crossing(self):
        t, u, p = _intersect((0, 0), (10, 0), (5, -5), (5, 5))
        self.assertAlmostEqual(t, 0.5)
        self.assertAlmostEqual(u, 0.5)
        self.assertEqual(p, (5.0, 0.0))

    def test_parallel_is_none(self):
        self.assertIsNone(_intersect((0, 0), (10, 0), (0, 1), (10, 1)))

    def test_disjoint_is_none(self):
        self.assertIsNone(_intersect((0, 0), (10, 0), (20, -5), (20, 5)))


class Duplicates(unittest.TestCase):

    def test_shorter_copy_is_dropped(self):
        kept, dropped = drop_duplicates([TRUNK, TWIN])
        self.assertEqual(kept, [0])
        self.assertEqual(dropped, [(1, 0)])

    def test_perpendicular_lines_are_not_duplicates(self):
        kept, dropped = drop_duplicates([TRUNK, LATERAL])
        self.assertEqual(kept, [0, 1])
        self.assertEqual(dropped, [])


class Network(unittest.TestCase):

    def test_crossing_becomes_one_shared_node(self):
        net = build_network([TRUNK, LATERAL])
        # trunk: end + crossing + end = 3 nodes -> 2 segs; lateral: the
        # 800 overshoot tail is dropped, one seg above the trunk
        self.assertEqual(len(net["segs"]), 3)
        self.assertEqual(len(net["tail_dropped"]), 1)
        li, length = net["tail_dropped"][0]
        self.assertEqual(li, 1)
        self.assertAlmostEqual(length, 800.0, places=6)

    def test_long_sides_survive(self):
        net = build_network([TRUNK, LATERAL])
        lens = sorted(round(s[3]) for s in net["segs"])
        self.assertEqual(lens, [10000, 12000, 20000])

    def test_isolated_line_keeps_its_geometry(self):
        net = build_network([LONER])
        self.assertEqual(len(net["segs"]), 1)
        self.assertEqual(net["tail_dropped"], [])


class Depths(unittest.TestCase):

    def test_distance_accumulates_through_junctions(self):
        net = build_network([TRUNK, LATERAL])
        out = nearest_node(net, (0.0, 0.0))
        d = assign_depths(net, out)
        far = nearest_node(net, (30000.0, 0.0))
        top = nearest_node(net, (10000.0, 12000.0))
        self.assertAlmostEqual(d[far], 30000.0, places=6)
        self.assertAlmostEqual(d[top], 22000.0, places=6)

    def test_unreachable_nodes_are_absent(self):
        net = build_network([TRUNK, LONER])
        out = nearest_node(net, (0.0, 0.0))
        d = assign_depths(net, out)
        lone = nearest_node(net, (50000.0, 50000.0))
        self.assertNotIn(lone, d)


class Solve(unittest.TestCase):

    def test_the_whole_little_network(self):
        sol = solved()
        run_lines = sorted(r["line"] for r in sol["runs"])
        self.assertEqual(run_lines, [0, 1, 2])
        self.assertEqual(len(sol["tees"]), 1)
        tee = sol["tees"][0]
        self.assertEqual(tee["host_line"], 0)
        self.assertEqual(tee["branch_line"], 1)
        self.assertEqual(len(sol["elbows"]), 1)
        self.assertEqual({sol["elbows"][0]["la"], sol["elbows"][0]["lb"]},
                         {0, 2})

    def test_runs_are_oriented_shallow_to_deep(self):
        sol = solved()
        for r in sol["runs"]:
            self.assertLess(sol["depths"][r["a"]], sol["depths"][r["b"]])

    def test_duplicate_and_loner_reported(self):
        sol = solved()
        text = " / ".join(sol["skipped"])
        self.assertIn("drawn twice", text)
        self.assertIn("not piped", text)
        self.assertIn("overshoot", text)

    def test_outfall_at_the_picked_end(self):
        sol = solved(pick=(29990.0, 10.0))
        self.assertAlmostEqual(sol["depths"][sol["outfall_node"]], 0.0)


class InvertMath(unittest.TestCase):

    def test_invert_rises_with_distance_over_n(self):
        self.assertAlmostEqual(node_z_m(40000.0, 10.0, 200.0), 10.2)

    def test_outfall_sits_at_the_given_invert(self):
        self.assertAlmostEqual(node_z_m(0.0, 3.25, 100.0), 3.25)

    def test_steeper_gradient_rises_faster(self):
        self.assertGreater(node_z_m(10000.0, 0.0, 40.0),
                           node_z_m(10000.0, 0.0, 300.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
