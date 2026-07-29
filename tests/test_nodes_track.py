#!/usr/bin/env python3
"""Unit tests for the node-branch tracker's pure parts (stdlib only, no
Revit needed) - the registry IO and the two geometric decisions the
UPDATE pass rests on: did the outlet move, and is a pipe a piece of the
stored main line. Extracted by AST (the module imports the Revit API).

Run:  python3 tests/test_nodes_track.py
"""

import ast
import math
import os
import shutil
import tempfile
import unittest

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "pyMEP.extension", "lib")


def load(names):
    path = os.path.join(LIB, "pymep_nodes_track.py")
    with open(path) as f:
        src = f.read()
    ns = {"math": math, "os": os, "REGISTRY": "node_branches.json"}
    import json as _json
    ns["json"] = _json
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.get_source_segment(src, node), path, "exec"),
                 ns)
    return ns


NS = load(["load_branches", "save_branches", "add_branch",
           "outlet_moved", "on_main_line"])
load_branches = NS["load_branches"]
save_branches = NS["save_branches"]
add_branch = NS["add_branch"]
outlet_moved = NS["outlet_moved"]
on_main_line = NS["on_main_line"]


class Registry(unittest.TestCase):

    def setUp(self):
        self.base = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_empty_and_roundtrip(self):
        self.assertEqual(load_branches(self.base), {"branches": []})
        add_branch(self.base, {"node_uid": "u1", "slope": 100})
        self.assertEqual(len(load_branches(self.base)["branches"]), 1)

    def test_one_record_per_node(self):
        add_branch(self.base, {"node_uid": "u1", "slope": 100})
        add_branch(self.base, {"node_uid": "u1", "slope": 150})
        recs = load_branches(self.base)["branches"]
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["slope"], 150)

    def test_corrupt_registry_recovers(self):
        with open(os.path.join(self.base, "node_branches.json"), "w") as f:
            f.write("{broken")
        self.assertEqual(load_branches(self.base), {"branches": []})


class OutletMoved(unittest.TestCase):

    def test_within_tolerance_is_unmoved(self):
        self.assertFalse(outlet_moved((0, 0, 0), (0.01, 0, 0), 0.0164))

    def test_beyond_tolerance_is_moved(self):
        self.assertTrue(outlet_moved((0, 0, 0), (1.0, 0, 0), 0.0164))
        self.assertTrue(outlet_moved((0, 0, 0), (0, 0, 0.5), 0.0164))


class OnMainLine(unittest.TestCase):
    A = (0.0, 0.0, 10.0)
    B = (100.0, 0.0, 8.0)

    def test_piece_of_the_line(self):
        # a split piece of the main: endpoints on the (sloped) line
        self.assertTrue(on_main_line((30.0, 0.0, 9.4), (60.0, 0.0, 8.8),
                                     self.A, self.B, 0.25))

    def test_branch_pipe_is_not(self):
        # a sloped branch leaves the line immediately
        self.assertFalse(on_main_line((30.0, 0.0, 9.4), (30.0, 40.0, 9.8),
                                      self.A, self.B, 0.25))

    def test_parallel_offset_is_not(self):
        self.assertFalse(on_main_line((0.0, 5.0, 10.0), (100.0, 5.0, 8.0),
                                      self.A, self.B, 0.25))


if __name__ == "__main__":
    unittest.main(verbosity=2)
