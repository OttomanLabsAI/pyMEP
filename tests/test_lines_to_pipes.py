#!/usr/bin/env python3
"""Unit tests for the Lines to Pipes network solver (stdlib only, no
Revit).

The pure half of lib/pymep_lines_to_pipes.py: segment intersection,
duplicate-line dropping, the junction graph with overshoot trimming,
Dijkstra depths and the run/tee/elbow plan. Extracted by AST (the
module imports the Revit API and cannot import under CPython).

The geometry vocabulary mirrors the user's real HEL18 drawing: laterals
CROSS their main with a little overshoot, duplicates are drawn on top
of each other, and the odd line touches nothing at all.

Run:  python3 tests/test_lines_to_pipes.py
"""

import ast
import io
import math
import os
import unittest

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "pyMEP.extension", "lib")
SRC_PATH = os.path.join(LIB, "pymep_lines_to_pipes.py")

import json
ns = {"math": math, "json": json, "os": os}
_src = io.open(SRC_PATH, encoding="utf-8").read()
for node in ast.parse(_src).body:
    if isinstance(node, ast.FunctionDef):
        exec(compile(ast.get_source_segment(_src, node), SRC_PATH, "exec"),
             ns)
    elif isinstance(node, ast.Assign):
        try:
            exec(compile(ast.get_source_segment(_src, node), SRC_PATH,
                         "exec"), ns)
        except Exception:
            pass

_intersect = ns["_intersect"]
parse_style_slope = ns["parse_style_slope"]
normalize_slopes = ns["normalize_slopes"]
fit_plan = ns["fit_plan"]
drop_duplicates = ns["drop_duplicates"]
build_network = ns["build_network"]
assign_depths = ns["assign_depths"]
nearest_node = ns["nearest_node"]
solve = ns["solve"]
node_z_m = ns["node_z_m"]


# A small network in mm, drawn the way the HEL18 model draws:
#   trunk    (0,0) -> (30000,0)
#   lateral  (10000,-800) -> (10000,12000)   crosses the trunk, 800 overshoot
#   corner   (30000,0) -> (30000,20000)      end-to-end elbow with the trunk
#   twin     (5000,0) -> (18000,0)           drawn ON the trunk (duplicate)
#   loner    (50000,50000) -> (50000,60000)  touches nothing
TRUNK = ((0.0, 0.0), (30000.0, 0.0))
LATERAL = ((10000.0, -800.0), (10000.0, 12000.0))
CORNER = ((30000.0, 0.0), (30000.0, 20000.0))
TWIN = ((5000.0, 0.0), (18000.0, 0.0))
LONER = ((50000.0, 50000.0), (50000.0, 60000.0))
LINES = [TRUNK, LATERAL, CORNER, TWIN, LONER]


def solved(pick=(0.0, 0.0)):
    return solve(list(LINES), pick, 200.0)


class Intersect(unittest.TestCase):

    def test_proper_crossing(self):
        t, u, p = _intersect((0, 0), (10, 0), (5, -5), (5, 5))
        self.assertAlmostEqual(t, 0.5)
        self.assertAlmostEqual(u, 0.5)
        self.assertEqual(p, (5.0, 0.0))

    def test_parallel_is_none(self):
        self.assertIsNone(_intersect((0, 0), (10, 0), (0, 1), (10, 1)))

    def test_disjoint_is_none(self):
        self.assertIsNone(_intersect((0, 0), (10, 0), (20, -5), (20, 5)))


class Duplicates(unittest.TestCase):

    def test_shorter_copy_is_dropped(self):
        kept, dropped = drop_duplicates([TRUNK, TWIN])
        self.assertEqual(kept, [0])
        self.assertEqual(dropped, [(1, 0)])

    def test_perpendicular_lines_are_not_duplicates(self):
        kept, dropped = drop_duplicates([TRUNK, LATERAL])
        self.assertEqual(kept, [0, 1])
        self.assertEqual(dropped, [])


