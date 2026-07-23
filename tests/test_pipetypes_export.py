#!/usr/bin/env python3
"""Unit tests for the pipe-types export schema shaping (stdlib only,
no Revit needed).

The pure data-shaping functions in lib/pymep_pipetypes_export.py are
extracted by AST (the module imports the Revit API and cannot be
imported under CPython) and exercised against the schema contract:

  - the PrimarySizeCriterion.All() sentinel becomes an explicit
    covers_all_sizes flag, never a magic number in the JSON;
  - fittings dedupe to one entry per (family, type) with a rule count;
  - the header is versioned and mm-united;
  - NO key anywhere in a payload is an ElementId-shaped identifier -
    everything is referenced by name.

Run:  python3 tests/test_pipetypes_export.py
"""

import ast
import json
import os
import unittest

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "pyMEP.extension", "lib")


def extract_function(module_file, func_name, extra=None):
    path = os.path.join(LIB, module_file)
    with open(path) as f:
        src = f.read()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            ns = dict(extra or {})
            exec(compile(ast.get_source_segment(src, node), path, "exec"),
                 ns)
            return ns[func_name]
    raise AssertionError("{} not found in {}".format(func_name, path))


MOD = "pymep_pipetypes_export.py"
size_criterion_entry = extract_function(MOD, "size_criterion_entry")
dedupe_fittings = extract_function(MOD, "dedupe_fittings")
build_header = extract_function(MOD, "build_header",
                                extra={"SCHEMA_VERSION": "1.0"})
build_payload = extract_function(MOD, "build_payload")
summarize_payload = extract_function(MOD, "summarize_payload")


def fitting_rule(family, ftype, desc=""):
    return {"description": desc,
            "part": {"kind": "fitting", "family": family, "type": ftype,
                     "category": "Pipe Fittings"},
            "criteria": [{"type": "size", "covers_all_sizes": True}]}


def segment_rule(name):
    return {"description": "",
            "part": {"kind": "segment", "segment": name},
            "criteria": [size_criterion_entry(50.0, 300.0)]}


def sample_payload():
    types = [
        {"name": "Storm", "preferred_junction_type": "Tee",
         "routing_preferences": {
             "Segments": [segment_rule("PE 100 - SDR 17")],
             "Elbows": [fitting_rule("Bend PE", "Standard"),
                        fitting_rule("Bend PE", "Long radius")],
             "Junctions": [fitting_rule("Tee PE", "Standard")]}},
        {"name": "Foul", "preferred_junction_type": "Tap",
         "routing_preferences": {
             "Segments": [segment_rule("PE 100 - SDR 17")],
             "Elbows": [fitting_rule("Bend PE", "Standard")]}},
    ]
    segments = [{"name": "PE 100 - SDR 17", "roughness_mm": 0.0015,
                 "schedule_type": "SDR 17", "material": "Polyethylene",
                 "sizes": [{"nominal_mm": 110.0, "inner_mm": 96.8,
                            "outer_mm": 110.0,
                            "used_in_size_lists": True,
                            "used_in_sizing": True}]}]
    header = build_header("HEL18-model", "2024", "20240409_1515",
                          "2026-07-23T10:00:00")
    return build_payload(header, types, segments,
                         dedupe_fittings(types), ["one warning"])


class SizeCriterion(unittest.TestCase):

    def test_all_sizes_sentinel_becomes_flag(self):
        # PrimarySizeCriterion.All() reads back with a giant maximum -
        # the JSON must carry the flag, not the number
        e = size_criterion_entry(0.0, 5.48e310)
        self.assertEqual(e, {"type": "size", "covers_all_sizes": True})
        self.assertNotIn("max_mm", e)

    def test_real_range_kept_in_mm(self):
        e = size_criterion_entry(50.0, 315.0)
        self.assertFalse(e["covers_all_sizes"])
        self.assertEqual((e["min_mm"], e["max_mm"]), (50.0, 315.0))

    def test_large_but_real_max_not_mistaken_for_all(self):
        # 999999 mm typed by a user is absurd but real - only the true
        # sentinel (>= 1e9 mm) may collapse to the flag
        e = size_criterion_entry(0.0, 999999.0)
        self.assertFalse(e["covers_all_sizes"])


class FittingsDedupe(unittest.TestCase):

    def test_counts_references_across_types_and_groups(self):
        p = sample_payload()
        fits = {(f["family"], f["type"]): f["rule_count"]
                for f in p["fittings"]}
        self.assertEqual(fits[("Bend PE", "Standard")], 2)
        self.assertEqual(fits[("Bend PE", "Long radius")], 1)
        self.assertEqual(fits[("Tee PE", "Standard")], 1)

    def test_sorted_and_identity_only(self):
        fits = sample_payload()["fittings"]
        self.assertEqual(fits, sorted(
            fits, key=lambda f: (f["family"], f["type"])))
        for f in fits:
            self.assertEqual(sorted(f.keys()),
                             ["category", "family", "rule_count", "type"])


class HeaderAndPayload(unittest.TestCase):

    def test_header_contract(self):
        h = sample_payload()["header"]
        self.assertEqual(h["schema_version"], "1.0")
        self.assertEqual(h["units"], "mm")
        self.assertEqual(h["source_model"], "HEL18-model")
        self.assertEqual(h["revit_version"], "2024")
        self.assertEqual(h["revit_build"], "20240409_1515")
        self.assertEqual(h["exported"], "2026-07-23T10:00:00")

    def test_types_sorted_and_warnings_kept(self):
        p = sample_payload()
        self.assertEqual([t["name"] for t in p["pipe_types"]],
                         ["Foul", "Storm"])
        self.assertEqual(p["warnings"], ["one warning"])

    def test_summary_counts(self):
        s = summarize_payload(sample_payload())
        self.assertEqual(s, {"types": 2, "rules": 6, "segments": 1,
                             "fittings": 3, "fitting_families": 2,
                             "warnings": 1})


class NoElementIds(unittest.TestCase):

    def test_no_id_shaped_keys_anywhere(self):
        def walk(obj, path=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    lk = k.lower()
                    self.assertFalse(
                        lk == "id" or lk.endswith("_id")
                        or "elementid" in lk,
                        "ElementId-shaped key '{}' at {}".format(k, path))
                    walk(v, path + "/" + k)
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    walk(v, "{}[{}]".format(path, i))
        walk(sample_payload())

    def test_json_serializable(self):
        json.dumps(sample_payload())


if __name__ == "__main__":
    unittest.main(verbosity=2)
