#!/usr/bin/env python3
"""Unit tests for the Fence button's pure half (stdlib only, no
Revit): station maths along the line (spacing, justification,
endpoints, closed loops), the polyline walk that turns a station into
a point + tangent, and the spacing-configuration store.

Run:  python3 tests/test_fence.py
"""

import json
import os
import shutil
import sys
import tempfile
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
        F.upsert_config(s, "Posts 3m", 2500.0, False, "90",
                        "Pad : 600x600")
        cfgs = F.get_configs(s)
        self.assertEqual(cfgs["Posts 3m"],
                         {"spacing_mm": 2500.0, "endpoints": False,
                          "rotation_deg": 90.0, "post": "",
                          "foundation": "Pad : 600x600",
                          "same_ends": True, "end_post": "",
                          "end_foundation": "", "line_style": "",
                          "priority": 99, "end_priority": False,
                          "panel": "", "panel_width_param": "",
                          "easting_param": "EASTINGS",
                          "northing_param": "NORTHINGS",
                          "terrain_mode": "auto",
                          "terrains": [],
                          "same_end_posts": True,
                          "same_end_foundations": True,
                          "post_col_size": "",
                          "post_fnd_depth": "",
                          "post_height": "",
                          "end_post_col_size": "",
                          "end_post_fnd_depth": "",
                          "end_post_height": "",
                          "fnd_embedment": "",
                          "fnd_diameter": "",
                          "fnd_depth": "",
                          "end_fnd_embedment": "",
                          "end_fnd_diameter": "",
                          "end_fnd_depth": ""})

    def test_upsert_validates(self):
        self.assertRaises(ValueError, F.upsert_config, {}, "  ",
                          1000, True)
        self.assertRaises(ValueError, F.upsert_config, {}, "x",
                          "abc", True)
        self.assertRaises(ValueError, F.upsert_config, {}, "x",
                          -5, True)
        self.assertRaises(ValueError, F.upsert_config, {}, "x",
                          1000, True, "ninety")

    def test_negative_rotation_allowed(self):
        s = {}
        F.upsert_config(s, "x", 1000, True, -45)
        self.assertEqual(F.get_configs(s)["x"]["rotation_deg"],
                         -45.0)

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
                         {"spacing_mm": 500.0, "endpoints": False,
                          "rotation_deg": 0.0, "post": "",
                          "foundation": "", "same_ends": True,
                          "end_post": "", "end_foundation": "",
                          "line_style": "", "priority": 99,
                          "end_priority": False, "panel": "",
                          "panel_width_param": "",
                          "easting_param": "EASTINGS",
                          "northing_param": "NORTHINGS",
                          "terrain_mode": "auto",
                          "terrains": [],
                          "same_end_posts": True,
                          "same_end_foundations": True,
                          "post_col_size": "",
                          "post_fnd_depth": "",
                          "post_height": "",
                          "end_post_col_size": "",
                          "end_post_fnd_depth": "",
                          "end_post_height": "",
                          "fnd_embedment": "",
                          "fnd_diameter": "",
                          "fnd_depth": "",
                          "end_fnd_embedment": "",
                          "end_fnd_diameter": "",
                          "end_fnd_depth": ""})


