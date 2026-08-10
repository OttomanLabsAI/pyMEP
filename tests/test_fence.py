#!/usr/bin/env python3
"""Unit tests for the Fence button's pure half (stdlib only, no
Revit): station maths along the line (spacing, justification,
endpoints, closed loops), the polyline walk that turns a station into
a point + tangent, and the spacing-configuration store.

Run:  python3 tests/test_fence.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "pyMEP.extension", "lib"))

import pymep_fence as F


class Stations(unittest.TestCase):
    def test_start_with_endpoints(self):
        self.assertEqual(F.stations(10.0, 3.0, "start", True),
                         [0.0, 3.0, 6.0, 9.0, 10.0])

    def test_start_without_endpoints(self):
        self.assertEqual(F.stations(10.0, 3.0, "start", False),
                         [3.0, 6.0, 9.0])

    def test_exact_multiple_no_duplicate_end(self):
        self.assertEqual(F.stations(9.0, 3.0, "start", True),
                         [0.0, 3.0, 6.0, 9.0])
        self.assertEqual(F.stations(9.0, 3.0, "start", False),
                         [3.0, 6.0])

    def test_end_justify(self):
        self.assertEqual(F.stations(10.0, 3.0, "end", False),
                         [1.0, 4.0, 7.0])
        self.assertEqual(F.stations(10.0, 3.0, "end", True),
                         [0.0, 1.0, 4.0, 7.0, 10.0])

    def test_centre_splits_leftover_evenly(self):
        # 10 = 3*3 + 1 -> pattern starts at 0.5, ends at 9.5
        self.assertEqual(F.stations(10.0, 3.0, "centre", False),
                         [0.5, 3.5, 6.5, 9.5])

    def test_centre_short_line_gets_the_middle(self):
        self.assertEqual(F.stations(4.0, 10.0, "centre", False),
                         [2.0])

    def test_spacing_longer_than_line_start(self):
        self.assertEqual(F.stations(10.0, 50.0, "start", True),
                         [0.0, 10.0])
        self.assertEqual(F.stations(10.0, 50.0, "start", False), [])

    def test_no_spacing_endpoints_only(self):
        self.assertEqual(F.stations(10.0, None, "start", True),
                         [0.0, 10.0])
        self.assertEqual(F.stations(10.0, 0.0, "start", False), [])

    def test_zero_length(self):
        self.assertEqual(F.stations(0.0, 3.0, "start", True), [0.0])
        self.assertEqual(F.stations(0.0, 3.0, "start", False), [])

    def test_closed_loop_drops_the_seam_twin(self):
        # 0 and length are the same point on a loop - one instance
        self.assertEqual(F.stations(9.0, 3.0, "start", True,
                                    closed=True),
                         [0.0, 3.0, 6.0])

    def test_float_spacing_no_rounding_extras(self):
        out = F.stations(39400.0 / 304.8, 5000.0 / 304.8, "start",
                         True)
        # 39.4 m at 5 m: 0..35 m marks then the 39.4 endpoint
        self.assertEqual(len(out), 9)
        self.assertAlmostEqual(out[-1], 39400.0 / 304.8, places=9)


class PointAt(unittest.TestCase):
    POLY = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0)]

    def test_first_segment(self):
        p, t = F.point_at(self.POLY, 4.0)
        self.assertEqual(p, (4.0, 0.0, 0.0))
        self.assertEqual(t, (1.0, 0.0))

    def test_second_segment_tangent_turns(self):
        p, t = F.point_at(self.POLY, 15.0)
        self.assertEqual(p, (10.0, 5.0, 0.0))
        self.assertEqual(t, (0.0, 1.0))

    def test_clamps_to_the_ends(self):
        p0, t0 = F.point_at(self.POLY, -5.0)
        self.assertEqual(p0, (0.0, 0.0, 0.0))
        self.assertEqual(t0, (1.0, 0.0))
        p1, t1 = F.point_at(self.POLY, 999.0)
        self.assertEqual(p1, (10.0, 10.0, 0.0))
        self.assertEqual(t1, (0.0, 1.0))

    def test_kink_point_belongs_to_the_first_segment(self):
        p, t = F.point_at(self.POLY, 10.0)
        self.assertEqual(p, (10.0, 0.0, 0.0))
        self.assertEqual(t, (1.0, 0.0))

    def test_length_and_closed(self):
        self.assertAlmostEqual(F.poly_length(self.POLY), 20.0)
        self.assertFalse(F.is_closed(self.POLY))
        loop = self.POLY + [(0.0, 0.0, 0.0)]
        self.assertTrue(F.is_closed(loop))


class ConfigStore(unittest.TestCase):
    def test_empty_settings_seed_default(self):
        cfgs = F.get_configs({})
        self.assertEqual(list(cfgs.keys()), [F.DEFAULT_NAME])
        self.assertEqual(cfgs[F.DEFAULT_NAME], F.DEFAULT_CONFIG)

    def test_upsert_creates_and_updates(self):
        s = {}
        F.upsert_config(s, "Posts 3m", "3000", True)
        F.upsert_config(s, "Posts 3m", 2500.0, False)
        cfgs = F.get_configs(s)
        self.assertEqual(cfgs["Posts 3m"],
                         {"spacing_mm": 2500.0, "endpoints": False})

    def test_upsert_validates(self):
        self.assertRaises(ValueError, F.upsert_config, {}, "  ",
                          1000, True)
        self.assertRaises(ValueError, F.upsert_config, {}, "x",
                          "abc", True)
        self.assertRaises(ValueError, F.upsert_config, {}, "x",
                          -5, True)

    def test_delete_last_reseeds_default(self):
        s = {}
        F.upsert_config(s, "Only", 1000, True)
        # upsert seeded 'Default' via get_configs, then added 'Only'
        F.delete_config(s, "Only")
        F.delete_config(s, F.DEFAULT_NAME)
        cfgs = F.get_configs(s)
        self.assertEqual(list(cfgs.keys()), [F.DEFAULT_NAME])

    def test_bad_stored_values_are_dropped(self):
        s = {F.SETTINGS_CONFIGS: {"ok": {"spacing_mm": 500,
                                         "endpoints": 0},
                                  "bad": {"spacing_mm": "nope"},
                                  "neg": {"spacing_mm": -3}}}
        cfgs = F.get_configs(s)
        self.assertEqual(list(cfgs.keys()), ["ok"])
        self.assertEqual(cfgs["ok"],
                         {"spacing_mm": 500.0, "endpoints": False})


if __name__ == "__main__":
    unittest.main(verbosity=2)
