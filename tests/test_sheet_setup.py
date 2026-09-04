#!/usr/bin/env python3
"""Unit tests for the pure helpers behind Chamber Sheet Setup (stdlib
only, no Revit): grouping views by chamber Mark, the scale parser, the
remembered settings and the row layout.

Run:  python3 tests/test_sheet_setup.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "pyMEP.extension", "lib"))

import pymep_sheet_setup as SS


class Keys(unittest.TestCase):
    def test_side_letter(self):
        self.assertEqual(SS.side_letter("LV1/Z1 SIDE A"), "A")
        self.assertEqual(SS.side_letter("LV1/Z1 SIDE C_2"), "C")
        self.assertEqual(SS.side_letter("LV1 SIDEB"), "B")
        for bad in (None, "", "Section 29", "LV1/Z1", "LV1 SIDE a",
                    "LV1 SIDE AB"):
            self.assertIsNone(SS.side_letter(bad), bad)

    def test_key_from_section_name(self):
        self.assertEqual(SS.key_from_section_name("LV1/Z1 SIDE A"), "LV1/Z1")
        self.assertEqual(SS.key_from_section_name("LV1/SIDE A"), "LV1")
        self.assertEqual(SS.key_from_section_name("LV4/Z2 SIDE D_2"), "LV4/Z2")
        self.assertEqual(SS.key_from_section_name("MH-7 SIDE B"), "MH-7")
        self.assertIsNone(SS.key_from_section_name("Section 29"))
        self.assertIsNone(SS.key_from_section_name("SIDE A"))

    def test_has_key_is_whole_token(self):
        self.assertTrue(SS.has_key("LV1/Z1", "LV1/Z1"))
        self.assertTrue(SS.has_key("LV1/Z1 SIDE A", "LV1/Z1"))
        self.assertTrue(SS.has_key("Plan lv1/z1", "LV1/Z1"))
        self.assertTrue(SS.has_key("LV1/Z1", "LV1"))
        self.assertFalse(SS.has_key("LV1/Z10", "LV1/Z1"))
        self.assertFalse(SS.has_key("LV11/Z1", "LV1/Z1"))
        self.assertFalse(SS.has_key("XLV1/Z1", "LV1/Z1"))
        self.assertFalse(SS.has_key("Level 1", "LV1"))
        self.assertFalse(SS.has_key("LV1", ""))

    def test_best_key_prefers_the_longest(self):
        keys = {"LV1", "LV1/Z1", "LV1/Z2"}
        self.assertEqual(SS.best_key("LV1/Z1 SIDE A", keys), "LV1/Z1")
        self.assertEqual(SS.best_key("LV1/Z2", keys), "LV1/Z2")
        self.assertEqual(SS.best_key("LV1/SIDE A", keys), "LV1")
        self.assertIsNone(SS.best_key("LV1/Z3", {"LV1/Z1"}))


class GroupChamberViews(unittest.TestCase):
    VIEWS = [
        ("Level 1", "plan"),
        ("LV1/Z1", "plan"),
        ("LV1/Z1 SIDE C", "section"),
        ("LV1/Z1 SIDE A", "section"),
        ("LV1/Z1 SIDE B", "section"),
        ("LV1/Z2", "plan"),                 # same LV number, other zone
        ("LV1/Z2 SIDE A", "section"),
        ("LV1/Z10", "plan"),
        ("LV1/Z10 SIDE A", "section"),
        ("LV2/Z2 SIDE A", "section"),
        ("LV3/Z2", "plan"),
        ("Section 29", "section"),
    ]

    def test_grouping_keeps_zones_apart(self):
        g = SS.group_chamber_views(self.VIEWS, known_marks=["LV3/Z2"])
        self.assertEqual(sorted(g), ["LV1/Z1", "LV1/Z10", "LV1/Z2", "LV2/Z2",
                                     "LV3/Z2"])
        self.assertEqual(g["LV1/Z1"]["plans"], ["LV1/Z1"])
        self.assertEqual([n for _l, n in g["LV1/Z1"]["sections"]],
                         ["LV1/Z1 SIDE A", "LV1/Z1 SIDE B", "LV1/Z1 SIDE C"])
        self.assertEqual(g["LV1/Z2"]["plans"], ["LV1/Z2"])
        self.assertEqual([n for _l, n in g["LV1/Z2"]["sections"]],
                         ["LV1/Z2 SIDE A"])
        self.assertEqual(g["LV1/Z10"]["plans"], ["LV1/Z10"])
        self.assertEqual(g["LV2/Z2"]["plans"], [])
        self.assertEqual(g["LV3/Z2"], {"plans": ["LV3/Z2"], "sections": []})

    def test_bare_key_does_not_swallow_zoned_views(self):
        views = self.VIEWS + [("LV1/SIDE D", "section")]
        g = SS.group_chamber_views(views)
        self.assertEqual([n for _l, n in g["LV1"]["sections"]],
                         ["LV1/SIDE D"])
        self.assertEqual(g["LV1"]["plans"], [])
        self.assertEqual(g["LV1/Z1"]["plans"], ["LV1/Z1"])

    def test_plain_plans_stay_out_without_known_marks(self):
        g = SS.group_chamber_views(self.VIEWS)
        self.assertNotIn("Level 1", g)
        self.assertNotIn("LV3/Z2", g)
        self.assertNotIn("Section 29", [n for grp in g.values()
                                        for _l, n in grp["sections"]])

    def test_ordered_and_label(self):
        g = SS.group_chamber_views(self.VIEWS)
        self.assertEqual(SS.ordered_views(g["LV1/Z1"]),
                         ["LV1/Z1", "LV1/Z1 SIDE A", "LV1/Z1 SIDE B",
                          "LV1/Z1 SIDE C"])
        self.assertEqual(SS.ordered_views(g["LV2/Z2"]), ["LV2/Z2 SIDE A"])
        self.assertEqual(SS.group_label("LV1/Z1", g["LV1/Z1"]),
                         "LV1/Z1   (plan LV1/Z1 + 3 sections)")
        self.assertEqual(SS.group_label("LV2/Z2", g["LV2/Z2"]),
                         "LV2/Z2   (no plan + 1 section)")


class ParseScale(unittest.TestCase):
    def test_forms(self):
        for text in ("1:20", "1 : 20", "1/20", "20", " 20 ", ":20"):
            self.assertEqual(SS.parse_scale(text), 20, text)
        self.assertEqual(SS.scale_text(50), "1:50")

    def test_rejects(self):
        for bad in (None, "", "0", "-5", "2:20", "abc", "1:0", "1:2.5"):
            self.assertIsNone(SS.parse_scale(bad), bad)


class SheetSettings(unittest.TestCase):
    def test_defaults(self):
        s = SS.sheet_settings({})
        self.assertEqual(s["scale"], 20)
        self.assertEqual(s["gap"], 15.0)
        self.assertEqual(s["left"], 20.0)
        self.assertEqual(s["top"], 20.0)
        self.assertEqual(s["label"], 12.0)
        self.assertEqual(s["plan_template"], "")
        self.assertEqual(s["section_template"], "")
        self.assertEqual(SS.sheet_settings(None)["scale"], 20)

    def test_templates_remembered(self):
        s = SS.sheet_settings({SS.SETTINGS_SHEET_PLAN_TEMPLATE: "CHAMBER PLAN",
                               SS.SETTINGS_SHEET_SECTION_TEMPLATE: None})
        self.assertEqual(s["plan_template"], "CHAMBER PLAN")
        self.assertEqual(s["section_template"], "")

    def test_template_choice(self):
        self.assertEqual(SS.template_choice(None), "")
        self.assertEqual(SS.template_choice(""), "")
        self.assertEqual(SS.template_choice(SS.LEAVE_TEMPLATE), "")
        self.assertEqual(SS.template_choice(SS.DEFAULT_VIEWPORT), "")
        self.assertEqual(SS.sheet_settings({})["viewport_type"], "")
        self.assertEqual(SS.sheet_settings(
            {SS.SETTINGS_SHEET_VIEWPORT_TYPE: "No Title"})["viewport_type"],
            "No Title")
        self.assertEqual(SS.template_choice("  CHAMBER PLAN "), "CHAMBER PLAN")

    def test_remembered_and_broken(self):
        s = SS.sheet_settings({SS.SETTINGS_SHEET_SCALE: 50,
                               SS.SETTINGS_SHEET_GAP: "10",
                               SS.SETTINGS_SHEET_LEFT: None,
                               SS.SETTINGS_SHEET_TOP: -3,
                               SS.SETTINGS_SHEET_LABEL: "x"})
        self.assertEqual(s["scale"], 50)
        self.assertEqual(s["gap"], 10.0)
        self.assertEqual(s["left"], 20.0)
        self.assertEqual(s["top"], 20.0)
        self.assertEqual(s["label"], 12.0)
        self.assertEqual(SS.sheet_settings(
            {SS.SETTINGS_SHEET_SCALE: "1:25"})["scale"], 25)


class NaturalKey(unittest.TestCase):
    def test_orders_numbers_numerically(self):
        keys = ["LV10", "LV2", "LV1", "MH-3", "LV1A", "lv11"]
        self.assertEqual(sorted(keys, key=SS.natural_key),
                         ["LV1", "LV1A", "LV2", "LV10", "lv11", "MH-3"])
        self.assertEqual(SS.natural_key(None), [])

    def test_filter_labels(self):
        labels = ["LV1   (plan + 3 sections)", "LV10   (no plan + 1 section)"]
        self.assertEqual(SS.filter_labels(labels, ""), [0, 1])
        self.assertEqual(SS.filter_labels(labels, "no plan"), [1])
        self.assertEqual(SS.filter_labels(labels, "lv1 "), [0, 1])


class Layout(unittest.TestCase):
    def test_single_row(self):
        rows = [[(100, 80), (60, 80), (60, 80)]]
        centres, below = SS.layout(rows, 600, 400, 20, 20, 10, 10, 12)
        self.assertEqual(below, 0)
        self.assertEqual(centres, [[(70.0, 340.0), (160.0, 340.0),
                                    (230.0, 340.0)]])

    def test_next_chamber_below(self):
        rows = [[(100, 80), (60, 50)], [(100, 80)]]
        centres, below = SS.layout(rows, 600, 400, 20, 20, 10, 10, 12)
        # second row top = 380 - (80 + 12 + 10) = 278
        self.assertEqual(centres[1], [(70.0, 238.0)])
        self.assertEqual(below, 0)

    def test_wraps_inside_a_row(self):
        rows = [[(300, 80), (300, 80), (100, 80)]]
        centres, below = SS.layout(rows, 500, 400, 20, 20, 10, 10, 12)
        first, second, third = centres[0]
        self.assertEqual(first, (170.0, 340.0))
        self.assertEqual(second, (170.0, 238.0))     # wrapped to a new line
        self.assertEqual(third, (380.0, 238.0))
        self.assertEqual(below, 0)

    def test_first_view_never_wraps_even_if_too_wide(self):
        centres, below = SS.layout([[(900, 80)]], 500, 400, 20, 20, 10, 10, 12)
        self.assertEqual(centres, [[(470.0, 340.0)]])

    def test_counts_views_below_the_sheet(self):
        rows = [[(100, 200)], [(100, 200)], [(100, 200)]]
        centres, below = SS.layout(rows, 600, 400, 20, 20, 10, 10, 12)
        self.assertEqual(below, 2)
        self.assertEqual(len(centres), 3)

    def test_empty(self):
        self.assertEqual(SS.layout([], 600, 400, 20, 20, 10, 10, 12), ([], 0))
        self.assertEqual(SS.layout([[]], 600, 400, 20, 20, 10, 10, 12),
                         ([[]], 0))


if __name__ == "__main__":
    unittest.main()