class EffectiveConfig(unittest.TestCase):
    SNAP = {"spacing_mm": 2000.0, "endpoints": True,
            "rotation_deg": 0.0, "post": "", "foundation": "",
            "same_ends": True, "end_post": "", "end_foundation": "",
            "line_style": "", "priority": 99,
            "end_priority": False, "panel": "",
            "panel_width_param": "",
                          "easting_param": "EASTINGS",
                          "northing_param": "NORTHINGS",
                          "terrain_mode": "auto",
                          "terrains": [],
                          "same_end_posts": True,
                          "same_end_foundations": True,
                          "post_col_size": "",
                          "post_fnd_depth": "",
                          "post_height": "",
                          "end_post_col_size": "",
                          "end_post_fnd_depth": "",
                          "end_post_height": "",
                          "fnd_embedment": "",
                          "fnd_diameter": "",
                          "fnd_depth": "",
                          "end_fnd_embedment": "",
                          "end_fnd_diameter": "",
                          "end_fnd_depth": ""}

    def test_current_config_wins(self):
        s = {}
        F.upsert_config(s, "Gate", 3000, False, 90, "Pad : 600",
                        "Post : 100x100")
        eff = F.effective_config(s, "Gate", self.SNAP)
        self.assertEqual(eff, {"spacing_mm": 3000.0,
                               "endpoints": False,
                               "rotation_deg": 90.0,
                               "post": "Post : 100x100",
                               "foundation": "Pad : 600",
                               "same_ends": True, "end_post": "",
                               "end_foundation": "",
                               "line_style": "",
                               "priority": 99,
                               "end_priority": False, "panel": "",
                               "panel_width_param": "",
                          "easting_param": "EASTINGS",
                          "northing_param": "NORTHINGS",
                          "terrain_mode": "auto",
                          "terrains": [],
                          "same_end_posts": True,
                          "same_end_foundations": True,
                          "post_col_size": "",
                          "post_fnd_depth": "",
                          "post_height": "",
                          "end_post_col_size": "",
                          "end_post_fnd_depth": "",
                          "end_post_height": "",
                          "fnd_embedment": "",
                          "fnd_diameter": "",
                          "fnd_depth": "",
                          "end_fnd_embedment": "",
                          "end_fnd_diameter": "",
                          "end_fnd_depth": ""})

    def test_missing_config_falls_back_to_snapshot(self):
        eff = F.effective_config({}, "Deleted", self.SNAP)
        self.assertEqual(eff, self.SNAP)

    def test_old_record_without_rotation(self):
        eff = F.effective_config({}, None,
                                 {"spacing_mm": 1500.0,
                                  "endpoints": False})
        self.assertEqual(eff, {"spacing_mm": 1500.0,
                               "endpoints": False,
                               "rotation_deg": 0.0, "post": "",
                               "foundation": "", "same_ends": True,
                               "end_post": "",
                               "end_foundation": "",
                               "line_style": "",
                               "priority": 99,
                               "end_priority": False, "panel": "",
                               "panel_width_param": "",
                          "easting_param": "EASTINGS",
                          "northing_param": "NORTHINGS",
                          "terrain_mode": "auto",
                          "terrains": [],
                          "same_end_posts": True,
                          "same_end_foundations": True,
                          "post_col_size": "",
                          "post_fnd_depth": "",
                          "post_height": "",
                          "end_post_col_size": "",
                          "end_post_fnd_depth": "",
                          "end_post_height": "",
                          "fnd_embedment": "",
                          "fnd_diameter": "",
                          "fnd_depth": "",
                          "end_fnd_embedment": "",
                          "end_fnd_diameter": "",
                          "end_fnd_depth": ""})

    def test_snapshot_family_becomes_the_post(self):
        # records from before posts joined configs carry 'family'
        eff = F.effective_config({}, None,
                                 {"spacing_mm": 1500.0,
                                  "endpoints": True,
                                  "family": "Bollard : 150"})
        self.assertEqual(eff["post"], "Bollard : 150")

    def test_config_without_post_key_keeps_record_family(self):
        # a config saved BEFORE posts joined configs has no 'post'
        # key - the record's placed family stays in charge
        s = {F.SETTINGS_CONFIGS: {"Gate": {"spacing_mm": 3000,
                                           "endpoints": True}}}
        eff = F.effective_config(s, "Gate",
                                 {"family": "Bollard : 150"})
        self.assertEqual(eff["post"], "Bollard : 150")
        self.assertEqual(eff["spacing_mm"], 3000.0)

    def test_config_with_explicit_none_post_wins(self):
        s = {}
        F.upsert_config(s, "Gate", 3000, True, 0, "Pad : 600", "")
        eff = F.effective_config(s, "Gate",
                                 {"family": "Bollard : 150"})
        self.assertEqual(eff["post"], "")


class EndFamilies(unittest.TestCase):
    def test_same_ends_mirror_the_main_pair(self):
        cfg = {"post": "P", "foundation": "F", "same_ends": True,
               "end_post": "EP", "end_foundation": "EF"}
        self.assertEqual(F.end_families(cfg), ("P", "F"))

    def test_dedicated_ends(self):
        cfg = {"post": "P", "foundation": "F", "same_ends": False,
               "end_post": "EP", "end_foundation": "EF"}
        self.assertEqual(F.end_families(cfg), ("EP", "EF"))

    def test_old_record_defaults_to_same(self):
        self.assertEqual(F.end_families({"post": "P"}), ("P", ""))

    def test_places_something(self):
        self.assertTrue(F.places_something({"post": "P"}))
        self.assertTrue(F.places_something({"foundation": "F"}))
        # nothing in between, ends only - valid when endpoints on
        ends_only = {"post": "", "foundation": "",
                     "endpoints": True, "same_ends": False,
                     "end_post": "EP", "end_foundation": ""}
        self.assertTrue(F.places_something(ends_only))
        ends_only["endpoints"] = False
        self.assertFalse(F.places_something(ends_only))
        self.assertFalse(F.places_something(
            {"post": "", "foundation": "", "endpoints": True,
             "same_ends": True, "end_post": "EP",
             "end_foundation": "EF"}))