class Network(unittest.TestCase):

    def test_crossing_becomes_one_shared_node(self):
        net = build_network([TRUNK, LATERAL])
        # trunk: end + crossing + end = 3 nodes -> 2 segs; lateral: the
        # 800 overshoot tail is dropped, one seg above the trunk
        self.assertEqual(len(net["segs"]), 3)
        self.assertEqual(len(net["tail_dropped"]), 1)
        li, length = net["tail_dropped"][0]
        self.assertEqual(li, 1)
        self.assertAlmostEqual(length, 800.0, places=6)

    def test_long_sides_survive(self):
        net = build_network([TRUNK, LATERAL])
        lens = sorted(round(s[3]) for s in net["segs"])
        self.assertEqual(lens, [10000, 12000, 20000])

    def test_isolated_line_keeps_its_geometry(self):
        net = build_network([LONER])
        self.assertEqual(len(net["segs"]), 1)
        self.assertEqual(net["tail_dropped"], [])


class Depths(unittest.TestCase):

    def test_distance_accumulates_through_junctions(self):
        net = build_network([TRUNK, LATERAL])
        out = nearest_node(net, (0.0, 0.0))
        d = assign_depths(net, out)
        far = nearest_node(net, (30000.0, 0.0))
        top = nearest_node(net, (10000.0, 12000.0))
        self.assertAlmostEqual(d[far], 30000.0, places=6)
        self.assertAlmostEqual(d[top], 22000.0, places=6)

    def test_unreachable_nodes_are_absent(self):
        net = build_network([TRUNK, LONER])
        out = nearest_node(net, (0.0, 0.0))
        d = assign_depths(net, out)
        lone = nearest_node(net, (50000.0, 50000.0))
        self.assertNotIn(lone, d)


class Solve(unittest.TestCase):

    def test_the_whole_little_network(self):
        sol = solved()
        run_lines = sorted(r["line"] for r in sol["runs"])
        self.assertEqual(run_lines, [0, 1, 2])
        self.assertEqual(len(sol["tees"]), 1)
        tee = sol["tees"][0]
        self.assertEqual(tee["host_line"], 0)
        self.assertEqual(tee["branch_line"], 1)
        self.assertEqual(len(sol["elbows"]), 1)
        self.assertEqual({sol["elbows"][0]["la"], sol["elbows"][0]["lb"]},
                         {0, 2})

    def test_runs_are_oriented_shallow_to_deep(self):
        sol = solved()
        for r in sol["runs"]:
            self.assertLess(sol["depths"][r["a"]], sol["depths"][r["b"]])

    def test_duplicate_and_loner_reported(self):
        sol = solved()
        text = " / ".join(sol["skipped"])
        self.assertIn("drawn twice", text)
        self.assertIn("not piped", text)
        self.assertIn("overshoot", text)

    def test_outfall_at_the_picked_end(self):
        sol = solved(pick=(29990.0, 10.0))
        self.assertAlmostEqual(sol["depths"][sol["outfall_node"]], 0.0)


class InvertMath(unittest.TestCase):

    def test_invert_is_outfall_plus_rise(self):
        # 40 m at 1:200 accumulates a 200 mm rise
        self.assertAlmostEqual(node_z_m(200.0, 10.0), 10.2)

    def test_outfall_sits_at_the_given_invert(self):
        self.assertAlmostEqual(node_z_m(0.0, 3.25), 3.25)


class StyleSlope(unittest.TestCase):

    def test_pipe_one_dash_n_names(self):
        for name, n in (("Pipe 1-8", 8.0), ("Pipe 1-80", 80.0),
                        ("Pipe 1-100", 100.0), ("Pipe 1-300", 300.0)):
            self.assertEqual(parse_style_slope(name), n)

    def test_custom_is_flagged_not_parsed(self):
        self.assertEqual(parse_style_slope("Slope Custom"), "custom")
        self.assertEqual(parse_style_slope("PIPE CUSTOM"), "custom")

    def test_unnumbered_styles_get_none(self):
        for name in ("Pipe", "Thin Lines", "<Lines>", "", None):
            self.assertIsNone(parse_style_slope(name))

    def test_colon_and_spacing_variants(self):
        self.assertEqual(parse_style_slope("Pipe 1 - 60"), 60.0)
        self.assertEqual(parse_style_slope("Pipe 1:150"), 150.0)

    def test_decimal_slope(self):
        self.assertEqual(parse_style_slope("Pipe 1-2.5"), 2.5)


