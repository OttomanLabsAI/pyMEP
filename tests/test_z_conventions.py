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


def extract_function_with(module_file, func_name, extra_ns):
    """Like extract_function, but with stubs available as globals."""
    path = os.path.join(LIB, module_file)
    with open(path) as f:
        src = f.read()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            ns = dict(extra_ns)
            exec(compile(ast.get_source_segment(src, node),
                         path, "exec"), ns)
            return ns[func_name]
    raise AssertionError("{} not found in {}".format(func_name, path))


placement_rows = extract_function("pymep_dashboard_pipes.py",
                                  "placement_rows")
anchor_z = extract_function("pymep_dashboard.py", "anchor_z")
row_height_m = extract_function("pymep_dashboard.py", "row_height_m")
qa_constant_delta = extract_function("pymep_dashboard.py",
                                     "qa_constant_delta")
datum_off_z_m = extract_function("pymep_landxml_place2.py",
                                 "datum_off_z_m")


class _XYZ(object):
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = x, y, z


import math as _math                                          # noqa: E402
make_survey_fn = extract_function_with(
    "pymep_landxml_place2.py", "make_survey_fn",
    {"math": _math, "XYZ": _XYZ})


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


class RowHeight(unittest.TestCase):
    """H = rim - sump pins the height; depth_m is only the fallback."""

    def test_rim_minus_sump_beats_depth(self):
        r = {"rim_m": 12.6, "sump_m": 7.83, "depth_m": 99.0}
        self.assertAlmostEqual(row_height_m(r), 4.77)

    def test_depth_fallback_when_an_end_is_missing(self):
        self.assertAlmostEqual(
            row_height_m({"rim_m": 12.6, "sump_m": None,
                          "depth_m": 3.2}), 3.2)
        self.assertAlmostEqual(
            row_height_m({"rim_m": None, "sump_m": 7.83,
                          "depth_m": 3.2}), 3.2)

    def test_nothing_derivable_is_none(self):
        self.assertIsNone(row_height_m({"rim_m": None, "sump_m": None,
                                        "depth_m": None}))
        self.assertIsNone(row_height_m({}))

    def test_inverted_ends_fall_back_to_depth(self):
        # rim BELOW sump is bad data, not a negative chamber
        r = {"rim_m": 7.0, "sump_m": 9.0, "depth_m": 2.5}
        self.assertAlmostEqual(row_height_m(r), 2.5)

    def test_zero_depth_is_none(self):
        self.assertIsNone(row_height_m({"depth_m": 0.0}))


class AnchorWithHeight(unittest.TestCase):
    """The SAME computed H sharpens anchor_z's missing-end fallbacks,
    so the derived end always lands where the height write puts it."""

    def test_top_anchor_without_rim_sits_at_sump_plus_h(self):
        self.assertAlmostEqual(
            anchor_z("top", None, 7.83, 7.83, height_m=3.0), 10.83)

    def test_base_anchor_without_sump_sits_at_rim_minus_h(self):
        self.assertAlmostEqual(
            anchor_z("base", 12.6, None, 12.6, height_m=3.0), 9.6)

    def test_center_anchor_with_one_end_and_h(self):
        self.assertAlmostEqual(
            anchor_z("center", None, 7.83, 7.83, height_m=3.0), 9.33)

    def test_both_ends_ignore_the_height(self):
        # rim and sump pin everything; H changes nothing
        self.assertAlmostEqual(
            anchor_z("center", 12.6, 7.83, 7.83, height_m=99.0), 10.215)

    def test_legacy_fallbacks_without_height(self):
        self.assertAlmostEqual(anchor_z("top", None, 7.83, 7.83), 7.83)
        self.assertAlmostEqual(anchor_z("base", 12.6, None, 9.9), 9.9)


class ConstantDelta(unittest.TestCase):
    """The 'family geometry does not span origin-to-H' detector."""

    def test_same_offset_everywhere_is_constant(self):
        self.assertTrue(qa_constant_delta(
            [(250.0, 250.0), (251.0, 249.0), (250.5, 250.2)]))

    def test_varying_offsets_are_not(self):
        self.assertFalse(qa_constant_delta(
            [(250.0, 250.0), (12.0, -40.0)]))

    def test_one_instance_proves_nothing(self):
        self.assertFalse(qa_constant_delta([(250.0, 250.0)]))


class DatumLevel(unittest.TestCase):
    """Place Pipes / Place Structures vertical datum: the picked
    level's zero is the site datum, so z_internal = level + z_site
    and displayed elevations read the export's site values."""

    def test_sign_and_units(self):
        # a datum level at internal -45.667 m
        lvl_ft = -45.667 / 0.3048
        self.assertAlmostEqual(datum_off_z_m(lvl_ft), 45.667, places=9)

    def test_site_level_lands_above_the_level(self):
        lvl_ft = -45.667 / 0.3048
        fn = make_survey_fn(0.0, 0.0, 0.0, datum_off_z_m(lvl_ft))
        p = fn(0.0, 0.0, 47.85)
        self.assertAlmostEqual(p.Z, lvl_ft + 47.85 / 0.3048, places=6)
        # displayed above the datum level: exactly the site value
        self.assertAlmostEqual((p.Z - lvl_ft) * 0.3048, 47.85, places=9)

    def test_level_at_internal_zero_keeps_absolute_z(self):
        fn = make_survey_fn(0.0, 0.0, 0.0, datum_off_z_m(0.0))
        self.assertAlmostEqual(fn(0.0, 0.0, 10.0).Z, 10.0 / 0.3048,
                               places=9)


class DatumLevelSingleCount(unittest.TestCase):
    """The 'twice as high' bug: a model whose SHARED coordinates already
    lift internal Z=0 to the site elevation must not have the site height
    added AGAIN by the datum-level convention. The placers fold the
    model's shared elevation (elev0) into z0, so displayed (shared)
    elevations read the export's site values ONCE."""

    ELEV0_M = 47.85       # shared elevation of the internal origin
    SITE_M = 47.85        # a pipe invert at the same site level

    def test_old_convention_doubled_the_display(self):
        # datum level at internal 0 -> z0 = 0: invert lands 47.85 m over
        # internal zero, but internal zero already DISPLAYS 47.85 shared
        fn = make_survey_fn(0.0, 0.0, 0.0, datum_off_z_m(0.0))
        p = fn(0.0, 0.0, self.SITE_M)
        shown_m = p.Z * 0.3048 + self.ELEV0_M
        self.assertAlmostEqual(shown_m, 2 * self.SITE_M, places=9)

    def test_folding_elev0_single_counts(self):
        # the fix: z0 = datum_off + elev0 -> displayed shared elevation
        # reads the site value exactly once
        z0 = datum_off_z_m(0.0) + self.ELEV0_M
        fn = make_survey_fn(0.0, 0.0, 0.0, z0)
        p = fn(0.0, 0.0, self.SITE_M)
        shown_m = p.Z * 0.3048 + self.ELEV0_M
        self.assertAlmostEqual(shown_m, self.SITE_M, places=9)

    def test_unreferenced_model_is_unchanged(self):
        # elev0 = 0 (no vertical georeference): the fold is a no-op and
        # the original datum-level convention stands
        z0 = datum_off_z_m(-45.667 / 0.3048) + 0.0
        fn = make_survey_fn(0.0, 0.0, 0.0, z0)
        p = fn(0.0, 0.0, 47.85)
        self.assertAlmostEqual((p.Z + 45.667 / 0.3048) * 0.3048, 47.85,
                               places=9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