class NetworkMaths(unittest.TestCase):
    def test_edge_stations_full_bay_from_the_corner(self):
        # no touching in-betweens: first post one SPACING from the
        # corner, run stops clear of the far circle
        sts = F.edge_stations(20.0, 5.0, 0.0, 2.5)
        self.assertEqual(sts, [5.0, 10.0, 15.0])

    def test_edge_stations_spacing_counts_from_the_double(self):
        # a double post at 2.5 anchors the run - next at 7.5
        sts = F.edge_stations(20.0, 5.0, 2.5, 2.5)
        self.assertEqual(sts, [7.5, 12.5, 17.5])

    def test_edge_stations_last_bay_shortens(self):
        # 19 long, far clearance 2: last post at 15, last bay 2 -
        # shorter than the spacing, never doubled up
        sts = F.edge_stations(19.0, 5.0, 0.0, 2.0)
        self.assertEqual(sts, [5.0, 10.0, 15.0])

    def test_edge_stations_unknown_circles_keep_one_spacing(self):
        sts = F.edge_stations(20.0, 5.0, 0.0, None)
        self.assertEqual(sts, [5.0, 10.0, 15.0])

    def test_edge_stations_bay_never_exceeds_spacing(self):
        # 10 at spacing 3: full spacings + ONE short extra - the
        # old clear-one-spacing fallback made the last bay 4 here
        sts = F.edge_stations(10.0, 3.0, 0.0, None)
        self.assertEqual(sts, [3.0, 6.0, 9.0])
        marks = [0.0] + sts + [10.0]
        self.assertTrue(all(
            marks[i + 1] - marks[i] <= 3.0 + 1e-9
            for i in range(len(marks) - 1)))

    def test_edge_stations_clash_shifts_not_widens(self):
        # extra bay (1.0) INSIDE the clearance (1.5): the last post
        # shifts back to the clearance, the bay before it shortens -
        # nothing exceeds the spacing
        sts = F.edge_stations(10.0, 3.0, 0.0, 1.5)
        self.assertEqual(sts, [3.0, 6.0, 8.5])
        marks = [0.0] + sts + [10.0]
        self.assertTrue(all(
            marks[i + 1] - marks[i] <= 3.0 + 1e-9
            for i in range(len(marks) - 1)))

    def test_edge_stations_no_room_drops_the_post(self):
        # clearance bigger than the spacing: the shifted post would
        # land before its neighbour - dropped, the final gap is the
        # geometry's to keep
        sts = F.edge_stations(9.0, 3.0, 0.0, 6.5)
        self.assertEqual(sts, [3.0])

    def test_edge_stations_too_short(self):
        self.assertEqual(F.edge_stations(4.0, 5.0, 0.0, 2.5), [])
        self.assertEqual(F.edge_stations(20.0, 0.0, 0.0, 2.5), [])

    def test_cluster_nodes(self):
        pts = [(0.0, 0.0), (10.0, 0.0), (10.005, 0.0), (0.0, 0.002),
               (5.0, 5.0)]
        centers, idx = F.cluster_nodes(pts, 0.01)
        self.assertEqual(len(centers), 3)
        self.assertEqual(idx, [0, 1, 1, 0, 2])

    def test_config_for_style(self):
        cfgs = {"Std": {"line_style": "FENCE - STANDARD"},
                "Imp": {"line_style": "FENCE - IMPACT RATED"},
                "Plain": {"line_style": ""}}
        self.assertEqual(
            F.config_for_style(cfgs, "FENCE - IMPACT RATED")[0],
            "Imp")
        self.assertIsNone(F.config_for_style(cfgs, "OTHER"))
        self.assertIsNone(F.config_for_style(cfgs, ""))

    def test_pick_priority_smallest_wins(self):
        named = [("Std", {"priority": 5}), ("Imp", {"priority": 1}),
                 ("Mid", {"priority": 3})]
        self.assertEqual(F.pick_priority(named)[0], "Imp")

    def test_renumber_from_list_order(self):
        s = {}
        F.upsert_config(s, "Standard", 2000, True)
        F.upsert_config(s, "Impact", 2000, True)
        F.upsert_config(s, "Gate", 2000, True)
        F.renumber_priorities(s, ["Impact", "Gate", "Standard"])
        cfgs = F.get_configs(s)
        self.assertEqual(cfgs["Impact"]["priority"], 1)
        self.assertEqual(cfgs["Gate"]["priority"], 2)
        self.assertEqual(cfgs["Standard"]["priority"], 3)
        # the seeded 'Default' keeps 99 and stays at the bottom
        self.assertEqual(F.priority_order(cfgs),
                         ["Impact", "Gate", "Standard", "Default"])

    def test_priority_order_ties_break_on_name(self):
        cfgs = {"b": {"priority": 99}, "A": {"priority": 99},
                "top": {"priority": 1}}
        self.assertEqual(F.priority_order(cfgs), ["top", "A", "b"])

    def test_pick_priority_tie_is_stable_by_name(self):
        named = [("B", {"priority": 2}), ("A", {"priority": 2})]
        self.assertEqual(F.pick_priority(named)[0], "A")

    def test_network_fence_label(self):
        lbl = F.fence_label({"kind": "network", "id": 4,
                             "lines": [1, 2, 3],
                             "instances": [1] * 40})
        self.assertIn("network 4", lbl)
        self.assertIn("3 line(s)", lbl)
        self.assertIn("40 post(s)", lbl)


