#!/usr/bin/env python3
"""Unit tests for the pipe-merge geometry + decisions (stdlib only, no
Revit needed). The pure functions in lib/pymep_merge_pipes.py are
extracted by AST (the module imports the Revit API and cannot import
under CPython) and checked against the contract that matters:

  - collinear pipes (any gap along the line) group into one chain;
    parallel-but-offset or differently-aimed pipes do NOT;
  - a chain's extremes are the EXACT outermost endpoint tuples (the new
    pipe must match those XYZ perfectly);
  - couplings fully inside a run are classed deletable, boundary
    fittings (to the outside) are kept;
  - large gaps along a run are surfaced.

Run:  python3 tests/test_merge_pipes.py
"""

import ast
import math
import os
import unittest

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "pyMEP.extension", "lib")


def load(names):
    path = os.path.join(LIB, "pymep_merge_pipes.py")
    with open(path) as f:
        src = f.read()
    tree = ast.parse(src)
    ns = {"math": math}
    want = set(names)
    # exec the helper + target functions in dependency order
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and (
                node.name.startswith("_") or node.name in want):
            exec(compile(ast.get_source_segment(src, node), path, "exec"),
                 ns)
    return ns


NS = load(["group_collinear", "chain_extremes", "chain_gaps",
           "classify_fittings"])
group_collinear = NS["group_collinear"]
chain_extremes = NS["chain_extremes"]
chain_gaps = NS["chain_gaps"]
classify_fittings = NS["classify_fittings"]


def row(rid, p0, p1, dia=0.5):
    return {"id": rid, "p0": p0, "p1": p1, "dia_ft": dia,
            "len_ft": math.sqrt(sum((a - b) ** 2
                                    for a, b in zip(p0, p1)))}


class Grouping(unittest.TestCase):

    def test_three_collinear_with_gaps_make_one_chain(self):
        rows = [row(1, (0, 0, 0), (10, 0, 0)),
                row(2, (10.1, 0, 0), (20, 0, 0)),   # coupling gap
                row(3, (20.05, 0, 0), (30, 0, 0))]
        chains, singles = group_collinear(rows)
        self.assertEqual(len(chains), 1)
        self.assertEqual(len(chains[0]), 3)
        self.assertEqual(singles, [])

    def test_parallel_but_offset_do_not_merge(self):
        rows = [row(1, (0, 0, 0), (10, 0, 0)),
                row(2, (0, 5, 0), (10, 5, 0))]   # 5 ft to the side
        chains, singles = group_collinear(rows)
        self.assertEqual(chains, [])
        self.assertEqual(len(singles), 2)

    def test_dogleg_turn_still_merges(self):
        # a run that doglegs ~30 deg at the coupling: the far end of pipe
        # 2 swings well off pipe 1's infinite line, but the meeting end
        # aligns - a hand-picked run must still collapse to one pipe
        import math as _m
        a = 30.0 * _m.pi / 180.0
        rows = [row(1, (0, 0, 0), (10, 0, 0)),
                row(2, (10.1, 0, 0),
                     (10.1 + 10 * _m.cos(a), 10 * _m.sin(a), 0))]
        chains, singles = group_collinear(rows)
        self.assertEqual(len(chains), 1)
        self.assertEqual(len(chains[0]), 2)

    def test_right_angle_turn_does_not_merge(self):
        # a ~70 deg turn is a real corner, not a run - stays apart
        import math as _m
        a = 70.0 * _m.pi / 180.0
        rows = [row(1, (0, 0, 0), (10, 0, 0)),
                row(2, (10.1, 0, 0),
                     (10.1 + 10 * _m.cos(a), 10 * _m.sin(a), 0))]
        chains, singles = group_collinear(rows)
        self.assertEqual(chains, [])

    def test_different_directions_do_not_merge(self):
        rows = [row(1, (0, 0, 0), (10, 0, 0)),
                row(2, (10, 0, 0), (10, 10, 0))]   # right angle
        chains, singles = group_collinear(rows)
        self.assertEqual(chains, [])
        self.assertEqual(len(singles), 2)

    def test_two_runs_kept_separate(self):
        rows = [row(1, (0, 0, 0), (10, 0, 0)),
                row(2, (10, 0, 0), (20, 0, 0)),
                row(3, (0, 8, 0), (10, 8, 0)),
                row(4, (10, 8, 0), (20, 8, 0))]
        chains, singles = group_collinear(rows)
        self.assertEqual(len(chains), 2)
        self.assertTrue(all(len(c) == 2 for c in chains))

    def test_collinear_in_3d(self):
        d = (1.0, 1.0, 1.0)
        rows = [row(1, (0, 0, 0), (3, 3, 3)),
                row(2, (5, 5, 5), (9, 9, 9))]
        chains, _ = group_collinear(rows)
        self.assertEqual(len(chains), 1)

    def test_reversed_endpoints_still_collinear(self):
        rows = [row(1, (0, 0, 0), (10, 0, 0)),
                row(2, (20, 0, 0), (10.1, 0, 0))]  # p0/p1 swapped
        chains, _ = group_collinear(rows)
        self.assertEqual(len(chains), 1)