class PerLineSlopes(unittest.TestCase):

    def test_scalar_becomes_uniform(self):
        self.assertEqual(normalize_slopes([TRUNK, LATERAL], 80.0),
                         {0: 80.0, 1: 80.0})

    def test_dict_passes_through_with_default_one(self):
        got = normalize_slopes([TRUNK, LATERAL, CORNER], {0: 80.0, 2: 40.0})
        self.assertEqual(got, {0: 80.0, 1: 1.0, 2: 40.0})

    def test_each_line_rises_at_its_own_slope(self):
        # trunk 1:200, lateral 1:40 - lateral's top end climbs 12 m/40
        # above the junction, the junction 10 m/200 above the outfall
        sol = solve([TRUNK, LATERAL], (0.0, 0.0), {0: 200.0, 1: 40.0})
        top = None
        for i, (x, y) in enumerate(sol["nodes"]):
            if abs(y - 12000.0) < 1.0:
                top = i
        self.assertIsNotNone(top)
        self.assertAlmostEqual(sol["depths"][top],
                               10000.0 / 200.0 + 12000.0 / 40.0, places=6)


class FitPlan(unittest.TestCase):

    def test_network_lands_inside_the_canvas(self):
        scale, ox, oy = fit_plan([TRUNK, LATERAL, CORNER], 560.0, 360.0)
        for a, b in (TRUNK, LATERAL, CORNER):
            for p in (a, b):
                cx = p[0] * scale + ox
                cy = -p[1] * scale + oy
                self.assertGreaterEqual(cx, 15.9)
                self.assertLessEqual(cx, 560.1 - 15.9)
                self.assertGreaterEqual(cy, 15.9)
                self.assertLessEqual(cy, 360.1 - 15.9)

    def test_north_is_up(self):
        scale, ox, oy = fit_plan([TRUNK, CORNER], 400.0, 400.0)
        y0 = -0.0 * scale + oy
        y_top = -20000.0 * scale + oy
        self.assertLess(y_top, y0)


