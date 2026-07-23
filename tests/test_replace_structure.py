#!/usr/bin/env python3
"""Unit test for the cylinder->pipe geometry (stdlib only, no Revit).

``vertical_endpoints`` in lib/pymep_replace_structure.py is the one
pure decision - the pipe stands straight up from the cylinder base by
its H, keeping the EXACT input coordinates (the replacement must match
the structure's position). Extracted by AST (the module imports the
Revit API and cannot import under CPython).

Run:  python3 tests/test_replace_structure.py
"""

import ast
import os
import unittest

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "pyMEP.extension", "lib")


def extract(name):
    path = os.path.join(LIB, "pymep_replace_structure.py")
    with open(path) as f:
        src = f.read()
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            ns = {}
            exec(compile(ast.get_source_segment(src, node), path, "exec"), ns)
            return ns[name]
    raise AssertionError(name + " not found")


vertical_endpoints = extract("vertical_endpoints")


class VerticalEndpoints(unittest.TestCase):

    def test_spans_h_from_base(self):
        p0, p1 = vertical_endpoints(3.5, -2.0, 10.0, 6.4567)
        self.assertEqual(p0, (3.5, -2.0, 10.0))
        self.assertEqual((p1[0], p1[1]), (3.5, -2.0))
        self.assertAlmostEqual(p1[2], 16.4567, places=9)

    def test_xy_preserved_exactly(self):
        x, y = 123.456789, -987.654321
        p0, p1 = vertical_endpoints(x, y, 0.0, 5.0)
        self.assertEqual((p0[0], p0[1]), (x, y))
        self.assertEqual((p1[0], p1[1]), (x, y))

    def test_length_is_h(self):
        p0, p1 = vertical_endpoints(0, 0, 4.0, 6.0)
        self.assertAlmostEqual(p1[2] - p0[2], 6.0, places=9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
