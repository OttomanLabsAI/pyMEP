#!/usr/bin/env python3
"""Unit tests for pymep_manhole_export - the pure parts of the Export
Manholes button (plane name filter, records, sorting, file naming).

Run:  python3 tests/test_manhole_export.py
"""

import json
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "pyMEP.extension", "lib"))

import pymep_manhole_export as M


def to_mm(v):
    return round(v * 304.8, 2)


class PlaneNames(unittest.TestCase):

    def test_wanted_names(self):
        for n in ("a1", "A7", "b3", "z6", "a_conduit_boundary_1",
                  "Z_CONDUIT_BOUNDARY_2", " b_conduit_boundary_2 "):
            self.assertTrue(M.plane_wanted(n), n)

    def test_unwanted_names(self):
        for n in ("", None, "Center (Left/Right)", "a", "ab1", "c1",
                  "a1x", "x1", "a_conduit_boundary", "conduit_boundary_1"):
            self.assertFalse(M.plane_wanted(n), repr(n))

    def test_axis(self):
        self.assertEqual(M.plane_axis("A3"), "a")
        self.assertEqual(M.plane_axis("z_conduit_boundary_1"), "z")


class Records(unittest.TestCase):

    def test_plane_record_offset_and_units(self):
        # plane through (2 ft, 0, 0) with normal +X -> offset 2 ft
        r = M.plane_record("a1", (2.0, 0.0, 0.0), (1.0, 0.0, 0.0), to_mm)
        self.assertEqual(r, {"name": "a1", "axis": "a",
                             "normal": [1.0, 0.0, 0.0], "offset_mm": 609.6,
                             "origin_mm": [609.6, 0.0, 0.0]})
        # a normal pointing the other way gives a negative offset
        r = M.plane_record("b7", (0.0, 3.0, 0.0), (0.0, -1.0, 0.0), to_mm)
        self.assertEqual(r["offset_mm"], -914.4)
        # the offset is origin . normal, so a point anywhere on the
        # plane gives the same offset
        r1 = M.plane_record("z1", (5.0, 7.0, 1.0), (0.0, 0.0, 1.0), to_mm)
        r2 = M.plane_record("z1", (-2.0, 0.0, 1.0), (0.0, 0.0, 1.0), to_mm)
        self.assertEqual(r1["offset_mm"], r2["offset_mm"])

    def test_sort_planes(self):
        names = ["z1", "b_conduit_boundary_1", "a7", "a1", "b2",
                 "a_conduit_boundary_2", "z_conduit_boundary_1"]
        recs = [M.plane_record(n, (0, 0, 0), (1, 0, 0), to_mm) for n in names]
        self.assertEqual([r["name"] for r in M.sort_planes(recs)],
                         ["a1", "a7", "a_conduit_boundary_2", "b2",
                          "b_conduit_boundary_1", "z1",
                          "z_conduit_boundary_1"])

    def test_instance_record(self):
        c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
        r = M.instance_record(1234, "MH01", "1200x1200", (1.0, 2.0, 0.5),
                              (c, s, 0.0), (-s, c, 0.0), False, to_mm)
        self.assertEqual(r["id"], 1234)
        self.assertEqual(r["mark"], "MH01")
        self.assertEqual(r["type"], "1200x1200")
        self.assertEqual(r["origin_mm"], [304.8, 609.6, 152.4])
        self.assertEqual(r["rotation_deg"], 30.0)
        self.assertEqual(r["family_x_axis"], [round(c, 6), round(s, 6), 0.0])
        self.assertFalse(r["mirrored"])
        # blank mark -> null, mirrored flag carried
        r = M.instance_record(5, "", "T", (0, 0, 0), (1, 0, 0), (0, 1, 0),
                              True, to_mm)
        self.assertIsNone(r["mark"])
        self.assertTrue(r["mirrored"])
        self.assertEqual(r["rotation_deg"], 0.0)
        # no parameters given -> every flag present and null
        self.assertEqual(r["params"], {"obstacle_around": None,
                                       "obstacle_under": None,
                                       "obstacle_over": None})
        # a 180 degree placement
        r = M.instance_record(6, None, "T", (0, 0, 0), (-1, 0, 0),
                              (0, -1, 0), False, to_mm)
        self.assertEqual(r["rotation_deg"], 180.0)

    def test_obstacle_flags(self):
        self.assertEqual(M.param_key("Obstacle_Over"), "obstacle_over")
        self.assertEqual(M.param_key("obstable_around"), "obstacle_around")
        self.assertIsNone(M.param_key("obstacle_sideways"))
        self.assertIsNone(M.param_key("Mark"))
        self.assertIsNone(M.param_key(None))
        self.assertIs(M.as_flag(1), True)
        self.assertIs(M.as_flag(0), False)
        self.assertIs(M.as_flag(None), None)
        self.assertIs(M.as_flag(True), True)
        flags = M.obstacle_flags({"obstable_around": 1, "obstacle_under": 0,
                                  "Width": 1200, "Mark": "MH01"})
        self.assertEqual(flags, {"obstacle_around": True,
                                 "obstacle_under": False,
                                 "obstacle_over": None})
        # carried on the instance record and the family defaults
        r = M.instance_record(7, "MH02", "T", (0, 0, 0), (1, 0, 0),
                              (0, 1, 0), False, to_mm,
                              {"obstacle_over": 1})
        self.assertEqual(r["params"]["obstacle_over"], True)
        self.assertIsNone(r["params"]["obstacle_under"])
        f = M.family_record("MH", [], [], {"obstable_around": 1,
                                           "obstacle_under": 1,
                                           "obstacle_over": 1})
        self.assertEqual(f["param_defaults"], {"obstacle_around": True,
                                               "obstacle_under": True,
                                               "obstacle_over": True})
        self.assertEqual(M.family_record("MH", [])["param_defaults"],
                         {"obstacle_around": None, "obstacle_under": None,
                          "obstacle_over": None})

    def test_export_document_keeps_only_families_with_planes(self):
        p = M.plane_record("a1", (0, 0, 0), (1, 0, 0), to_mm)
        fams = [M.family_record("Manhole", [p], [{"id": 1}]),
                M.family_record("Door", [], [{"id": 2}]),
                None]
        d = M.export_document(u"C:\\proj\\model.rvt", fams)
        self.assertEqual(d["units"], "mm")
        self.assertEqual(d["source"], u"C:\\proj\\model.rvt")
        self.assertEqual([f["family"] for f in d["families"]], ["Manhole"])
        # the whole thing is JSON serialisable
        json.dumps(d)
        self.assertEqual(M.export_document(None, [])["source"], u"")


class Naming(unittest.TestCase):

    def test_default_file_name(self):
        self.assertEqual(M.default_file_name("Site A.rvt"),
                         "Site A-manholes.json")
        self.assertEqual(M.default_file_name("MH_1200.RFA"),
                         "MH_1200-manholes.json")
        self.assertEqual(M.default_file_name("Project1"),
                         "Project1-manholes.json")
        self.assertEqual(M.default_file_name(""), "model-manholes.json")

    def test_ensure_json_ext(self):
        self.assertEqual(M.ensure_json_ext(u"C:\\x\\out"), u"C:\\x\\out.json")
        self.assertEqual(M.ensure_json_ext(u"C:\\x\\out.JSON"),
                         u"C:\\x\\out.JSON")
        self.assertEqual(M.ensure_json_ext(u""), u"")

    def test_summary(self):
        none = M.summary_text(0, 0, u"p")
        self.assertIn("No family", none)
        self.assertIn("a1-a7", none)
        some = M.summary_text(2, 5, u"C:\\out.json")
        self.assertIn("2 family", some)
        self.assertIn("5 placed", some)
        self.assertIn("C:\\out.json", some)
        self.assertIn("family document", M.summary_text(1, 0, u"p", True))


if __name__ == "__main__":
    unittest.main()
