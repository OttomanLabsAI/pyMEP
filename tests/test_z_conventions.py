#!/usr/bin/env python3
"""Unit tests for the two vertical-placement conventions (stdlib only,
no Revit needed):

  1. pymep_dashboard_pipes.placement_rows lifts pipe endpoints by half
     the diameter - the dashboard export's z is the INVERT (pipe bottom)
     while Revit pipes are CENTERLINE-defined.
  2. pymep_dashboard.anchor_z maps the family's vertical origin to the
     level driven through offset-from-level: base -> sump, top -> rim,
     center -> mid-height.

Both functions are pure Python, so they are extracted from their modules
by AST (the modules themselves import the Revit API and cannot be
imported under CPython).

Run:  python3 tests/test_z_conventions.py
"""

import ast
import os
import unittest

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "pyMEP.extension", "lib")


def extract_function(module_file, func_name):
    """Compile just one module-level function out of a source file."""
    path = os.path.join(LIB, module_file)
    with open(path) as f:
        src = f.read()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            ns = {}
            exec(compile(ast.get_source_segment(src, node),
                         path, "exec"), ns)
            return ns[func_name]
    raise AssertionError("{} not found in {}".format(func_name, path))


placement_rows = extract_function("pymep_dashboard_pipes.py",
                                  "placement_rows")
anchor_z = extract_function("pymep_dashboard.py", "anchor_z")


def reader_row(dia_mm, sz=7.0, ez=6.5):
    return {"name": "Pipe - (1)", "layer": "SW", "is_circular": True,
            "dia_mm": dia_mm, "sx": 100.0, "sy": 200.0, "sz": sz,
            "ex": 110.0, "ey": 210.0, "ez": ez}


class HalfDiameterLift(unittest.TestCase):

    def test_dia_900_lifts_both_ends_450mm(self):
        out = placement_rows([reader_row(900.0)])
        self.assertEqual(len(out), 1)
        self.assertAlmostEqual(out[0]["sz"], 7.45, places=9)
        self.assertAlmostEqual(out[0]["ez"], 6.95, places=9)

    def test_dia_none_and_zero_lift_nothing(self):
        for dia in (None, 0, 0.0):
            out = placement_rows([reader_row(dia)])
            self.assertAlmostEqual(out[0]["sz"], 7.0, places=9,
                                   msg="dia={}".format(dia))
            self.assertAlmostEqual(out[0]["ez"], 6.5, places=9,
                                   msg="dia={}".format(dia))

    def test_plan_coordinates_untouched(self):
        out = placement_rows([reader_row(900.0)])
        self.assertEqual((out[0]["sx"], out[0]["sy"],
                          out[0]["ex"], out[0]["ey"]),
                         (100.0, 200.0, 110.0, 210.0))

    def test_150_dia_lifts_75mm(self):
        out = placement_rows([reader_row(150.0)])
        self.assertAlmostEqual(out[0]["sz"], 7.075, places=9)


class AnchorToZ(unittest.TestCase):
    RIM, SUMP = 12.6, 7.83

    def test_base_origin_sits_at_sump(self):
        self.assertAlmostEqual(
            anchor_z("base", self.RIM, self.SUMP, self.SUMP), 7.83)

    def test_top_origin_sits_at_rim(self):
        self.assertAlmostEqual(
            anchor_z("top", self.RIM, self.SUMP, self.SUMP), 12.6)

    def test_center_origin_sits_at_mid_height(self):
        self.assertAlmostEqual(
            anchor_z("center", self.RIM, self.SUMP, self.SUMP), 10.215)

    def test_missing_sump_falls_back_to_row_z(self):
        self.assertAlmostEqual(anchor_z("base", 12.6, None, 9.9), 9.9)

    def test_missing_rim_falls_back_to_sump(self):
        self.assertAlmostEqual(anchor_z("top", None, 7.83, 7.83), 7.83)


if __name__ == "__main__":
    unittest.main(verbosity=2)
