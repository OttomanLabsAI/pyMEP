#!/usr/bin/env python3
"""Unit tests for the drainage-networks dashboard's pure parts (stdlib
only, no Revit needed): type-name parsing, main-line keys, run
extremes, the main's new-ends math, Z projection, and the edits-file
plumbing. Extracted by AST (the module imports the Revit API).

Run:  python3 tests/test_drainage_networks.py
"""

import ast
import math
import os
import shutil
import tempfile
import time
import unittest

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "pyMEP.extension", "lib")

FT = 304.8 / 1000.0


def load(names):
    path = os.path.join(LIB, "pymep_drainage_networks.py")
    with open(path) as f:
        src = f.read()
    import json as _json
    ns = {"math": math, "os": os, "json": _json, "FT_TO_M": FT,
          "EDITS_KIND": "pymep-drainage-edits",
          "EDITS_PREFIX": "pymep_network_edits",
          "FILTER_DEFAULT": "node"}

    def regrade_main_ends(a, b, slope_n, keep="low"):
        if not slope_n or slope_n <= 0:
            return a, b
        run = math.hypot(b[0] - a[0], b[1] - a[1])
        fall = run / slope_n
        a_low = a[2] < b[2]
        pin_a = (a_low and keep == "low") or ((not a_low) and
                                              keep == "high")
        if pin_a:
            return a, (b[0], b[1], a[2] + (fall if keep == "low"
                                           else -fall))
        return (a[0], a[1], b[2] + (fall if keep == "low" else -fall)), b

    ns["regrade_main_ends"] = regrade_main_ends
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.get_source_segment(src, node), path, "exec"),
                 ns)
    return ns


NS = load(["parse_network", "line_key", "run_extremes", "new_main_ends",
           "project_z", "parse_edits", "find_edits_file", "mark_applied",
           "networks_settings", "edits_stamp"])
networks_settings = NS["networks_settings"]
edits_stamp = NS["edits_stamp"]
parse_network = NS["parse_network"]
line_key = NS["line_key"]
run_extremes = NS["run_extremes"]
new_main_ends = NS["new_main_ends"]
project_z = NS["project_z"]
parse_edits = NS["parse_edits"]
find_edits_file = NS["find_edits_file"]
mark_applied = NS["mark_applied"]


class NetworksSettings(unittest.TestCase):

    def test_defaults(self):
        self.assertEqual(networks_settings({}), ("node", "", True))

    def test_values_and_blanks(self):
        s = {"networks_filter": "  chamber ",
             "networks_edits_folder": " C:\\edits ",
             "networks_confirm_apply": False}
        self.assertEqual(networks_settings(s),
                         ("chamber", "C:\\edits", False))
        self.assertEqual(networks_settings({"networks_filter": "   "}),
                         ("node", "", True))


class ParseNetwork(unittest.TestCase):

    def test_three_parts(self):
        n = parse_network("STORMWATER - IN - N1")
        self.assertEqual(n["system"], "STORMWATER")
        self.assertEqual(n["flow"], "IN")
        self.assertEqual(n["label"], "N1")
        self.assertEqual(n["name"], "STORMWATER - IN - N1")

    def test_fewer_parts_degrade(self):
        self.assertEqual(parse_network("FOUL")["system"], "FOUL")
        self.assertEqual(parse_network("FOUL")["flow"], "")
        self.assertEqual(parse_network("A - B")["flow"], "B")
        self.assertEqual(parse_network("")["name"], "(unnamed)")


class LineKey(unittest.TestCase):

    def test_direction_independent(self):
        a, b = (1.23456, 2.0, 3.0), (9.0, 8.0, 7.0)
        self.assertEqual(line_key((a, b)), line_key((b, a)))

    def test_rounds_to_3dp(self):
        self.assertEqual(line_key(((1.0004, 0, 0), (2, 0, 0))),
                         line_key(((1.0001, 0, 0), (2, 0, 0))))


class RunExtremes(unittest.TestCase):

    def test_farthest_pair(self):
        ends = [(0, 0, 10), (40, 0, 9.5), (40, 0, 9.5), (100, 0, 9)]
        a, b = run_extremes(ends)
        self.assertEqual(sorted([a[0], b[0]]), [0, 100])


