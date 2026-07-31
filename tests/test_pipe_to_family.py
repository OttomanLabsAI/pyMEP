#!/usr/bin/env python3
"""Unit tests for the Family at Pipe Top decisions (stdlib only, no
Revit).

The pure parts of lib/pymep_pipe_to_family.py: which end of a line
element is the top, where the top of a placed family's box is, how the
category > family > type picker lists are shaped, how the search
matches, and how the result sentence reads. Extracted by AST (the
module imports the Revit API and cannot import under CPython).

Run:  python3 tests/test_pipe_to_family.py
"""

import ast
import os
import unittest

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "pyMEP.extension", "lib")
SRC_PATH = os.path.join(LIB, "pymep_pipe_to_family.py")


def extract(names):
    with open(SRC_PATH) as f:
        src = f.read()
    ns = {}
    wanted = set(names)
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            exec(compile(ast.get_source_segment(src, node), SRC_PATH,
                         "exec"), ns)
    missing = wanted - set(ns)
    if missing:
        raise AssertionError("not found: " + ", ".join(sorted(missing)))
    return [ns[n] for n in names]


(top_point, box_top, symbol_categories, symbol_families, symbol_types_in,
 search_symbol_rows, placement_summary) = extract([
     "top_point", "box_top", "symbol_categories", "symbol_families",
     "symbol_types_in", "search_symbol_rows", "placement_summary"])


def row(cat, fam, typ):
    return {"cat": cat, "fam": fam, "type": typ,
            "label": "{} : {} : {}".format(cat, fam, typ), "id": 1}


ROWS = [
    row("Pipe Fittings", "Bend", "45 deg"),
    row("Generic Models", "Chamber", "1200 dia"),
    row("Generic Models", "Chamber", "900 dia"),
    row("Generic Models", "Cover", "D400"),
]


class TopPoint(unittest.TestCase):

    def test_riser_takes_its_head(self):
        base, head = (10.0, 4.0, 2.5), (10.0, 4.0, 18.75)
        self.assertEqual(top_point(base, head), head)
        self.assertEqual(top_point(head, base), head)

    def test_graded_run_takes_the_upstream_end(self):
        upper, lower = (0.0, 0.0, 3.2), (100.0, 0.0, 2.2)
        self.assertEqual(top_point(lower, upper), upper)

    def test_flat_pipe_keeps_its_start(self):
        a, b = (1.0, 2.0, 5.0), (9.0, 2.0, 5.0)
        self.assertEqual(top_point(a, b), a)

    def test_coordinates_are_not_rounded(self):
        a = (123.456789, -987.654321, 0.1)
        b = (0.0, 0.0, 0.2000000001)
        self.assertIs(top_point(a, b), b)
        self.assertEqual(top_point(b, a), b)


class BoxTop(unittest.TestCase):

    def test_family_gets_its_own_xy_at_the_box_top(self):
        self.assertEqual(box_top(12.5, -3.0, 1.0, 4.25), (12.5, -3.0, 4.25))

    def test_a_flat_box_gives_the_point_itself(self):
        self.assertEqual(box_top(0.0, 0.0, 2.0, 2.0), (0.0, 0.0, 2.0))

    def test_an_inverted_box_never_lands_below_the_element(self):
        self.assertEqual(box_top(1.0, 1.0, 5.0, 3.0), (1.0, 1.0, 5.0))

    def test_coordinates_are_not_rounded(self):
        self.assertEqual(box_top(123.456789, -987.654321, 0.0, 0.30000001),
                         (123.456789, -987.654321, 0.30000001))


class PickerLists(unittest.TestCase):

    def test_categories_are_unique_and_sorted(self):
        self.assertEqual(symbol_categories(ROWS),
                         ["Generic Models", "Pipe Fittings"])

    def test_families_are_scoped_to_the_category(self):
        self.assertEqual(symbol_families(ROWS, "Generic Models"),
                         ["Chamber", "Cover"])
        self.assertEqual(symbol_families(ROWS, "Pipe Fittings"), ["Bend"])

    def test_unknown_category_gives_nothing(self):
        self.assertEqual(symbol_families(ROWS, "Ducts"), [])

    def test_types_are_scoped_to_category_and_family(self):
        got = symbol_types_in(ROWS, "Generic Models", "Chamber")
        self.assertEqual([r["type"] for r in got], ["1200 dia", "900 dia"])

    def test_types_of_a_foreign_pair_are_empty(self):
        self.assertEqual(symbol_types_in(ROWS, "Pipe Fittings", "Chamber"),
                         [])


class Search(unittest.TestCase):

    def test_every_word_must_match(self):
        got = search_symbol_rows(ROWS, "chamber 900")
        self.assertEqual([r["type"] for r in got], ["900 dia"])

    def test_matching_is_case_insensitive(self):
        self.assertEqual(len(search_symbol_rows(ROWS, "COVER")), 1)

    def test_empty_query_falls_back_to_the_cascade(self):
        self.assertEqual(search_symbol_rows(ROWS, ""), [])
        self.assertEqual(search_symbol_rows(ROWS, "   "), [])
        self.assertEqual(search_symbol_rows(ROWS, None), [])

    def test_no_hit_is_empty_not_everything(self):
        self.assertEqual(search_symbol_rows(ROWS, "manhole"), [])

    def test_a_word_may_match_the_category(self):
        got = search_symbol_rows(ROWS, "generic dia")
        self.assertEqual(len(got), 2)


class Summary(unittest.TestCase):

    def test_single_placement_reads_singular(self):
        self.assertEqual(placement_summary(1, 0, 0),
                         "Placed 1 family on top.")

    def test_deletions_are_reported(self):
        self.assertIn("4 original(s) deleted", placement_summary(4, 4, 0))

    def test_failures_point_at_the_report(self):
        msg = placement_summary(3, 0, 2)
        self.assertIn("Placed 3 families", msg)
        self.assertIn("2 failed", msg)

    def test_clean_run_mentions_neither(self):
        msg = placement_summary(6, 0, 0)
        self.assertNotIn("deleted", msg)
        self.assertNotIn("failed", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
