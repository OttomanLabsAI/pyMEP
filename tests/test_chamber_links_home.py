#!/usr/bin/env python3
"""The chamber-section links file must live in the durable per-model
home (%APPDATA%\\pyRevit\\pyMEP_exports\\<model>\\), not inside the
extension folder that Install Update replaces - and a file left by an
older version must be picked up once, never overwriting a live one.

links_path is extracted by AST from pymep_chamber_links (the module
imports the Revit API); pymep_config is loaded against a sandbox
APPDATA and registered as `pymep_config` so the extracted function
finds it.

Run:  python3 tests/test_chamber_links_home.py
"""

import ast
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "pyMEP.extension", "lib")
LINKS_SRC = os.path.join(LIB, "pymep_chamber_links.py")
if LIB not in sys.path:            # pymep_config imports pymep_json
    sys.path.insert(0, LIB)


def load_config(appdata):
    os.environ["APPDATA"] = appdata
    spec = importlib.util.spec_from_file_location(
        "pymep_config", os.path.join(LIB, "pymep_config.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def extract(names):
    src = io.open(LINKS_SRC, encoding="utf-8").read()
    ns = {"os": os, "shutil": shutil,
          "LINK_FILENAME": "chamber_section_links.json"}
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.get_source_segment(src, node), LINKS_SRC,
                         "exec"), ns)
    return ns


class Doc(object):
    Title = "Linked Model"


class LinksHome(unittest.TestCase):

    def setUp(self):
        self.appdata = tempfile.mkdtemp()
        self.legacy_root = tempfile.mkdtemp()
        self.cfg = load_config(self.appdata)
        self.cfg.LEGACY_EXPORTS_ROOT = self.legacy_root
        self._old_mod = sys.modules.get("pymep_config")
        sys.modules["pymep_config"] = self.cfg
        self.ns = extract(("_safe_name", "_title", "links_path"))
        self.links_path = self.ns["links_path"]

    def tearDown(self):
        if self._old_mod is not None:
            sys.modules["pymep_config"] = self._old_mod
        else:
            sys.modules.pop("pymep_config", None)
        shutil.rmtree(self.appdata, ignore_errors=True)
        shutil.rmtree(self.legacy_root, ignore_errors=True)

    def test_lives_in_the_durable_home(self):
        path = self.links_path(Doc())
        self.assertTrue(path.startswith(self.cfg.EXPORTS_ROOT), path)
        self.assertTrue(path.startswith(
            os.path.join(self.appdata, "pyRevit", "pyMEP_exports")))
        self.assertFalse(path.startswith(self.legacy_root))
        self.assertEqual(os.path.basename(path), "chamber_section_links.json")
        self.assertTrue(os.path.isdir(os.path.dirname(path)))
        self.assertFalse(os.path.exists(path))   # nothing to rescue

    def test_rescues_a_file_left_inside_the_extension(self):
        old_dir = os.path.join(self.legacy_root,
                               self.ns["_safe_name"]("Linked Model"))
        os.makedirs(old_dir)
        with open(os.path.join(old_dir, "chamber_section_links.json"),
                  "w") as f:
            f.write('{"12": {"chamber_mark": "LV1/Z1"}}')
        path = self.links_path(Doc())
        self.assertTrue(os.path.isfile(path))
        with open(path) as f:
            self.assertIn("LV1/Z1", f.read())
        # and the copy is in the durable home, not the legacy one
        self.assertTrue(path.startswith(self.cfg.EXPORTS_ROOT))

    def test_never_overwrites_a_live_file(self):
        path = self.links_path(Doc())
        with open(path, "w") as f:
            f.write('{"live": true}')
        old_dir = os.path.join(self.legacy_root,
                               self.ns["_safe_name"]("Linked Model"))
        os.makedirs(old_dir)
        with open(os.path.join(old_dir, "chamber_section_links.json"),
                  "w") as f:
            f.write('{"stale": true}')
        again = self.links_path(Doc())
        self.assertEqual(again, path)
        with open(path) as f:
            self.assertIn("live", f.read())


if __name__ == "__main__":
    unittest.main(verbosity=2)
