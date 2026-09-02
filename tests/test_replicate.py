#!/usr/bin/env python3
"""Unit tests for Replicate Parameter's unit tidy-up (stdlib only, no
Revit): angles converted to degrees snap onto the standard bend
series - 11.25 / 22.5 / 45 / 90 print exactly, with their natural
decimals - while anything genuinely off the series keeps its value at
2 dp. The script imports pyRevit, so the helpers are extracted by AST.

Run:  python3 tests/test_replicate.py
"""

import ast
import os
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                      "pyMEP.extension", "pyMEP.tab", "05_Parameters.panel",
                      "ReplicateParameter.pushbutton", "script.py")


def _extract(names):
    with open(SCRIPT) as f:
        src = f.read()
    tree = ast.parse(src)
    ns = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in
                ("DISPLAY_DP", "ANGLE_SNAP_STEPS", "ANGLE_SNAP_TOL")
                for t in node.targets):
            exec(compile(ast.get_source_segment(src, node), SCRIPT,
                         "exec"), ns)
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.get_source_segment(src, node), SCRIPT,
                         "exec"), ns)
    return ns


NS = _extract(("_snap_std_angle", "_num_text", "_is_angle_unit"))
snap = NS["_snap_std_angle"]
num_text = NS["_num_text"]
is_angle_unit = NS["_is_angle_unit"]


class StandardAngles(unittest.TestCase):
    def test_the_four_asked_for(self):
        # 11.25 -> 2 dp, 22.5 -> 1 dp, 45 and 90 -> 0 dp
        self.assertEqual(num_text(snap(11.25)), "11.25")
        self.assertEqual(num_text(snap(22.5)), "22.5")
        self.assertEqual(num_text(snap(45.0)), "45")
        self.assertEqual(num_text(snap(90.0)), "90")

    def test_radians_round_trip_noise_snaps(self):
        # what radians -> degrees actually produces in floating point
        import math
        for deg in (11.25, 22.5, 45.0, 90.0, 33.75, 67.5):
            noisy = math.degrees(math.radians(deg))
            self.assertEqual(snap(noisy), deg)
        self.assertEqual(num_text(snap(22.499999999999996)), "22.5")

    def test_whole_five_degree_steps(self):
        self.assertEqual(num_text(snap(30.0000004)), "30")
        self.assertEqual(num_text(snap(15.0)), "15")
        self.assertEqual(num_text(snap(0.0)), "0")

    def test_far_values_stay_as_they_are(self):
        # not near any standard angle - keep the value (2 dp)
        self.assertEqual(snap(12.5), 12.5)
        self.assertEqual(num_text(snap(12.5)), "12.5")
        self.assertEqual(snap(23.4), 23.4)
        self.assertEqual(snap(44.0), 44.0)
        self.assertEqual(num_text(snap(37.123456)), "37.12")
        # 1.25 off 11.25 is a different angle, not noise
        self.assertEqual(snap(10.0), 10.0)     # a 5-step itself
        self.assertEqual(snap(11.0), 11.0)

    def test_tolerance_is_tight(self):
        self.assertEqual(snap(45.009), 45.0)
        self.assertEqual(snap(45.02), 45.02)


class AngleUnitDetection(unittest.TestCase):
    class Forge(object):
        def __init__(self, tid):
            self.TypeId = tid

    def test_degrees_forge_and_legacy(self):
        self.assertTrue(is_angle_unit(
            self.Forge("autodesk.unit.unit:degrees-1.0.1")))
        self.assertTrue(is_angle_unit("DUT_DECIMAL_DEGREES"))

    def test_lengths_are_not(self):
        self.assertFalse(is_angle_unit(
            self.Forge("autodesk.unit.unit:millimeters-1.0.1")))
        self.assertFalse(is_angle_unit("DUT_MILLIMETERS"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