class Intersections(unittest.TestCase):
    def test_segments_crossing(self):
        t, u = F.seg_intersect((0, 0), (10, 0), (5, -5), (5, 5))
        self.assertAlmostEqual(t, 0.5)
        self.assertAlmostEqual(u, 0.5)

    def test_segments_touching_at_endpoint(self):
        t, u = F.seg_intersect((0, 0), (10, 0), (10, 0), (10, 8))
        self.assertAlmostEqual(t, 1.0)
        self.assertAlmostEqual(u, 0.0)

    def test_segments_parallel_or_missing(self):
        self.assertIsNone(F.seg_intersect((0, 0), (10, 0),
                                          (0, 1), (10, 1)))
        self.assertIsNone(F.seg_intersect((0, 0), (10, 0),
                                          (20, -5), (20, 5)))

    def test_poly_crossing_stations(self):
        pa = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0)]
        pb = [(4.0, -3.0, 0.0), (4.0, 7.0, 0.0)]
        hits = F.poly_intersections(pa, pb)
        self.assertEqual(len(hits), 1)
        da, db, x, y = hits[0]
        self.assertAlmostEqual(da, 4.0)
        self.assertAlmostEqual(db, 3.0)
        self.assertAlmostEqual(x, 4.0)
        self.assertAlmostEqual(y, 0.0)

    def test_poly_crossing_through_a_kink(self):
        # crossing at a shared vertex of two segments reports ONCE
        pa = [(0.0, 0.0, 0.0), (5.0, 0.0, 0.0), (10.0, 0.0, 0.0)]
        pb = [(5.0, -5.0, 0.0), (5.0, 5.0, 0.0)]
        hits = F.poly_intersections(pa, pb)
        self.assertEqual(len(hits), 1)
        self.assertAlmostEqual(hits[0][0], 5.0)

    def test_panel_bays(self):
        # bays between consecutive posts: centre + width; the tiny
        # bay at a touching double post gets NO panel
        bays = F.panel_bays([0.0, 2.0, 7.0, 12.0, 12.05], 0.5)
        self.assertEqual(len(bays), 3)
        self.assertEqual(bays[0], (1.0, 2.0))
        self.assertEqual(bays[1], (4.5, 5.0))
        self.assertEqual(bays[2], (9.5, 5.0))
        self.assertEqual(F.panel_bays([3.0], 0.5), [])

    def test_panel_bays_skip_corner_to_double(self):
        # the corner-post-to-DOUBLE-post gap is foundation
        # clearance, not a fence bay - a skip span drops its panel
        # even when it is wider than min_len
        sts = [0.0, 2.0, 12.0, 22.0, 27.0]
        bays = F.panel_bays(sts, 0.5, skip=[(0.0, 2.0)])
        self.assertEqual(bays, [(7.0, 10.0), (17.0, 10.0),
                                (24.5, 5.0)])
        # skip at the FAR end of a line works the same way
        bays = F.panel_bays(sts, 0.5, skip=[(22.0, 27.0)])
        self.assertEqual(bays, [(1.0, 2.0), (7.0, 10.0),
                                (17.0, 10.0)])
        # no skip spans: unchanged behaviour
        self.assertEqual(F.panel_bays(sts, 0.5, skip=[]),
                         F.panel_bays(sts, 0.5))

    def test_bbox2d_and_overlap(self):
        # the AUTO-terrain relevance test: plan bounding boxes
        a = F.bbox2d([[(0.0, 0.0, 0.0), (10.0, 5.0, 2.0)]])
        self.assertEqual(a, (0.0, 0.0, 10.0, 5.0))
        self.assertEqual(
            F.bbox2d([[(1.0, 1.0, 0.0)], [(-2.0, 4.0, 0.0)]]),
            (-2.0, 1.0, 1.0, 4.0))
        self.assertIsNone(F.bbox2d([]))
        self.assertIsNone(F.bbox2d([[]]))
        b = (12.0, 0.0, 20.0, 5.0)     # 2 to the right of a
        self.assertFalse(F.boxes_overlap_2d(a, b))
        self.assertTrue(F.boxes_overlap_2d(a, b, margin=2.0))
        self.assertTrue(F.boxes_overlap_2d(a, (5.0, 2.0, 6.0, 3.0)))
        self.assertFalse(F.boxes_overlap_2d(a, None))
        self.assertFalse(F.boxes_overlap_2d(None, b))

    def test_terrain_config_normalised(self):
        s = {}
        F.upsert_config(s, "x", 1000, True,
                        terrain_mode="AUTO", terrains=None)
        self.assertEqual(F.get_configs(s)["x"]["terrain_mode"],
                         F.TERRAIN_AUTO)
        F.upsert_config(s, "x", 1000, True, terrain_mode="named",
                        terrains=["Topo A", " Topo A ", "", "Topo B"])
        cfg = F.get_configs(s)["x"]
        self.assertEqual(cfg["terrain_mode"], F.TERRAIN_NAMED)
        self.assertEqual(cfg["terrains"], ["Topo A", "Topo B"])
        # AUTO is the default: junk normalises to it, an explicit
        # 'pick' is honoured
        F.upsert_config(s, "x", 1000, True, terrain_mode="bogus",
                        terrains="not-a-list")
        cfg = F.get_configs(s)["x"]
        self.assertEqual(cfg["terrain_mode"], F.TERRAIN_AUTO)
        self.assertEqual(cfg["terrains"], [])
        F.upsert_config(s, "x", 1000, True, terrain_mode="pick")
        self.assertEqual(F.get_configs(s)["x"]["terrain_mode"],
                         F.TERRAIN_PICK)

    def test_network_marks_single_line(self):
        # one line, nodes at both ends, two in-between posts:
        # numbered from the start
        edges = {0: {"poly": [(0.0, 0.0, 0.0), (9.0, 0.0, 0.0)],
                     "posts": [(0.0, "node", 0),
                               (3.0, "post", (0, 3.0)),
                               (6.0, "post", (0, 6.0)),
                               (9.0, "node", 1)]}}
        m = F.network_marks(edges, [(0.0, 0.0), (9.0, 0.0)], 0)
        self.assertEqual(m[("node", 0)], "1")
        self.assertEqual(m[("post", (0, 3.0))], "2")
        self.assertEqual(m[("post", (0, 6.0))], "3")
        self.assertEqual(m[("node", 1)], "4")

    def test_network_marks_ccw_loop_reversed(self):
        # a square drawn COUNTER-clockwise: the walk re-runs the
        # other way so the numbers go CLOCKWISE from the start
        xy = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
        pl = lambda a, b: [(xy[a][0], xy[a][1], 0.0),
                           (xy[b][0], xy[b][1], 0.0)]
        edges = {
            0: {"poly": pl(0, 1), "posts": [(0.0, "node", 0),
                                            (2.0, "post", (0, 2.0)),
                                            (4.0, "node", 1)]},
            1: {"poly": pl(1, 2), "posts": [(0.0, "node", 1),
                                            (4.0, "node", 2)]},
            2: {"poly": pl(2, 3), "posts": [(0.0, "node", 2),
                                            (4.0, "node", 3)]},
            3: {"poly": pl(3, 0), "posts": [(0.0, "node", 3),
                                            (4.0, "node", 0)]},
        }
        m = F.network_marks(edges, xy, 0)
        self.assertEqual(m[("node", 0)], "1")
        # clockwise from (0,0): up the left side first
        self.assertEqual(m[("node", 3)], "2")
        self.assertEqual(m[("node", 2)], "3")
        self.assertEqual(m[("node", 1)], "4")
        # the in-between post on edge 0 is numbered walking 1 -> 0
        self.assertEqual(m[("post", (0, 2.0))], "5")

    def test_network_marks_branch_naming(self):
        # a straight chain with a spur at its middle corner: the
        # spur numbers off the corner's mark with a letter
        xy = [(0.0, 0.0), (6.0, 0.0), (12.0, 0.0), (6.0, 5.0)]
        edges = {
            0: {"poly": [(0.0, 0.0, 0.0), (12.0, 0.0, 0.0)],
                "posts": [(0.0, "node", 0),
                          (3.0, "post", (0, 3.0)),
                          (6.0, "node", 1),
                          (9.0, "post", (0, 9.0)),
                          (12.0, "node", 2)]},
            1: {"poly": [(6.0, 0.0, 0.0), (6.0, 5.0, 0.0)],
                "posts": [(0.0, "node", 1),
                          (1.0, "double", (1, 1)),
                          (3.0, "post", (1, 3.0)),
                          (5.0, "node", 3)]},
        }
        m = F.network_marks(edges, xy, 0)
        self.assertEqual(m[("node", 0)], "1")
        self.assertEqual(m[("post", (0, 3.0))], "2")
        self.assertEqual(m[("node", 1)], "3")
        self.assertEqual(m[("post", (0, 9.0))], "4")
        self.assertEqual(m[("node", 2)], "5")
        # the spur off corner 3: its OWN numbering, double first
        self.assertEqual(m[("double", (1, 1))], "3A1")
        self.assertEqual(m[("post", (1, 3.0))], "3A2")
        self.assertEqual(m[("node", 3)], "3A3")

    def test_network_marks_prefix_and_sweep(self):
        # the GLOBAL prefix lands in front of every number, branch
        # anchors carry it through; a post the walks cannot reach
        # still gets a (prefixed) fallback mark
        edges = {0: {"poly": [(0.0, 0.0, 0.0), (6.0, 0.0, 0.0)],
                     "posts": [(0.0, "node", 0),
                               (3.0, "post", (0, 3.0)),
                               (6.0, "node", 1)]},
                 # degenerate: a lone edge with a single node and a
                 # post - no segment, unreachable by any walk
                 1: {"poly": [(20.0, 0.0, 0.0), (20.0, 1.0, 0.0)],
                     "posts": [(0.5, "post", (1, 0.5))]}}
        m = F.network_marks(edges, [(0.0, 0.0), (6.0, 0.0)], 0,
                            prefix="FF")
        self.assertEqual(m[("node", 0)], "FF1")
        self.assertEqual(m[("post", (0, 3.0))], "FF2")
        self.assertEqual(m[("node", 1)], "FF3")
        self.assertEqual(m[("post", (1, 0.5))], "FFX1")

    def test_mark_settings(self):
        self.assertEqual(F.mark_settings({}), (False, ""))
        s = {F.SETTINGS_MARK: True, F.SETTINGS_MARK_PREFIX: " FF "}
        self.assertEqual(F.mark_settings(s), (True, "FF"))

    def test_toc_settings_and_eval(self):
        self.assertEqual(F.toc_settings({}), (False, "TOC", ""))
        s = {F.SETTINGS_TOC: True, F.SETTINGS_TOC_PARAM: " Top ",
             F.SETTINGS_TOC_FORMULA: "z + 50"}
        self.assertEqual(F.toc_settings(s), (True, "Top", "z + 50"))
        # empty equation = the ground level itself
        self.assertEqual(F.eval_toc("", 855.5), 855.5)
        self.assertEqual(F.eval_toc("z + 50", 855.5), 905.5)
        self.assertEqual(F.eval_toc("max(z, 900)", 855.5), 900.0)
        self.assertEqual(F.eval_toc("round(z / 10) * 10", 856.0),
                         860.0)
        # e / n (metres) available too
        self.assertEqual(F.eval_toc("z + e + n", 100.0, 2.0, 3.0),
                         105.0)
        # a typo raises with the reason; builtins stay locked out
        self.assertRaises(ValueError, F.eval_toc, "z +", 100.0)
        self.assertRaises(ValueError, F.eval_toc, "q * 2", 100.0)
        self.assertRaises(ValueError, F.eval_toc,
                          "__import__('os')", 100.0)

    def test_export_and_merge_settings(self):
        src = {}
        F.upsert_config(src, "Impact 3.0m", 2750, True)
        src[F.SETTINGS_MARK] = True
        src[F.SETTINGS_MARK_PREFIX] = "FF"
        src["unrelated"] = "stays home"
        data = F.export_settings(src)
        self.assertIn(F.SETTINGS_CONFIGS, data)
        self.assertNotIn("unrelated", data)
        self.assertEqual(data[F.SETTINGS_MARK_PREFIX], "FF")
        # merge into a profile that already has a config of the
        # SAME name (file wins) and one of its own (kept)
        dst = {}
        F.upsert_config(dst, "Impact 3.0m", 9999, False)
        F.upsert_config(dst, "Mine", 1500, True)
        added, updated, others = F.merge_settings(dst, data)
        self.assertEqual(updated, 2)    # Impact + auto-seeded Default
        self.assertEqual(added, 0)
        cfgs = F.get_configs(dst)
        self.assertEqual(cfgs["Impact 3.0m"]["spacing_mm"], 2750.0)
        self.assertIn("Mine", cfgs)
        self.assertEqual(dst[F.SETTINGS_MARK], True)
        self.assertEqual(dst[F.SETTINGS_MARK_PREFIX], "FF")
        # not an export -> refused
        self.assertRaises(ValueError, F.merge_settings, {},
                          {"random": 1})
        self.assertRaises(ValueError, F.merge_settings, {}, "no")

    def test_end_families_split_flags(self):
        # the ends can mix: same post on a DIFFERENT foundation,
        # and vice versa - each behind its own tick
        cfg = {"post": "P", "foundation": "F",
               "end_post": "EP", "end_foundation": "EF",
               "same_end_posts": True,
               "same_end_foundations": False}
        self.assertEqual(F.end_families(cfg), ("P", "EF"))
        cfg["same_end_posts"] = False
        cfg["same_end_foundations"] = True
        self.assertEqual(F.end_families(cfg), ("EP", "F"))
        # legacy records only carry same_ends - it stands in for both
        self.assertEqual(F.end_families(
            {"post": "P", "foundation": "F", "end_post": "EP",
             "end_foundation": "EF", "same_ends": False}),
            ("EP", "EF"))
        self.assertEqual(F.end_families(
            {"post": "P", "foundation": "F", "end_post": "EP",
             "end_foundation": "EF", "same_ends": True}),
            ("P", "F"))

    def test_parse_assignments_and_eval_assign(self):
        # 'Parameter = equation' lines: blanks and #comments skip
        a = F.parse_assignments(
            "Foundation Depth = 750\n\n# a comment\n"
            "Height = 3200\nLabel = 'SHS 40x40'")
        self.assertEqual(a, [("Foundation Depth", "750"),
                             ("Height", "3200"),
                             ("Label", "'SHS 40x40'")])
        self.assertEqual(F.parse_assignments(""), [])
        self.assertRaises(ValueError, F.parse_assignments,
                          "no equals sign here")
        self.assertRaises(ValueError, F.parse_assignments, "= 5")
        # numbers evaluate like TOC, TEXT comes through quoted
        self.assertEqual(F.eval_assign("750", 0.0), 750.0)
        self.assertEqual(F.eval_assign("z + 10", 100.0), 110.0)
        self.assertEqual(F.eval_assign("'SHS 40x40'", 0.0),
                         "SHS 40x40")
        p = {"Height": 3200.0}
        self.assertEqual(F.eval_assign("Height / 2", 0.0, params=p),
                         1600.0)
        self.assertRaises(ValueError, F.eval_assign, "", 0.0)

    def test_eval_toc_parameter_names(self):
        # the equation may use the foundation's OWN parameters by
        # name - spaces and all - or in [brackets]
        p = {"Height Offset From Level": 855.5, "Embedment": 300.0,
             "Height": 9999.0}
        self.assertEqual(F.eval_toc(
            "Height Offset From Level - Embedment", 0.0, params=p),
            555.5)
        self.assertEqual(F.eval_toc(
            "[Height Offset From Level] + 10", 0.0, params=p),
            865.5)
        # longest name wins - 'Height' inside the long name is NOT
        # matched on its own
        self.assertEqual(F.eval_toc(
            "Height Offset From Level", 0.0, params=p), 855.5)
        # mixes with z and functions
        self.assertEqual(F.eval_toc("max(z, Embedment) + 1", 100.0,
                                    params=p), 301.0)
        # unknown names raise with a pointer at the spelling
        self.assertRaises(ValueError, F.eval_toc, "[Nope]", 0.0,
                          0.0, 0.0, p)
        self.assertRaises(ValueError, F.eval_toc, "Embedmet + 1",
                          0.0, 0.0, 0.0, p)

    def test_network_marks_longest_chain_absorbs_one_spur(self):
        # no circle: the LONGEST run is the chain - one spur joins
        # it, the other hangs off as a branch. A LONGER spur wins
        # the chain regardless of group.
        xy = [(0.0, 0.0), (6.0, 0.0), (6.0, 5.0), (6.0, -9.0)]
        edges = {
            0: {"poly": [(0.0, 0.0, 0.0), (6.0, 0.0, 0.0)],
                "posts": [(0.0, "node", 0), (6.0, "node", 1)],
                "group": "main"},
            1: {"poly": [(6.0, 0.0, 0.0), (6.0, 5.0, 0.0)],
                "posts": [(0.0, "node", 1), (5.0, "node", 2)],
                "group": "spur"},
            2: {"poly": [(6.0, 0.0, 0.0), (6.0, -9.0, 0.0)],
                "posts": [(0.0, "node", 1), (9.0, "node", 3)],
                "group": "spur"},
        }
        m = F.network_marks(edges, xy, 0)
        self.assertEqual(m[("node", 0)], "1")
        self.assertEqual(m[("node", 1)], "2")
        # the 9-long south spur continues the chain; the 5-long
        # north spur branches off corner 2
        self.assertEqual(m[("node", 3)], "3")
        self.assertEqual(m[("node", 2)], "2A1")

    def test_network_marks_full_circle_beats_styles(self):
        # a loop of MIXED styles with a spur: the FULL CIRCLE is
        # always the main chain (clockwise), the spur branches
        xy = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0),
              (8.0, 0.0)]
        pl = lambda a, b: [(xy[a][0], xy[a][1], 0.0),
                           (xy[b][0], xy[b][1], 0.0)]
        edges = {
            0: {"poly": pl(0, 1), "posts": [(0.0, "node", 0),
                                            (4.0, "node", 1)],
                "group": "impact"},
            1: {"poly": pl(1, 2), "posts": [(0.0, "node", 1),
                                            (4.0, "node", 2)],
                "group": "standard"},
            2: {"poly": pl(2, 3), "posts": [(0.0, "node", 2),
                                            (4.0, "node", 3)],
                "group": "standard"},
            3: {"poly": pl(3, 0), "posts": [(0.0, "node", 3),
                                            (4.0, "node", 0)],
                "group": "standard"},
            # spur off node 1, pointing away from the loop
            4: {"poly": pl(1, 4), "posts": [(0.0, "node", 1),
                                            (4.0, "node", 4)],
                "group": "palladine"},
        }
        m = F.network_marks(edges, xy, 0)
        # drawn counter-clockwise -> numbered the OTHER way round:
        # the whole loop is the chain despite the style changes
        self.assertEqual(m[("node", 0)], "1")
        self.assertEqual(m[("node", 3)], "2")
        self.assertEqual(m[("node", 2)], "3")
        self.assertEqual(m[("node", 1)], "4")
        # the spur branches off corner 4
        self.assertEqual(m[("node", 4)], "4A1")

    def test_polys_touch(self):
        a = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0)]
        crossing = [(5.0, -5.0, 0.0), (5.0, 5.0, 0.0)]
        touching = [(10.0, 0.01, 0.0), (10.0, 8.0, 0.0)]
        far = [(0.0, 5.0, 0.0), (10.0, 5.0, 0.0)]
        self.assertTrue(F.polys_touch(a, crossing, 0.1))
        self.assertTrue(F.polys_touch(a, touching, 0.1))
        self.assertFalse(F.polys_touch(a, far, 0.1))

    def test_project_to_poly(self):
        poly = [(0.0, 0.0, 0.0), (10.0, 0.0, 0.0)]
        d, away, px, py = F.project_to_poly(poly, 6.0, 2.0)
        self.assertAlmostEqual(d, 6.0)
        self.assertAlmostEqual(away, 2.0)
        self.assertAlmostEqual(px, 6.0)
        self.assertAlmostEqual(py, 0.0)
        # beyond the end clamps to the endpoint
        d2, away2, _px, _py = F.project_to_poly(poly, 14.0, 3.0)
        self.assertAlmostEqual(d2, 10.0)
        self.assertAlmostEqual(away2, 5.0)


