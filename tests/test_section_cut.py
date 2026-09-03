#!/usr/bin/env python3
"""Unit tests for the pure section-cut maths behind Create Chamber
Sections' 'does this side cut any pipework?' check (stdlib only, no
Revit): centreline-vs-crop-frame crossing, fitting box straddle, and the
survivor re-lettering.

Run:  python3 tests/test_section_cut.py
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "pyMEP.extension", "lib"))

import pymep_section_cut as SC

# Side A of a chamber centred at the origin: plane at x = +10 looking back
# along -X; right = up x look = (0, -1, 0). Crop is 3 wide / 2 high each way.
FA = ((10.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, 1.0), (-1.0, 0.0, 0.0))
HW, HH = 3.0, 2.0


def rotated_frame(angle_deg, offset=10.0):
    a = math.radians(angle_deg)
    out = (math.cos(a), math.sin(a), 0.0)
    origin = (out[0] * offset, out[1] * offset, 0.0)
    look = (-out[0], -out[1], 0.0)
    up = (0.0, 0.0, 1.0)
    right = (up[1] * look[2] - up[2] * look[1],
             up[2] * look[0] - up[0] * look[2],
             up[0] * look[1] - up[1] * look[0])
    return origin, right, up, look, out


class ToLocal(unittest.TestCase):
    def test_axes(self):
        self.assertEqual(SC.to_local(FA, (10.0, 0.0, 0.0)), (0.0, 0.0, 0.0))
        x, y, z = SC.to_local(FA, (4.0, 2.0, 5.0))
        self.assertAlmostEqual(x, -2.0)   # +Y world is -right
        self.assertAlmostEqual(y, 5.0)
        self.assertAlmostEqual(z, 6.0)    # 6 in front of the plane


class SegmentCut(unittest.TestCase):
    def test_run_through_plane_inside_crop(self):
        self.assertTrue(SC.segment_cut(FA, (0, 0, 0), (20, 0, 0), HW, HH))
        # direction does not matter
        self.assertTrue(SC.segment_cut(FA, (20, 0, 0), (0, 0, 0), HW, HH))

    def test_crossing_outside_width(self):
        self.assertFalse(SC.segment_cut(FA, (0, 5, 0), (20, 5, 0), HW, HH))

    def test_radius_widens_crop(self):
        self.assertTrue(SC.segment_cut(FA, (0, 5, 0), (20, 5, 0), HW, HH,
                                       radius=2.5))

    def test_crossing_outside_height(self):
        self.assertFalse(SC.segment_cut(FA, (0, 0, 5), (20, 0, 5), HW, HH))
        self.assertFalse(SC.segment_cut(FA, (0, 0, -5), (20, 0, -5), HW, HH))

    def test_parallel_run_beyond_plane(self):
        # runs along the side wall, half a unit inside the plane
        self.assertFalse(SC.segment_cut(FA, (10.5, -1, 0), (10.5, 1, 0),
                                        HW, HH))

    def test_parallel_run_within_radius_band(self):
        self.assertTrue(SC.segment_cut(FA, (10.5, -1, 0), (10.5, 1, 0),
                                       HW, HH, radius=0.6))

    def test_parallel_run_in_band_but_outside_crop(self):
        self.assertFalse(SC.segment_cut(FA, (10.0, 4, 0), (10.0, 6, 0),
                                        HW, HH))

    def test_run_stopping_short_of_plane(self):
        # comes from the chamber side and ends before the plane
        self.assertFalse(SC.segment_cut(FA, (0, 0, 0), (9.5, 0, 0), HW, HH))
        # or approaches from outside and ends before it
        self.assertFalse(SC.segment_cut(FA, (20, 0, 0), (10.5, 0, 0), HW, HH))

    def test_run_ending_on_plane_counts(self):
        self.assertTrue(SC.segment_cut(FA, (0, 0, 0), (10.0, 0, 0), HW, HH))
        self.assertTrue(SC.segment_cut(FA, (0, 0, 0), (9.5, 0, 0), HW, HH,
                                       radius=0.5))

    def test_rotated_chamber(self):
        origin, right, up, look, out = rotated_frame(37.0)
        frame = (origin, right, up, look)
        p0 = (0.0, 0.0, 0.0)
        p1 = (out[0] * 30.0, out[1] * 30.0, 0.0)
        self.assertTrue(SC.segment_cut(frame, p0, p1, HW, HH))
        # the same run rotated 90 deg runs parallel to the plane: no cut
        perp = (-out[1], out[0], 0.0)
        q0 = (perp[0] * -30.0, perp[1] * -30.0, 0.0)
        q1 = (perp[0] * 30.0, perp[1] * 30.0, 0.0)
        self.assertFalse(SC.segment_cut(frame, q0, q1, HW, HH))

    def test_none_radius(self):
        self.assertTrue(SC.segment_cut(FA, (0, 0, 0), (20, 0, 0), HW, HH,
                                       radius=None))


class PolylineCut(unittest.TestCase):
    def test_flex_run(self):
        pts = [(0, 0, 0), (5, 1, 0), (9, 1.5, 0), (14, 1.0, 0)]
        self.assertTrue(SC.polyline_cut(FA, pts, HW, HH))

    def test_flex_run_that_turns_back(self):
        pts = [(0, 0, 0), (5, 1, 0), (9, 1.5, 0), (5, 3.0, 0)]
        self.assertFalse(SC.polyline_cut(FA, pts, HW, HH))

    def test_degenerate(self):
        self.assertFalse(SC.polyline_cut(FA, [], HW, HH))
        self.assertFalse(SC.polyline_cut(FA, [(10, 0, 0)], HW, HH))


class BoxCut(unittest.TestCase):
    def test_straddling_box(self):
        self.assertTrue(SC.box_cut(FA, (9, -1, -1), (11, 1, 1), HW, HH))

    def test_box_beyond_plane(self):
        self.assertFalse(SC.box_cut(FA, (11, -1, -1), (13, 1, 1), HW, HH))

    def test_box_behind_plane(self):
        self.assertFalse(SC.box_cut(FA, (5, -1, -1), (9, 1, 1), HW, HH))

    def test_straddling_but_outside_crop(self):
        self.assertFalse(SC.box_cut(FA, (9, 4, -1), (11, 6, 1), HW, HH))
        self.assertFalse(SC.box_cut(FA, (9, -1, 3), (11, 1, 5), HW, HH))

    def test_box_touching_plane(self):
        self.assertTrue(SC.box_cut(FA, (10, -1, -1), (12, 1, 1), HW, HH))


class PlanSides(unittest.TestCase):
    def test_two_sides_cut(self):
        sides, all_kept = SC.plan_sides([3, 0, 2, 0])
        self.assertEqual(sides, [(0, "A", "A"), (2, "C", "B")])
        self.assertFalse(all_kept)

    def test_only_last_side_cut(self):
        sides, all_kept = SC.plan_sides([0, 0, 0, 4])
        self.assertEqual(sides, [(3, "D", "A")])
        self.assertFalse(all_kept)

    def test_all_sides_cut(self):
        sides, all_kept = SC.plan_sides([1, 1, 1, 1])
        self.assertEqual(sides, [(0, "A", "A"), (1, "B", "B"),
                                 (2, "C", "C"), (3, "D", "D")])
        self.assertFalse(all_kept)

    def test_nothing_cut_keeps_all(self):
        sides, all_kept = SC.plan_sides([0, 0, 0, 0])
        self.assertEqual([s[2] for s in sides], ["A", "B", "C", "D"])
        self.assertTrue(all_kept)

    def test_none_counts(self):
        sides, all_kept = SC.plan_sides([None, 2, None, None])
        self.assertEqual(sides, [(1, "B", "A")])


class LettersNeeded(unittest.TestCase):
    def test_max_over_chambers(self):
        two = SC.plan_sides([1, 0, 1, 0])[0]
        four = SC.plan_sides([0, 0, 0, 0])[0]
        one = SC.plan_sides([0, 0, 5, 0])[0]
        self.assertEqual(SC.letters_needed([two, one]), ("A", "B"))
        self.assertEqual(SC.letters_needed([two, four]),
                         ("A", "B", "C", "D"))
        self.assertEqual(SC.letters_needed([one]), ("A",))
        self.assertEqual(SC.letters_needed([]), ())


if __name__ == "__main__":
    unittest.main()