class Extremes(unittest.TestCase):

    def test_exact_endpoint_tuples_returned(self):
        a = (0.0, 0.0, 0.0)
        b = (30.123456, 0.0, 0.0)
        rows = [row(1, a, (10, 0, 0)),
                row(2, (10.1, 0, 0), (20, 0, 0)),
                row(3, (20.05, 0, 0), b)]
        lo, hi = chain_extremes(rows)
        # identity: the returned points ARE the input endpoint objects
        self.assertIs(lo, a)
        self.assertIs(hi, b)

    def test_extremes_independent_of_order(self):
        a = (0.0, 0.0, 0.0)
        b = (30.0, 0.0, 0.0)
        rows = [row(3, (20.05, 0, 0), b),
                row(1, a, (10, 0, 0)),
                row(2, (10.1, 0, 0), (20, 0, 0))]
        lo, hi = chain_extremes(rows)
        self.assertEqual((lo, hi), (a, b))

    def test_diagonal_extremes(self):
        a = (0.0, 0.0, 0.0)
        b = (9.0, 9.0, 9.0)
        rows = [row(1, a, (3, 3, 3)), row(2, (5, 5, 5), b)]
        lo, hi = chain_extremes(rows)
        self.assertEqual((lo, hi), (a, b))


class Gaps(unittest.TestCase):

    def test_coupling_gaps_not_flagged(self):
        rows = [row(1, (0, 0, 0), (10, 0, 0)),
                row(2, (10.1, 0, 0), (20, 0, 0))]   # 0.1 ft ~ 30 mm
        self.assertEqual(chain_gaps(rows), [])

    def test_large_gap_flagged(self):
        rows = [row(1, (0, 0, 0), (10, 0, 0)),
                row(2, (13, 0, 0), (20, 0, 0))]      # 3 ft break
        gaps = chain_gaps(rows)
        self.assertEqual(len(gaps), 1)
        self.assertAlmostEqual(gaps[0][0], 3.0, places=6)


class Fittings(unittest.TestCase):

    def test_internal_coupling_vs_boundary_elbow(self):
        # coupling 100 joins pipes 1&2 (both in chain) -> internal
        # elbow 200 joins pipe 2 (in) and pipe 9 (outside) -> boundary
        links = {100: [1, 2], 200: [2, 9], 300: [1]}
        internal, boundary = classify_fittings(links, [1, 2])
        self.assertEqual(internal, [100])
        self.assertEqual(sorted(boundary), [200, 300])

    def test_fitting_touching_only_outside_ignored(self):
        links = {100: [8, 9]}
        internal, boundary = classify_fittings(links, [1, 2])
        self.assertEqual(internal, [])
        self.assertEqual(boundary, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