class NewMainEnds(unittest.TestCase):
    A = (0.0, 0.0, 33.0)     # upper
    B = (100.0, 0.0, 32.0)   # lower

    def test_untouched(self):
        self.assertEqual(new_main_ends(self.A, self.B, 1.0), (self.A,
                                                              self.B))

    def test_regrade_keep_lower(self):
        a2, b2 = new_main_ends(self.A, self.B, 1.0, slope_n=200,
                               keep="lower")
        self.assertEqual(b2, self.B)                  # lower end pinned
        self.assertAlmostEqual(a2[2], 32.0 + 100.0 / 200)

    def test_regrade_keep_upper(self):
        a2, b2 = new_main_ends(self.A, self.B, 1.0, slope_n=100,
                               keep="upper")
        self.assertEqual(a2, self.A)                  # upper end pinned
        self.assertAlmostEqual(b2[2], 33.0 - 1.0)

    def test_set_lower_invert(self):
        dia = 0.9843  # ~300 mm in ft
        a2, b2 = new_main_ends(self.A, self.B, dia, invert_end="lower",
                               invert_m=9.0)
        # lower end centreline = invert + dia/2; fall preserved (1.0 ft)
        self.assertAlmostEqual(b2[2], 9.0 / FT + dia / 2.0)
        self.assertAlmostEqual(a2[2] - b2[2], 1.0)

    def test_set_upper_invert_with_slope(self):
        a2, b2 = new_main_ends(self.A, self.B, 0.0, slope_n=50,
                               invert_end="upper", invert_m=10.0)
        self.assertAlmostEqual(a2[2], 10.0 / FT)
        self.assertAlmostEqual(a2[2] - b2[2], 100.0 / 50)


class ProjectZ(unittest.TestCase):

    def test_interpolates(self):
        a, b = (0, 0, 10), (100, 0, 8)
        self.assertAlmostEqual(project_z((50, 5, 99), a, b), 9.0)
        self.assertAlmostEqual(project_z((-10, 0, 0), a, b), 10.0)


class EditsPlumbing(unittest.TestCase):

    def setUp(self):
        self.base = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_parse_rejects_junk(self):
        self.assertRaises(ValueError, parse_edits, "{nope")
        self.assertRaises(ValueError, parse_edits, '{"kind": "x"}')
        self.assertRaises(ValueError, parse_edits,
                          '{"kind": "pymep-drainage-edits", "edits": []}')

    def test_parse_accepts_real(self):
        d = parse_edits('{"kind": "pymep-drainage-edits", '
                        '"edits": [{"network": "N"}]}')
        self.assertEqual(len(d["edits"]), 1)

    def _touch(self, name, age=0):
        p = os.path.join(self.base, name)
        with open(p, "w") as f:
            f.write("{}")
        t = time.time() - age
        os.utime(p, (t, t))
        return p

    def test_newest_wins_and_applied_skipped(self):
        self._touch("pymep_network_edits.json", age=100)
        newest = self._touch("pymep_network_edits (1).json", age=10)
        self._touch("pymep_network_edits (2).applied.json", age=0)
        self._touch("other.json", age=0)
        self.assertEqual(find_edits_file(self.base), newest)

    def test_none_when_empty(self):
        self.assertIsNone(find_edits_file(self.base))

    def test_mark_applied_renames(self):
        p = self._touch("pymep_network_edits.json")
        new = mark_applied(p)
        self.assertTrue(new.endswith(".applied.json"))
        self.assertFalse(os.path.exists(p))
        self.assertIsNone(find_edits_file(self.base))

    def test_edits_stamp_prefers_saved(self):
        p = self._touch("pymep_network_edits.json")
        self.assertEqual(edits_stamp({"saved": "2026-07-29T10:00:00Z"},
                                     p), "2026-07-29T10:00:00Z")
        # no saved key -> the file's mtime identifies the save
        s = edits_stamp({}, p)
        self.assertTrue(s.startswith("mtime:"))
        self.assertEqual(s, edits_stamp({"saved": ""}, p))
        # missing file AND no stamp -> empty (never blocks the apply)
        self.assertEqual(edits_stamp({}, os.path.join(self.base,
                                                      "gone.json")), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