class InvertMarkers(unittest.TestCase):
    """Head-fed solving: Invert Level nodes pin the HIGH ends and the
    pick marks the LOW (outfall) end, which needs no node. The network
    falls from every head toward the pick; merges continue from the
    lower feed; headless branches rise off the mains."""

    def test_marker_is_the_high_point(self):
        # marker at the trunk's west end pins 51324 mm; the pick marks
        # the east end as the outfall - 30 m at 1:200 falls 150 mm
        sol = solve([TRUNK], (30000.0, 0.0), 200.0,
                    sources=[((0.0, 0.0), 51324.0)])
        west, east = None, None
        for i, (x, y) in enumerate(sol["nodes"]):
            if abs(x) < 1.0:
                west = i
            if abs(x - 30000.0) < 1.0:
                east = i
        self.assertAlmostEqual(sol["depths"][west], 51324.0, places=6)
        self.assertAlmostEqual(sol["depths"][east], 51174.0, places=6)
        self.assertEqual(sol["outfall_node"], east)

    def test_two_islands_each_take_their_own_marker(self):
        # pick on the trunk; the loner is an island with its own
        # marker - it keeps the fall-away-from-the-node rule
        sol = solve([TRUNK, LONER], (30000.0, 0.0), 100.0,
                    sources=[((0.0, 0.0), 10000.0),
                             ((50000.0, 50000.0), 20000.0)])
        lone_top = None
        for i, (x, y) in enumerate(sol["nodes"]):
            if abs(y - 60000.0) < 1.0:
                lone_top = i
        self.assertAlmostEqual(sol["depths"][lone_top], 19900.0,
                               places=6)
        # both lines build - the loner is no longer 'not piped'
        self.assertEqual(sorted(r["line"] for r in sol["runs"]), [0, 1])
        self.assertEqual(len(sol["source_nodes"]), 2)

    def test_heads_pin_their_levels_exactly(self):
        # markers at both trunk ends: each stays at ITS typed level -
        # a feed arriving below a pinned head is reported, not fudged
        sol = solve([TRUNK], (0.0, 0.0), 100.0,
                    sources=[((0.0, 0.0), 10000.0),
                             ((30000.0, 0.0), 10100.0)])
        west, east = None, None
        for i, (x, y) in enumerate(sol["nodes"]):
            if abs(x) < 1.0:
                west = i
            if abs(x - 30000.0) < 1.0:
                east = i
        self.assertAlmostEqual(sol["depths"][west], 10000.0, places=6)
        self.assertAlmostEqual(sol["depths"][east], 10100.0, places=6)

    def test_headless_branches_rise_off_the_mains(self):
        # ONE head west, outfall picked east: the trunk falls west ->
        # east, and the lateral / corner (no head above them) RISE
        # away from the network at their grade so they drain into it
        sol = solve([TRUNK, LATERAL, CORNER], (30000.0, 0.0), 100.0,
                    sources=[((0.0, 0.0), 50000.0)])
        idx = {}
        for i, (x, y) in enumerate(sol["nodes"]):
            idx[(round(x), round(y))] = i
        self.assertAlmostEqual(
            sol["depths"][idx[(10000, 0)]], 49900.0, places=6)   # tee
        self.assertAlmostEqual(
            sol["depths"][idx[(30000, 0)]], 49700.0, places=6)   # out
        self.assertAlmostEqual(
            sol["depths"][idx[(10000, 12000)]], 50020.0,
            places=6)                     # lateral rises off the tee
        self.assertAlmostEqual(
            sol["depths"][idx[(30000, 20000)]], 49900.0,
            places=6)                     # corner rises off the out
        # every line builds - nothing 'fed from both ends'
        self.assertEqual(sorted(set(r["line"] for r in sol["runs"])),
                         [0, 1, 2])

    def test_merge_takes_the_lower_feed(self):
        # two heads feed one junction; the run continues from the
        # LOWER feed and the higher feed's last stretch is steeper -
        # it still builds, nothing is flagged
        A = ((0.0, 0.0), (20000.0, 0.0))
        B = ((10000.0, 10000.0), (10000.0, 0.0))
        sol = solve([A, B], (20000.0, 0.0), 100.0,
                    sources=[((0.0, 0.0), 50000.0),
                             ((10000.0, 10000.0), 49000.0)])
        idx = {}
        for i, (x, y) in enumerate(sol["nodes"]):
            idx[(round(x), round(y))] = i
        self.assertAlmostEqual(
            sol["depths"][idx[(10000, 0)]], 48900.0, places=6)
        self.assertAlmostEqual(
            sol["depths"][idx[(20000, 0)]], 48800.0, places=6)
        self.assertAlmostEqual(
            sol["depths"][idx[(0, 0)]], 50000.0, places=6)
        self.assertEqual(sorted(set(r["line"] for r in sol["runs"])),
                         [0, 1])
        self.assertFalse(any("fed from both ends" in s
                             for s in sol["skipped"]))

    def test_three_run_ends_become_a_join(self):
        # two feeds and the outfall run all END at one point - the
        # solver emits a 3-way JOIN with the straight-through pair
        # first, and nothing is 'left unconnected'
        A = ((0.0, 0.0), (10000.0, 0.0))
        B = ((10000.0, 10000.0), (10000.0, 0.0))
        C = ((10000.0, 0.0), (20000.0, 0.0))
        sol = solve([A, B, C], (20000.0, 0.0), 100.0,
                    sources=[((0.0, 0.0), 50000.0),
                             ((10000.0, 10000.0), 49000.0)])
        self.assertEqual(len(sol["joins"]), 1)
        jn = sol["joins"][0]
        lines_in = [r["line"] for r in jn["runs"]]
        self.assertEqual(sorted(lines_in), [0, 1, 2])
        # the collinear pair (A and C) carries straight through
        self.assertEqual(sorted(lines_in[:2]), [0, 2])
        self.assertEqual(lines_in[2], 1)
        self.assertFalse(any("left unconnected" in s
                             for s in sol["skipped"]))

    def test_loop_segment_left_out(self):
        # a closed square has no single flow direction - one segment
        # is dropped with a note, the rest still solves
        L0 = ((0.0, 0.0), (10000.0, 0.0))
        L1 = ((10000.0, 0.0), (10000.0, 10000.0))
        L2 = ((10000.0, 10000.0), (0.0, 10000.0))
        L3 = ((0.0, 10000.0), (0.0, 0.0))
        sol = solve([L0, L1, L2, L3], (0.0, 0.0), 100.0,
                    sources=[((0.0, 10000.0), 50000.0)])
        self.assertTrue(any("LOOP" in s for s in sol["skipped"]))


