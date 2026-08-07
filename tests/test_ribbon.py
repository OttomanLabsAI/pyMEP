#!/usr/bin/env python3
"""Unit tests for the ribbon panel-visibility helpers (stdlib only,
no Revit) - extracted by AST from lib/pymep_ribbon.py.

Run:  python3 tests/test_ribbon.py
"""

import ast
import io
import os
import unittest

SRC_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "pyMEP.extension", "lib", "pymep_ribbon.py")

ns = {}
_src = io.open(SRC_PATH, encoding="utf-8").read()
for node in ast.parse(_src).body:
    if isinstance(node, (ast.FunctionDef, ast.Assign)):
        try:
            exec(compile(ast.get_source_segment(_src, node), SRC_PATH,
                         "exec"), ns)
        except Exception:
            pass

_panel_key = ns["_panel_key"]
HIDEABLE_PANELS = ns["HIDEABLE_PANELS"]


class PanelKey(unittest.TestCase):

    def test_every_hideable_panel_matches_itself(self):
        for name in HIDEABLE_PANELS:
            self.assertEqual(_panel_key(name), name)

    def test_longest_prefix_wins(self):
        # 'Pipe Networks' must NEVER resolve to 'Networks'
        self.assertEqual(_panel_key("Pipe Networks"), "Pipe Networks")
        self.assertEqual(_panel_key("Networks"), "Networks")

    def test_versioned_and_unknown_titles(self):
        self.assertIsNone(_panel_key("pyMEP v1.147.0"))
        self.assertIsNone(_panel_key("Something Else"))

    def test_new_panel_is_hideable(self):
        self.assertIn("Project Data Transfer", HIDEABLE_PANELS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
