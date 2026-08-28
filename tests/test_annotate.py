#!/usr/bin/env python3
"""Unit tests for the Annotate button's pure half (stdlib only, no
Revit): grouping runs into banks, reading a bank's arrangement as
'2x2', and composing the label's parts in order across lines.

Run:  python3 tests/test_annotate.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "pyMEP.extension", "lib"))

import pymep_annotate as A


def run(x0, y0, x1, y1, z=0.0, dia=150.0, slope=0.0):
    """A run item as the script builds them (mm)."""
    d = A.normalise_dir(x1 - x0, y1 - y0)
    return {"dir": d,
            "mid": ((x0 + x1) / 2.0, (y0 + y1) / 2.0, z),
            "ends": ((x0, y0, z), (x1, y1, z)),
            "dia": dia, "slope": slope}


class Direction(unittest.TestCase):
    def test_sign_normalised(self):
        # the same line drawn backwards gives the SAME direction, so
        # neighbours in a bank still match
        self.assertEqual(A.normalise_dir(10.0, 0.0),
                         A.normalise_dir(-10.0, 0.0))
        self.assertEqual(A.normalise_dir(0.0, -5.0), (0.0, 1.0))

    def test_no_plan_length(self):
        self.assertIsNone(A.normalise_dir(0.0, 0.0))

    def test_parallel_within_tolerance(self):
        a = A.normalise_dir(1.0, 0.0)
        self.assertTrue(A.parallel(a, A.normalise_dir(1.0, 0.05)))
        self.assertFalse(A.parallel(a, A.normalise_dir(1.0, 1.0)))


class SameBank(unittest.TestCase):
    def test_side_by_side_is_one_bank(self):
        a = run(0, 0, 10000, 0)
        b = run(0, 200, 10000, 200)
        self.assertTrue(A.same_bank(a, b))

    def test_stacked_is_one_bank(self):
        a = run(0, 0, 10000, 0, z=0.0)
        b = run(0, 0, 10000, 0, z=200.0)
        self.assertTrue(A.same_bank(a, b))

    def test_far_apart_across_is_not(self):
        a = run(0, 0, 10000, 0)
        b = run(0, 5000, 10000, 5000)      # 5 m away, 150 mm runs
        self.assertFalse(A.same_bank(a, b))

    def test_crossing_runs_are_not(self):
        a = run(0, 0, 10000, 0)
        b = run(5000, -5000, 5000, 5000)   # perpendicular
        self.assertFalse(A.same_bank(a, b))

    def test_collinear_but_distant_along_is_not(self):
        # the bug this guards: two conduits on the SAME line but in
        # different trenches 100 m apart must not be one bank
        a = run(0, 0, 10000, 0)
        b = run(100000, 0, 110000, 0)
        self.assertFalse(A.same_bank(a, b))

    def test_touching_segments_of_one_run_join(self):
        a = run(0, 0, 10000, 0)
        b = run(10000, 0, 20000, 0)
        self.assertTrue(A.same_bank(a, b))

    def test_two_50mm_conduits_at_210_centres(self):
        # straight from the drawing: 50 mm conduits 210 mm apart are
        # ONE bank - the old 3x-diameter reach split them into two
        # 1x1 labels
        a = run(0, 0, 10000, 0, dia=50.0)
        b = run(0, 210, 10000, 210, dia=50.0)
        self.assertTrue(A.same_bank(a, b))

    def test_gap_is_the_clear_distance_between_surfaces(self):
        # 600 mm pipes at 900 mm centres: 300 mm of clear air, inside
        # the default gap
        big_a = run(0, 0, 10000, 0, dia=600.0)
        big_b = run(0, 900, 10000, 900, dia=600.0)
        self.assertTrue(A.same_bank(big_a, big_b))
        # 50 mm conduits at the same centres: 850 mm of air, outside it
        small_a = run(0, 0, 10000, 0, dia=50.0)
        small_b = run(0, 900, 10000, 900, dia=50.0)
        self.assertFalse(A.same_bank(small_a, small_b))

    def test_gap_is_tunable(self):
        a = run(0, 0, 10000, 0, dia=50.0)
        b = run(0, 900, 10000, 900, dia=50.0)
        self.assertTrue(A.same_bank(a, b, gap_mm=1000.0))
        self.assertFalse(A.same_bank(a, b, gap_mm=100.0))
        # a tighter gap splits the 210 mm pair back apart
        c = run(0, 210, 10000, 210, dia=50.0)
        self.assertFalse(A.same_bank(a, c, gap_mm=100.0))


class Cluster(unittest.TestCase):
    def test_chains_transitively(self):
        # a row of four, each only reaching its neighbour
        items = [run(0, i * 200, 10000, i * 200) for i in range(4)]
        groups = A.cluster(len(items),
                           lambda i, j: A.same_bank(items[i], items[j]))
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0], [0, 1, 2, 3])

    def test_two_separate_banks(self):
        items = [run(0, 0, 10000, 0), run(0, 200, 10000, 200),
                 run(0, 50000, 10000, 50000)]
        groups = A.cluster(len(items),
                           lambda i, j: A.same_bank(items[i], items[j]))
        self.assertEqual([sorted(g) for g in groups], [[0, 1], [2]])

    def test_singletons(self):
        self.assertEqual(A.cluster(3, lambda i, j: False),
                         [[0], [1], [2]])


class DrawingCase(unittest.TestCase):
    """The screenshot: two 50 mm ELV conduits at 210 mm centres came
    out as two '1x1 50mm' labels instead of one bank."""

    def test_one_bank_reading_2x1(self):
        items = [run(0, 0, 20000, 0, dia=50.0),
                 run(0, 210, 20000, 210, dia=50.0)]
        groups = A.cluster(len(items),
                           lambda i, j: A.same_bank(items[i], items[j]))
        self.assertEqual(len(groups), 1)
        # cells as the script builds them, in the bank's own frame
        cells = [(0.0, 0.0), (210.0, 0.0)]
        tol = max(10.0, 0.4 * 50.0)
        self.assertEqual(A.combo_text(cells, tol), "2x1")
        self.assertEqual(A.dia_text([50.0, 50.0]), "50")
        vals = {A.ITEM_PREFIX: "ELV", A.ITEM_COMBO: "2x1",
                A.ITEM_DIA: "50"}
        self.assertEqual(
            A.compose(vals, A.DEFAULT_ORDER, [True, False, False],
                      A.DEFAULT_SUFFIXES),
            u"ELV\n2x1 50\u00d8")


class Arrangement(unittest.TestCase):
    TOL = 60.0

    def test_two_by_two(self):
        cells = [(0, 0), (200, 0), (0, 200), (200, 200)]
        self.assertEqual(A.combo_text(cells, self.TOL), "2x2")

    def test_one_by_two_is_across_by_up(self):
        # one across, two stacked
        self.assertEqual(A.combo_text([(0, 0), (0, 200)], self.TOL),
                         "1x2")
        # two across, one high
        self.assertEqual(A.combo_text([(0, 0), (200, 0)], self.TOL),
                         "2x1")

    def test_single_run(self):
        self.assertEqual(A.combo_text([(0, 0)], self.TOL), "1x1")

    def test_segments_of_one_run_count_once(self):
        # three segments at the SAME position in the bank
        cells = [(0, 0), (0, 0), (0, 10)]
        self.assertEqual(A.combo_text(cells, self.TOL), "1x1")

    def test_ragged_bank_is_counted_not_gridded(self):
        # an L of three is NOT a 2x2
        cells = [(0, 0), (200, 0), (0, 200)]
        self.assertEqual(A.combo_text(cells, self.TOL), "3 no.")

    def test_empty(self):
        self.assertEqual(A.combo_text([], self.TOL), "")

    def test_swap_writes_up_by_across(self):
        # the option: a row of 4 side by side reads 1x4 instead of 4x1
        row4 = [(0, 0), (200, 0), (400, 0), (600, 0)]
        self.assertEqual(A.combo_text(row4, self.TOL), "4x1")
        self.assertEqual(A.combo_text(row4, self.TOL, swap=True), "1x4")
        stack = [(0, 0), (0, 200)]
        self.assertEqual(A.combo_text(stack, self.TOL, swap=True),
                         "2x1")

    def test_swap_leaves_squares_and_counts_alone(self):
        sq = [(0, 0), (200, 0), (0, 200), (200, 200)]
        self.assertEqual(A.combo_text(sq, self.TOL, swap=True), "2x2")
        ragged = [(0, 0), (200, 0), (0, 200)]
        self.assertEqual(A.combo_text(ragged, self.TOL, swap=True),
                         "3 no.")


class Parts(unittest.TestCase):
    def test_dia_text_single_and_mixed(self):
        self.assertEqual(A.dia_text([150.0, 150.0]), "150")
        self.assertEqual(A.dia_text([150.0, 100.0]), "100/150")
        self.assertEqual(A.dia_text([]), "")

    def test_sizes_text_from_run_texts(self):
        # pipes/conduits give plain numbers, rectangular ducts WxH
        self.assertEqual(A.sizes_text(["150", "150"]), "150")
        self.assertEqual(A.sizes_text(["300", "250"]), "250/300")
        self.assertEqual(A.sizes_text(["400x250", "400x250"]),
                         "400x250")
        # a mixed duct bank sorts by the leading number
        self.assertEqual(A.sizes_text(["400x250", "300"]),
                         "300/400x250")
        self.assertEqual(A.sizes_text(["", ""]), "")

    def test_slope_text(self):
        self.assertEqual(A.slope_text(1.0 / 150.0), "1:150")
        self.assertEqual(A.slope_text(0.0), "1:0")
        self.assertEqual(A.slope_text(-1.0 / 80.0), "1:80")


class Compose(unittest.TestCase):
    VALUES = {A.ITEM_PREFIX: "HV", A.ITEM_COMBO: "2x2",
              A.ITEM_DIA: "150", A.ITEM_SLOPE: "1:150"}

    def test_default_order_one_line(self):
        txt = A.compose(self.VALUES, A.DEFAULT_ORDER, A.DEFAULT_BREAKS)
        self.assertEqual(txt, "HV 2x2 150")

    def test_reordered(self):
        order = [A.ITEM_DIA, A.ITEM_COMBO, A.ITEM_PREFIX, A.ITEM_NONE]
        self.assertEqual(A.compose(self.VALUES, order, [False] * 3),
                         "150 2x2 HV")

    def test_line_breaks(self):
        txt = A.compose(self.VALUES, A.DEFAULT_ORDER,
                        [True, False, False])
        self.assertEqual(txt, "HV\n2x2 150")
        txt = A.compose(self.VALUES, A.DEFAULT_ORDER,
                        [True, True, False])
        self.assertEqual(txt, "HV\n2x2\n150")

    def test_empty_part_takes_its_separator_with_it(self):
        # an empty prefix must not leave a leading space or blank line
        vals = dict(self.VALUES)
        vals[A.ITEM_PREFIX] = ""
        self.assertEqual(
            A.compose(vals, A.DEFAULT_ORDER, [True, False, False]),
            "2x2 150")

    def test_break_survives_a_skipped_neighbour(self):
        # prefix breaks, combination is missing -> the break still
        # separates the prefix from the diameter
        vals = {A.ITEM_PREFIX: "HV", A.ITEM_COMBO: "",
                A.ITEM_DIA: "150"}
        self.assertEqual(
            A.compose(vals, A.DEFAULT_ORDER, [True, False, False]),
            "HV\n150")

    def test_slope_omitted_for_conduits(self):
        vals = dict(self.VALUES)
        vals[A.ITEM_SLOPE] = ""            # conduits never set it
        order = [A.ITEM_COMBO, A.ITEM_DIA, A.ITEM_SLOPE, A.ITEM_NONE]
        self.assertEqual(A.compose(vals, order, [False] * 3),
                         "2x2 150")


class Suffixes(unittest.TestCase):
    VALUES = {A.ITEM_PREFIX: "ELV", A.ITEM_COMBO: "2x2",
              A.ITEM_DIA: "150", A.ITEM_SLOPE: "1:150"}

    def test_default_diameter_suffix_is_the_symbol(self):
        self.assertEqual(A.DEFAULT_SUFFIXES[A.ITEM_DIA], u"\u00d8")
        self.assertEqual(
            A.compose(self.VALUES, A.DEFAULT_ORDER, A.DEFAULT_BREAKS,
                      A.DEFAULT_SUFFIXES),
            u"ELV 2x2 150\u00d8")

    def test_suffix_joins_with_no_space(self):
        sufs = {A.ITEM_DIA: "dia", A.ITEM_COMBO: "no."}
        self.assertEqual(
            A.compose(self.VALUES, A.DEFAULT_ORDER, A.DEFAULT_BREAKS,
                      sufs),
            "ELV 2x2no. 150dia")

    def test_a_spaced_suffix_is_kept_verbatim(self):
        # typing ' mm' (with the space) must survive
        self.assertEqual(
            A.compose(self.VALUES, A.DEFAULT_ORDER, A.DEFAULT_BREAKS,
                      {A.ITEM_DIA: " mm"}),
            "ELV 2x2 150 mm")

    def test_a_skipped_part_takes_its_suffix_with_it(self):
        vals = dict(self.VALUES)
        vals[A.ITEM_COMBO] = ""
        self.assertEqual(
            A.compose(vals, A.DEFAULT_ORDER, A.DEFAULT_BREAKS,
                      A.DEFAULT_SUFFIXES),
            u"ELV 150\u00d8")

    def test_no_suffixes_at_all_is_bare(self):
        self.assertEqual(
            A.compose(self.VALUES, A.DEFAULT_ORDER, A.DEFAULT_BREAKS),
            "ELV 2x2 150")

    def test_remembered_and_defaulted(self):
        saved = A.annotate_settings(
            {A.SETTINGS_SUFFIXES: {A.ITEM_DIA: " mm"}})
        self.assertEqual(saved["suffixes"][A.ITEM_DIA], " mm")
        # the parts that were not stored keep their defaults
        self.assertEqual(saved["suffixes"][A.ITEM_COMBO],
                         A.DEFAULT_SUFFIXES[A.ITEM_COMBO])
        self.assertEqual(A.annotate_settings({})["suffixes"],
                         A.DEFAULT_SUFFIXES)


class Settings(unittest.TestCase):
    def test_defaults(self):
        s = A.annotate_settings({})
        self.assertEqual((s["prefix"], s["text_type"]), ("", ""))
        self.assertEqual(s["order"], A.DEFAULT_ORDER)
        self.assertEqual(s["breaks"], A.DEFAULT_BREAKS)
        self.assertEqual(s["gap"], A.DEFAULT_BANK_GAP_MM)
        self.assertFalse(s["swap"])          # across x up by default

    def test_remembered(self):
        s = {A.SETTINGS_PREFIX: "COMMS",
             A.SETTINGS_TEXT_TYPE: "2.5mm Arial",
             A.SETTINGS_ORDER: [A.ITEM_COMBO, A.ITEM_DIA,
                                A.ITEM_PREFIX, A.ITEM_NONE],
             A.SETTINGS_BREAKS: [False, True, False]}
        got = A.annotate_settings(s)
        self.assertEqual(got["prefix"], "COMMS")
        self.assertEqual(got["text_type"], "2.5mm Arial")
        self.assertEqual(got["order"][0], A.ITEM_COMBO)
        self.assertEqual(got["breaks"], [False, True, False])

    def test_repeats_and_junk_are_repaired(self):
        s = {A.SETTINGS_ORDER: [A.ITEM_DIA, A.ITEM_DIA, "nonsense"]}
        got = A.annotate_settings(s)
        self.assertEqual(got["order"], [A.ITEM_DIA, A.ITEM_NONE,
                                        A.ITEM_NONE, A.ITEM_NONE])
        self.assertEqual(len(got["breaks"]), A.SLOTS - 1)

    def test_bank_gap_remembered_and_repaired(self):
        self.assertEqual(
            A.annotate_settings({A.SETTINGS_BANK_GAP: "250"})["gap"],
            250.0)
        for bad in ("abc", -5, None):
            self.assertEqual(
                A.annotate_settings({A.SETTINGS_BANK_GAP: bad})["gap"],
                A.DEFAULT_BANK_GAP_MM)

    def test_swap_remembered(self):
        self.assertTrue(A.annotate_settings(
            {A.SETTINGS_COMBO_SWAP: True})["swap"])

    def test_all_none_falls_back_to_default(self):
        s = {A.SETTINGS_ORDER: ["", "", "", ""]}
        self.assertEqual(A.annotate_settings(s)["order"],
                         A.DEFAULT_ORDER)


if __name__ == "__main__":
    unittest.main(verbosity=2)