class AimPick(unittest.TestCase):
    """Node-to-pipe association for Inflow to Lines, run against the
    REAL ray/distance helpers from pymep_connect_fixtures - a stub
    with the wrong shape is how this feature shipped broken twice."""

    @classmethod
    def setUpClass(cls):
        cf_path = os.path.join(LIB, "pymep_connect_fixtures.py")
        src = io.open(cf_path, encoding="utf-8").read()
        cfns = {"math": math, "MIN_LEN_FT": 50.0 / 304.8}
        for node in ast.parse(src).body:
            if isinstance(node, ast.FunctionDef) and node.name in (
                    "ray_hits_main", "plan_dist_to_segment"):
                exec(compile(ast.get_source_segment(src, node), cf_path,
                             "exec"), cfns)
        # staticmethod: a bare function stored on the class would
        # bind as a method and swallow ``self`` as its first argument
        cls.ray = staticmethod(cfns["ray_hits_main"])
        cls.dist = staticmethod(cfns["plan_dist_to_segment"])

    # two parallel mains (in feet): near at y=10, far at y=30
    SEGS = [("near", (0.0, 10.0), (100.0, 10.0)),
            ("far", (0.0, 30.0), (100.0, 30.0))]

    def test_facing_hit_wins_over_nearness(self):
        # node at y=20 between the mains, FACING the far one
        key, how = ns["aim_pick"]((50.0, 20.0), [(0.0, 1.0), (0.0, -1.0)],
                                  self.SEGS, self.ray, self.dist)
        self.assertEqual((key, how), ("far", "aimed"))

    def test_first_direction_with_a_hit_decides(self):
        key, how = ns["aim_pick"]((50.0, 20.0), [(0.0, -1.0), (0.0, 1.0)],
                                  self.SEGS, self.ray, self.dist)
        self.assertEqual((key, how), ("near", "aimed"))

    def test_nearest_hit_wins_within_one_direction(self):
        # aiming up from BELOW both mains: the near one is hit first
        key, how = ns["aim_pick"]((50.0, 0.0), [(0.0, 1.0)],
                                  self.SEGS, self.ray, self.dist)
        self.assertEqual((key, how), ("near", "aimed"))

    def test_no_hit_falls_back_to_plan_nearest(self):
        # aiming along x hits nothing (parallel) -> nearest by distance
        key, how = ns["aim_pick"]((50.0, 12.0), [(1.0, 0.0)],
                                  self.SEGS, self.ray, self.dist)
        self.assertEqual((key, how), ("near", "nearest"))

    def test_real_node_directions_feed_straight_in(self):
        # the exact chain the button runs: node_directions -> aim_pick,
        # no reshaping anywhere ('tuple has no attribute X' shipped
        # THREE times from reshaping guesses)
        import types
        cf_path = os.path.join(LIB, "pymep_connect_fixtures.py")
        src = io.open(cf_path, encoding="utf-8").read()
        nd_ns = {}
        for node in ast.parse(src).body:
            if isinstance(node, ast.FunctionDef) and \
                    node.name == "node_directions":
                exec(compile(ast.get_source_segment(src, node), cf_path,
                             "exec"), nd_ns)
        inst = types.SimpleNamespace(
            FacingOrientation=types.SimpleNamespace(X=0.0, Y=1.0, Z=0.0),
            HandOrientation=types.SimpleNamespace(X=1.0, Y=0.0, Z=0.0))
        dirs = nd_ns["node_directions"](inst)
        self.assertEqual(dirs, [(0.0, 1.0), (-0.0, -1.0),
                                (1.0, 0.0), (-1.0, -0.0)])
        key, how = ns["aim_pick"]((50.0, 20.0), dirs, self.SEGS,
                                  self.ray, self.dist)
        self.assertEqual((key, how), ("far", "aimed"))

    def test_no_segments_is_none(self):
        key, how = ns["aim_pick"]((0.0, 0.0), [(0.0, 1.0)], [],
                                  self.ray, self.dist)
        self.assertEqual((key, how), (None, None))


