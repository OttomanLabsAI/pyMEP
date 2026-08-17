#!/usr/bin/env python3
"""Unit tests for the Pipes to Conduits pure half (stdlib only, no
Revit): the conduit-size matching maths - which pipe sizes the
standard is missing, and which available nominal a conduit takes.

Run:  python3 tests/test_conduit.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "pyMEP.extension", "lib"))

import pymep_conduit as C


class NearestSize(unittest.TestCase):
    def test_empty_is_none(self):
        self.assertIsNone(C.nearest_size([], 110.0))

    def test_picks_closest(self):
        self.assertEqual(C.nearest_size([16.0, 21.0, 27.0, 103.0], 110.0),
                         103.0)
        self.assertEqual(C.nearest_size([16.0, 21.0, 27.0], 20.0), 21.0)

    def test_tie_goes_first(self):
        self.assertEqual(C.nearest_size([100.0, 120.0], 110.0), 100.0)


class MissingSizes(unittest.TestCase):
    def test_all_present_within_tol(self):
        self.assertEqual(C.missing_sizes([110.0, 160.0],
                                         [110.05, 160.0]), [])

    def test_missing_sorted_and_deduped(self):
        self.assertEqual(
            C.missing_sizes([21.0], [110.0, 160.0, 110.0, 21.0]),
            [110.0, 160.0])

    def test_empty_available_wants_everything(self):
        self.assertEqual(C.missing_sizes([], [63.0, 50.0]), [50.0, 63.0])


class PickSize(unittest.TestCase):
    def test_exact_within_tolerance(self):
        use, exact = C.pick_size([110.0, 160.0], 110.05)
        self.assertEqual(use, 110.0)
        self.assertTrue(exact)

    def test_snaps_to_nearest_when_missing(self):
        use, exact = C.pick_size([103.0, 155.0], 110.0)
        self.assertEqual(use, 103.0)
        self.assertFalse(exact)

    def test_nothing_available(self):
        self.assertEqual(C.pick_size([], 110.0), (None, False))


class SettingsKeys(unittest.TestCase):
    def test_key_names(self):
        # the script and any future settings UI must agree on these
        self.assertEqual(C.SETTINGS_CONDUIT_TYPE, "conduit_type_name")
        self.assertEqual(C.SETTINGS_CONDUIT_ADD_SIZES, "conduit_add_sizes")
        self.assertEqual(C.SETTINGS_CONDUIT_WALL, "conduit_wall_mm")
        self.assertGreater(C.SIZE_TOL_MM, 0.0)


class ConduitSettings(unittest.TestCase):
    def test_defaults(self):
        name, add, wall = C.conduit_settings({})
        self.assertEqual(name, "")
        self.assertTrue(add)                     # size creation on by default
        self.assertEqual(wall, C.DEFAULT_WALL_MM)

    def test_remembered_values(self):
        s = {"conduit_type_name": "Conduit with Fittings",
             "conduit_add_sizes": False, "conduit_wall_mm": 3.5}
        self.assertEqual(C.conduit_settings(s),
                         ("Conduit with Fittings", False, 3.5))

    def test_bad_wall_falls_back(self):
        for bad in ("abc", -1.0, 0.0, None):
            _n, _a, wall = C.conduit_settings({"conduit_wall_mm": bad})
            self.assertEqual(wall, C.DEFAULT_WALL_MM)


class InnerFromTrade(unittest.TestCase):
    def test_trade_minus_twice_the_wall(self):
        # trade = outer = 110; 2 mm wall -> inner 106
        self.assertAlmostEqual(C.inner_from_trade(110.0, 2.0), 106.0)

    def test_overthick_wall_floors_at_a_tenth(self):
        # a 60 mm wall on a 110 trade would invert the size
        self.assertAlmostEqual(C.inner_from_trade(110.0, 60.0), 11.0)

    def test_floor_boundary(self):
        # inner exactly at the floor passes through
        self.assertAlmostEqual(C.inner_from_trade(100.0, 45.0), 10.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
