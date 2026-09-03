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
    def test_key(self):
        self.assertEqual(CS.chamber_key("LV1/Z1"), "LV1")
        self.assertEqual(CS.chamber_key(" LV12 / Z3 "), "LV12")
        self.assertEqual(CS.chamber_key("MH-7"), "MH-7")
        self.assertEqual(CS.chamber_key("/Z1"), "/Z1")
        self.assertEqual(CS.chamber_key(""), "")
        self.assertEqual(CS.chamber_key(None), "")


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
