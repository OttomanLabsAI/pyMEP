#!/usr/bin/env python3
"""Unit tests for the RVT-to-RVT pipe-type import reporting (stdlib
only, no Revit needed). ``diff_names`` in lib/pymep_pipetypes_copy.py
is pure and extracted by AST; it must report exactly which requested
types came in new and which already existed (and were kept), including
the corner cases: nothing new, everything new, and a destination that
gained unrelated names mid-copy is not this function's problem - only
requested/before/after matter.

Run:  python3 tests/test_pipetypes_import.py
"""

import ast
import os
import unittest

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "pyMEP.extension", "lib")


def extract_function(module_file, func_name):
    path = os.path.join(LIB, module_file)
    with open(path) as f:
        src = f.read()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            ns = {}
            exec(compile(ast.get_source_segment(src, node), path, "exec"),
                 ns)
            return ns[func_name]
    raise AssertionError("{} not found in {}".format(func_name, path))


diff_names = extract_function("pymep_pipetypes_copy.py", "diff_names")


class DiffNames(unittest.TestCase):

    def test_mixed_created_and_existing(self):
        created, existed = diff_names(
            before={"Default", "Storm"},
            after={"Default", "Storm", "Foul", "Potable"},
            requested=["Storm", "Foul", "Potable"])
        self.assertEqual(created, ["Foul", "Potable"])
        self.assertEqual(existed, ["Storm"])

    def test_everything_already_present(self):
        created, existed = diff_names(
            {"A", "B"}, {"A", "B"}, ["A", "B"])
        self.assertEqual(created, [])
        self.assertEqual(existed, ["A", "B"])

    def test_everything_new_into_empty_model(self):
        created, existed = diff_names(set(), {"A", "B"}, ["A", "B"])
        self.assertEqual(created, ["A", "B"])
        self.assertEqual(existed, [])

    def test_results_sorted(self):
        created, existed = diff_names(
            {"Z"}, {"Z", "C", "A", "B"}, ["C", "A", "B", "Z"])
        self.assertEqual(created, ["A", "B", "C"])
        self.assertEqual(existed, ["Z"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