class Registry(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    @staticmethod
    def _rec(**kw):
        rec = {"line_uid": "L1", "terrain_uid": "T1",
               "family": "Post : 100mm", "spacing_mm": 2000.0,
               "endpoints": True, "justify": "start",
               "config": "Default",
               "instances": [{"uid": "a", "station_ft": 0.0,
                              "angle": 0.0}],
               "updated": "2026-08-10T00:00:00"}
        rec.update(kw)
        return rec

    def test_missing_registry_is_empty(self):
        self.assertEqual(F.load_fences(self.base), {"fences": []})

    def test_add_assigns_sequential_ids(self):
        self.assertEqual(F.add_fence(self.base, self._rec()), 1)
        self.assertEqual(F.add_fence(self.base, self._rec()), 2)
        data = F.load_fences(self.base)
        self.assertEqual([r["id"] for r in data["fences"]], [1, 2])

    def test_update_replaces_by_id(self):
        F.add_fence(self.base, self._rec())
        rec = self._rec(id=1, spacing_mm=999.0)
        F.update_fence(self.base, rec)
        data = F.load_fences(self.base)
        self.assertEqual(len(data["fences"]), 1)
        self.assertEqual(data["fences"][0]["spacing_mm"], 999.0)

    def test_drop(self):
        F.add_fence(self.base, self._rec())
        F.add_fence(self.base, self._rec())
        F.drop_fence(self.base, 1)
        data = F.load_fences(self.base)
        self.assertEqual([r["id"] for r in data["fences"]], [2])

    def test_corrupt_file_never_raises(self):
        with open(os.path.join(self.base, F.REGISTRY), "w") as f:
            f.write("{nope")
        self.assertEqual(F.load_fences(self.base), {"fences": []})

    def test_file_is_ascii_json(self):
        F.add_fence(self.base, self._rec(family=u"Clôture : bois"))
        with open(os.path.join(self.base, F.REGISTRY), "rb") as f:
            raw = f.read()
        self.assertTrue(all(b < 128 for b in bytearray(raw)))
        data = F.load_fences(self.base)
        self.assertEqual(data["fences"][0]["family"],
                         u"Clôture : bois")


class PairStations(unittest.TestCase):
    INST = [{"uid": "b", "station_ft": 5.0, "angle": 0.1},
            {"uid": "a", "station_ft": 0.0, "angle": 0.0},
            {"uid": "c", "station_ft": 10.0, "angle": 0.2}]

    def test_pairs_in_station_order(self):
        pairs = F.pair_stations(self.INST, [12.0, 0.0, 6.0])
        self.assertEqual([(p[0]["uid"], p[1]) for p in pairs],
                         [("a", 0.0), ("b", 6.0), ("c", 12.0)])

    def test_count_mismatch_means_rebuild(self):
        self.assertIsNone(F.pair_stations(self.INST, [0.0, 5.0]))
        self.assertIsNone(F.pair_stations([], []))

    def test_label_mentions_count(self):
        lbl = F.fence_label({"id": 3, "family": "Post : 100",
                             "spacing_mm": 2000.0,
                             "justify": "centre",
                             "instances": self.INST})
        self.assertIn("Fence 3", lbl)
        self.assertIn("3 post(s)", lbl)


if __name__ == "__main__":
    unittest.main(verbosity=2)
