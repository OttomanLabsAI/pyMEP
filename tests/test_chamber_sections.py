#!/usr/bin/env python3
"""Unit tests for the pure helpers behind the Create Chamber Sections
dialog (stdlib only, no Revit): remembered settings, the mm field
parser and the family-type search filter.

Run:  python3 tests/test_chamber_sections.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "pyMEP.extension", "lib"))

import pymep_chamber_sections as CS


class SectionSettings(unittest.TestCase):
    def test_defaults_when_empty(self):
        s = CS.section_settings({})
        self.assertEqual(s["offset"], 1500.0)
        self.assertEqual(s["height"], 3000.0)
        self.assertEqual(s["depth"], 3000.0)
        self.assertEqual(s["type"], "")
        self.assertEqual(s["side_types"], {})
        self.assertTrue(s["same"])
        self.assertTrue(s["cut_only"])

    def test_none_settings(self):
        self.assertEqual(CS.section_settings(None)["offset"], 1500.0)

    def test_remembered_values(self):
        s = CS.section_settings({
            CS.SETTINGS_SECTION_OFFSET: 1200,
            CS.SETTINGS_SECTION_HEIGHT: "2500",
            CS.SETTINGS_SECTION_DEPTH: 4000.5,
            CS.SETTINGS_SECTION_TYPE: "Building Section",
            CS.SETTINGS_SECTION_SIDE_TYPES: {"a": "Side A type",
                                             "B": "Side B type"},
            CS.SETTINGS_SECTION_SAME_TYPE: False,
            CS.SETTINGS_SECTION_CUT_ONLY: False,
        })
        self.assertEqual(s["offset"], 1200.0)
        self.assertEqual(s["height"], 2500.0)
        self.assertEqual(s["depth"], 4000.5)
        self.assertEqual(s["type"], "Building Section")
        self.assertEqual(s["side_types"], {"A": "Side A type",
                                           "B": "Side B type"})
        self.assertFalse(s["same"])
        self.assertFalse(s["cut_only"])

    def test_broken_values_fall_back(self):
        s = CS.section_settings({
            CS.SETTINGS_SECTION_OFFSET: None,
            CS.SETTINGS_SECTION_HEIGHT: "tall",
            CS.SETTINGS_SECTION_DEPTH: -5,
            CS.SETTINGS_SECTION_SIDE_TYPES: ["not", "a", "dict"],
            CS.SETTINGS_SECTION_TYPE: None,
        })
        self.assertEqual(s["offset"], 1500.0)
        self.assertEqual(s["height"], 3000.0)
        self.assertEqual(s["depth"], 3000.0)
        self.assertEqual(s["side_types"], {})
        self.assertEqual(s["type"], "")


class ChamberKey(unittest.TestCase):
    def test_key_is_the_whole_mark(self):
        self.assertEqual(CS.chamber_key("LV1/Z1"), "LV1/Z1")
        self.assertEqual(CS.chamber_key(" LV12/Z3 "), "LV12/Z3")
        self.assertEqual(CS.chamber_key("MH-7"), "MH-7")
        self.assertEqual(CS.chamber_key(""), "")
        self.assertEqual(CS.chamber_key(None), "")


class BoxBottom(unittest.TestCase):
    # chamber from -3 to -1, plan cut plane at +1.2, margins 0.5 / 0.3
    def test_tall_seed_wraps_the_chamber(self):
        bottom, reaches = CS.box_bottom(-3.0, 6.0, 1.2, 0.5, 0.3)
        self.assertAlmostEqual(bottom, -3.5)
        self.assertTrue(reaches)
        self.assertGreaterEqual(bottom + 6.0, 1.2 + 0.3)

    def test_short_seed_sits_as_low_as_the_plane_allows(self):
        bottom, reaches = CS.box_bottom(-3.0, 3.0, 1.2, 0.5, 0.3)
        self.assertAlmostEqual(bottom, -1.5)
        self.assertFalse(reaches)
        self.assertAlmostEqual(bottom + 3.0, 1.5)

    def test_exact_fit(self):
        bottom, reaches = CS.box_bottom(-3.0, 5.0, 1.2, 0.5, 0.3)
        self.assertAlmostEqual(bottom, -3.5)
        self.assertTrue(reaches)

    def test_chamber_above_the_plane_keeps_the_plane_inside(self):
        bottom, reaches = CS.box_bottom(5.0, 4.0, 1.2, 0.5, 0.3)
        self.assertAlmostEqual(bottom, 0.9)
        self.assertTrue(reaches)


class UprightRotation(unittest.TestCase):
    def deg(self, chamber_deg, ref_deg=0.0):
        import math
        return math.degrees(CS.upright_rotation(math.radians(chamber_deg),
                                                math.radians(ref_deg)))

    def test_project_north(self):
        self.assertAlmostEqual(self.deg(0), 0.0)
        self.assertAlmostEqual(self.deg(10), 10.0)
        self.assertAlmostEqual(self.deg(-30), -30.0)
        # a chamber turned 80 deg: its +X face is nearly north - use it
        self.assertAlmostEqual(self.deg(80), -10.0)
        self.assertAlmostEqual(self.deg(90), 0.0)
        self.assertAlmostEqual(self.deg(170), -10.0)
        self.assertAlmostEqual(self.deg(-100), -10.0)
        self.assertAlmostEqual(self.deg(360 + 40), 40.0)

    def test_true_north_reference_picks_a_different_face(self):
        # project north: the 130 deg face is nearest up -> box at +40
        self.assertAlmostEqual(self.deg(40, 0.0), 40.0)
        # true north 15.25 deg clockwise (344.75): the 40 deg face is
        # nearer that 'up' -> box at -50
        self.assertAlmostEqual(self.deg(40, -15.25), -50.0)
        self.assertAlmostEqual(self.deg(80, -15.25), -10.0)

    def test_result_always_aligned_to_the_chamber(self):
        import math
        for c in range(-180, 181, 7):
            for ref in (0.0, -15.25, 30.0):
                phi = self.deg(c, ref)
                self.assertAlmostEqual(
                    math.fmod((phi - c) + 3600.0, 90.0), 0.0, places=6)
                self.assertLessEqual(abs(phi - ref), 45.0 + 1e-9)

    def test_wrap_angle(self):
        import math
        self.assertAlmostEqual(CS.wrap_angle(math.radians(100), math.pi / 2),
                               math.radians(10))
        self.assertAlmostEqual(CS.wrap_angle(math.radians(-50), math.pi / 2),
                               math.radians(40))
        self.assertAlmostEqual(CS.wrap_angle(math.radians(45), math.pi / 2),
                               math.radians(45))
        self.assertAlmostEqual(CS.wrap_angle(math.radians(-45), math.pi / 2),
                               math.radians(45))


class Sizing(unittest.TestCase):
    def test_size_settings_defaults(self):
        s = CS.size_settings({})
        self.assertEqual(s["mode"], CS.SIZE_FIXED)
        self.assertEqual((s["px"], s["py"], s["ph"]),
                         ("Width", "Length", "Height"))
        self.assertEqual(s["clear"], 500.0)
        self.assertEqual(CS.size_settings(None)["mode"], CS.SIZE_FIXED)

    def test_size_settings_remembered(self):
        s = CS.size_settings({CS.SETTINGS_SIZE_MODE: CS.SIZE_PARAMS,
                              CS.SETTINGS_SIZE_PARAM_X: "W",
                              CS.SETTINGS_SIZE_PARAM_Y: "L",
                              CS.SETTINGS_SIZE_PARAM_H: "H",
                              CS.SETTINGS_SIZE_CLEAR: 250})
        self.assertEqual(s["mode"], CS.SIZE_PARAMS)
        self.assertEqual((s["px"], s["py"], s["ph"]), ("W", "L", "H"))
        self.assertEqual(s["clear"], 250.0)
        self.assertEqual(CS.size_settings(
            {CS.SETTINGS_SIZE_CLEAR: 0})["clear"], 0.0)
        self.assertEqual(CS.size_settings(
            {CS.SETTINGS_SIZE_MODE: "odd"})["mode"], CS.SIZE_FIXED)

    def test_section_box_from_dims(self):
        # chamber 2000 along X, 1200 along Y, 1800 high, 500 clear
        plane, hw, hh, depth = CS.section_box_from_dims(0, 2000, 1200,
                                                        1800, 500)
        self.assertEqual((plane, hw, hh, depth), (1500, 1100, 1400, 3000))
        plane, hw, hh, depth = CS.section_box_from_dims(1, 2000, 1200,
                                                        1800, 500)
        self.assertEqual((plane, hw, hh, depth), (1100, 1500, 1400, 2200))
        self.assertEqual(CS.section_box_from_dims(2, 2000, 1200, 1800, 500),
                         CS.section_box_from_dims(0, 2000, 1200, 1800, 500))
        self.assertEqual(CS.section_box_from_dims(3, 2000, 1200, 1800, 500),
                         CS.section_box_from_dims(1, 2000, 1200, 1800, 500))

    def test_plan_crop_from_dims(self):
        self.assertEqual(CS.plan_crop_from_dims(2000, 1200, 500),
                         (1500, 1100))
        self.assertEqual(CS.plan_crop_from_dims(2000, 1200, 0), (1000, 600))


class PlansSettings(unittest.TestCase):
    def test_defaults_and_remembered(self):
        s = CS.plans_settings({})
        self.assertEqual(s["template"], CS.PLANS_TEMPLATE_ACTIVE)
        self.assertEqual(s["seed"], "")
        self.assertEqual(s["extents"], CS.EXTENTS_CROP)
        self.assertEqual((s["width"], s["depth"]), (3000.0, 3000.0))
        s = CS.plans_settings({CS.SETTINGS_PLANS_EXTENTS: CS.EXTENTS_SCOPE,
                               CS.SETTINGS_PLANS_WIDTH: 2500,
                               CS.SETTINGS_PLANS_DEPTH: "bad"})
        self.assertEqual(s["extents"], CS.EXTENTS_SCOPE)
        self.assertEqual((s["width"], s["depth"]), (2500.0, 3000.0))
        s = CS.plans_settings({CS.SETTINGS_PLANS_TEMPLATE: "CHAMBER PLAN",
                               CS.SETTINGS_PLANS_SEED: "sample_scope_box"})
        self.assertEqual(s["template"], "CHAMBER PLAN")
        self.assertEqual(s["seed"], "sample_scope_box")
        self.assertEqual(CS.plans_settings(None)["template"],
                         CS.PLANS_TEMPLATE_ACTIVE)

    def test_pick_seed_name(self):
        names = ["LV1/Z1", "Sample_Scope_Box", "LV2/Z1"]
        self.assertEqual(CS.pick_seed_name(names), "Sample_Scope_Box")
        self.assertEqual(CS.pick_seed_name(names, "LV2/Z1"), "LV2/Z1")
        self.assertEqual(CS.pick_seed_name(names, "gone"), "Sample_Scope_Box")
        self.assertEqual(CS.pick_seed_name(["only"]), "only")
        self.assertIsNone(CS.pick_seed_name(["a", "b"]))
        self.assertIsNone(CS.pick_seed_name([]))


class ParseMm(unittest.TestCase):
    def test_plain_numbers(self):
        self.assertEqual(CS.parse_mm("1500"), 1500.0)
        self.assertEqual(CS.parse_mm(" 1234.5 "), 1234.5)

    def test_suffix_and_comma(self):
        self.assertEqual(CS.parse_mm("1500 mm"), 1500.0)
        self.assertEqual(CS.parse_mm("1500MM"), 1500.0)
        self.assertEqual(CS.parse_mm("1234,5"), 1234.5)

    def test_rejects(self):
        for bad in (None, "", "   ", "abc", "0", "-100", "mm", "1e400"):
            self.assertIsNone(CS.parse_mm(bad), bad)


class MmText(unittest.TestCase):
    def test_trims(self):
        self.assertEqual(CS.mm_text(1500.0), "1500")
        self.assertEqual(CS.mm_text(1234.5), "1234.5")
        self.assertEqual(CS.mm_text("x"), "")


class FilterLabels(unittest.TestCase):
    LABELS = ["GEM_Vault : 1200x1200   (3 placed)",
              "GEM_Vault : 1800x1800   (1 placed)",
              "MAN_Manhole : DN1200   (7 placed)"]

    def test_empty_keeps_all(self):
        self.assertEqual(CS.filter_labels(self.LABELS, ""), [0, 1, 2])
        self.assertEqual(CS.filter_labels(self.LABELS, None), [0, 1, 2])

    def test_case_insensitive_words(self):
        self.assertEqual(CS.filter_labels(self.LABELS, "vault"), [0, 1])
        self.assertEqual(CS.filter_labels(self.LABELS, "VAULT 1800"), [1])
        self.assertEqual(CS.filter_labels(self.LABELS, "1200"), [0, 2])
        self.assertEqual(CS.filter_labels(self.LABELS, "nothing"), [])

    def test_none_label(self):
        self.assertEqual(CS.filter_labels([None, "abc"], "ab"), [1])


if __name__ == "__main__":
    unittest.main()