class MarkerZ(unittest.TestCase):
    """The marker family's invert resolution order: its own 'Invert
    Level' parameter first, Level + Elevation from Level next, the
    location point last."""

    class P(object):
        def __init__(self, v):
            self.HasValue = v is not None
            self.StorageType = "Double"
            self._v = v

        def AsDouble(self):
            return self._v

    class El(object):
        def __init__(self, params, level_id="LID"):
            self._p = params
            self.LevelId = level_id

        def LookupParameter(self, nm):
            return self._p.get(nm)

    class Doc(object):
        def __init__(self, lvl):
            self._lvl = lvl

        def GetElement(self, _id):
            return self._lvl

    class Lvl(object):
        def __init__(self, proj):
            self.ProjectElevation = proj
            self.Elevation = proj + 150.0   # internal differs - must NOT win

    def test_workplane_datum_wins_over_the_marker_level(self):
        # the lines' work plane is the authoritative datum - the
        # marker's own (possibly wrong/unset) level must not shift it
        el = self.El({"Invert Level": self.P(169.7)})
        doc = self.Doc(self.Lvl(0.0))    # marker level internal = 150
        self.assertAlmostEqual(
            ns["_marker_z_ft"](doc, el, 999.0, datum_z_ft=-144.2),
            169.7 - 144.2)

    def test_invert_level_param_is_level_relative(self):
        # the typed invert is measured above the marker's LEVEL, and
        # the level's INTERNAL elevation carries it into model space -
        # a Datum Level far from the internal origin must not shift
        # the displayed result
        el = self.El({"Invert Level": self.P(169.7),
                      "Elevation from Level": self.P(5.0)})
        doc = self.Doc(self.Lvl(0.0))     # internal = 0 + 150
        self.assertAlmostEqual(
            ns["_marker_z_ft"](doc, el, 999.0), 169.7 + 150.0)

    def test_invert_level_param_without_level(self):
        el = self.El({"Invert Level": self.P(169.7)}, level_id=None)

        class NoDoc(object):
            def GetElement(self, _id):
                raise Exception("no level")
        self.assertAlmostEqual(
            ns["_marker_z_ft"](NoDoc(), el, 999.0), 169.7)

    def test_zero_invert_falls_back_to_level_plus_offset(self):
        el = self.El({"Invert Level": self.P(0.0),
                      "Elevation from Level": self.P(5.0)})
        doc = self.Doc(self.Lvl(10.0))
        self.assertAlmostEqual(
            ns["_marker_z_ft"](doc, el, 999.0), 15.0)

    def test_project_elevation_not_internal(self):
        el = self.El({"Elevation from Level": self.P(5.0)})
        doc = self.Doc(self.Lvl(0.0))
        # internal is proj + 150; the answer must use proj (0) + 5
        self.assertAlmostEqual(
            ns["_marker_z_ft"](doc, el, 999.0), 5.0)

    def test_location_z_as_last_resort(self):
        el = self.El({}, level_id=None)

        class NoDoc(object):
            def GetElement(self, _id):
                return None
        self.assertAlmostEqual(
            ns["_marker_z_ft"](NoDoc(), el, 42.0), 42.0)


class BuildRecord(unittest.TestCase):
    """load/save of the Update Pipes registry (plain json file)."""

    def setUp(self):
        import tempfile
        self.base = tempfile.mkdtemp()

    def test_round_trip(self):
        rec = {"dia_mm": 150.0, "invert_m": 5.0,
               "pick_mm": [318290.0, 63620.0],
               "custom_slopes": {"uid-1": 40.0},
               "element_uids": ["a", "b", "c"], "when": "now"}
        ns["save_lines_record"](self.base, rec)
        got = ns["load_lines_record"](self.base)
        self.assertEqual(got["element_uids"], ["a", "b", "c"])
        self.assertEqual(got["custom_slopes"], {"uid-1": 40.0})
        self.assertEqual(got["pick_mm"], [318290.0, 63620.0])

    def test_missing_file_is_empty_dict(self):
        self.assertEqual(ns["load_lines_record"](self.base), {})

    def test_corrupt_file_is_empty_dict(self):
        import os
        with open(os.path.join(self.base, "lines_network.json"),
                  "w") as f:
            f.write("{not json")
        self.assertEqual(ns["load_lines_record"](self.base), {})

    def test_save_creates_the_folder(self):
        import os
        deep = os.path.join(self.base, "sub", "deeper")
        ns["save_lines_record"](deep, {"x": 1})
        self.assertEqual(ns["load_lines_record"](deep), {"x": 1})


if __name__ == "__main__":
    unittest.main(verbosity=2)
