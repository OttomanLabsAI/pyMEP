#!/usr/bin/env python3
"""Unit tests for the view-template transfer schema layer (stdlib
only, no Revit). pymep_vt_schema is deliberately pure so this suite
imports it directly; the Revit-bound serialize / deserialize halves
share its vocabulary, so nailing the vocabulary here nails both.

Run:  python3 tests/test_view_templates.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "pyMEP.extension", "lib"))

import pymep_vt_schema as S


class EvaluatorVocabulary(unittest.TestCase):
    """The evaluator maps are bijective - every Revit evaluator class
    has exactly one schema word and back, so a rule exported on one
    side always rebuilds identically on the other."""

    def test_string_round_trip(self):
        for cls, word in S.STRING_EVALUATORS.items():
            self.assertEqual(S.STRING_EVALUATOR_NAMES[word], cls)
        self.assertEqual(len(S.STRING_EVALUATORS),
                         len(S.STRING_EVALUATOR_NAMES))

    def test_numeric_round_trip(self):
        for cls, word in S.NUMERIC_EVALUATORS.items():
            self.assertEqual(S.NUMERIC_EVALUATOR_NAMES[word], cls)
        self.assertEqual(len(S.NUMERIC_EVALUATORS),
                         len(S.NUMERIC_EVALUATOR_NAMES))

    def test_all_documented_evaluators_present(self):
        for word in ("equals", "contains", "begins", "ends", "greater",
                     "greater_or_equal", "less", "less_or_equal"):
            self.assertIn(word, S.STRING_EVALUATOR_NAMES)
        for word in ("equals", "greater", "greater_or_equal", "less",
                     "less_or_equal"):
            self.assertIn(word, S.NUMERIC_EVALUATOR_NAMES)


NESTED_FILTER = {
    "name": "EL - Conduit LV",
    "categories": ["OST_Conduit", "OST_ConduitFitting"],
    "element_filter": {
        "logic": "and",
        "children": [
            {"logic": "rules", "rules": [
                {"parameter": {"kind": "builtin",
                               "id": "ALL_MODEL_INSTANCE_COMMENTS"},
                 "rule": "string", "evaluator": "contains",
                 "value": "LV", "inverted": True},
                {"parameter": {"kind": "shared",
                               "guid": "f9c8b7a6-0000-1111-2222-333344445555",
                               "name": "Zone"},
                 "rule": "double", "evaluator": "greater",
                 "value": 3.2808, "epsilon": 1e-6,
                 "inverted": False}]},
            {"logic": "or", "children": [
                {"logic": "rules", "rules": [
                    {"parameter": {"kind": "project", "name": "Loop"},
                     "rule": "has_value", "inverted": False}]}]},
        ],
    },
}


class CanonicalJson(unittest.TestCase):

    def test_dumps_is_deterministic_and_sorted(self):
        doc = S.make_document("2025", "20250401")
        doc["filters"].append(dict(NESTED_FILTER))
        a = S.dumps(doc)
        b = S.dumps(S.loads(a))
        self.assertEqual(a, b)                       # byte-identical
        self.assertLess(a.index('"filters"'),
                        a.index('"view_templates"'))  # sorted keys

    def test_nested_filter_survives_the_text_round_trip(self):
        text = S.dumps({"filters": [NESTED_FILTER],
                        "view_templates": [],
                        "schema_version": S.SCHEMA_VERSION})
        back = S.loads(text)
        self.assertEqual(back["filters"][0], NESTED_FILTER)

    def test_document_skeleton(self):
        doc = S.make_document("2022", "b1")
        for key in ("schema_version", "revit_version", "revit_build",
                    "exported", "filters", "view_templates"):
            self.assertIn(key, doc)
        self.assertEqual(doc["schema_version"], S.SCHEMA_VERSION)


class Validation(unittest.TestCase):

    def _doc(self):
        d = S.make_document("2025", "x")
        d["filters"].append(dict(NESTED_FILTER))
        d["view_templates"].append({"name": "T", "filters": []})
        return d

    def test_good_document_passes(self):
        self.assertEqual(S.validate_document(self._doc()), [])

    def test_wrong_schema_version_flagged(self):
        d = self._doc()
        d["schema_version"] = 999
        self.assertTrue(any("schema_version" in n
                            for n in S.validate_document(d)))

    def test_unknown_rule_kind_flagged(self):
        d = self._doc()
        d["filters"][0] = {
            "name": "Bad", "categories": [],
            "element_filter": {"logic": "rules", "rules": [
                {"parameter": {"kind": "builtin", "id": "X"},
                 "rule": "regex"}]}}
        notes = S.validate_document(d)
        self.assertTrue(any("unknown rule kind 'regex'" in n
                            for n in notes))

    def test_unknown_logic_flagged(self):
        d = self._doc()
        d["filters"][0] = {"name": "Bad", "categories": [],
                           "element_filter": {"logic": "xor",
                                              "children": []}}
        notes = S.validate_document(d)
        self.assertTrue(any("unknown logic 'xor'" in n for n in notes))

    def test_filters_used_by(self):
        t = {"name": "T", "filters": [{"name": "A"}, {"name": "B"},
                                      {"name": None}]}
        self.assertEqual(S.filters_used_by(t), ["A", "B"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
