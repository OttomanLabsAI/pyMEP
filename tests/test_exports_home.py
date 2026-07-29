#!/usr/bin/env python3
"""The durable exports home: EXPORTS_ROOT must live in %APPDATA% (so
Install Update can't wipe the tracking registries / project files) and
_merge_copy must rescue legacy data WITHOUT overwriting live files.
pymep_config is plain Python - imported directly, with APPDATA pointed
at a sandbox.

Run:  python3 tests/test_exports_home.py
"""

import importlib.util
import os
import shutil
import tempfile
import unittest

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "pyMEP.extension", "lib")


def load_config(appdata):
    os.environ["APPDATA"] = appdata
    spec = importlib.util.spec_from_file_location(
        "pymep_config_t", os.path.join(LIB, "pymep_config.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ExportsHome(unittest.TestCase):

    def setUp(self):
        self.appdata = tempfile.mkdtemp()
        self.cfg = load_config(self.appdata)

    def tearDown(self):
        shutil.rmtree(self.appdata, ignore_errors=True)

    def test_root_is_outside_the_extension(self):
        self.assertEqual(
            self.cfg.EXPORTS_ROOT,
            os.path.join(self.appdata, "pyRevit", "pyMEP_exports"))
        self.assertNotEqual(self.cfg.EXPORTS_ROOT,
                            self.cfg.LEGACY_EXPORTS_ROOT)
        self.assertTrue(
            self.cfg.LEGACY_EXPORTS_ROOT.startswith(self.cfg.EXT_ROOT))

    def test_merge_copy_rescues_without_overwriting(self):
        src = tempfile.mkdtemp()
        dst = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(src, "Model", "project_files"))
            with open(os.path.join(src, "Model", "project_files",
                                   "node_branches.json"), "w") as f:
                f.write('{"branches": ["old"]}')
            with open(os.path.join(src, "Model", "stale.json"), "w") as f:
                f.write("stale")
            # the live home already has a NEWER registry - it must win
            os.makedirs(os.path.join(dst, "Model", "project_files"))
            with open(os.path.join(dst, "Model", "project_files",
                                   "node_branches.json"), "w") as f:
                f.write('{"branches": ["live"]}')

            n = self.cfg._merge_copy(src, dst)
            self.assertEqual(n, 1)          # only stale.json came over
            with open(os.path.join(dst, "Model", "project_files",
                                   "node_branches.json")) as f:
                self.assertIn("live", f.read())
            self.assertTrue(os.path.isfile(
                os.path.join(dst, "Model", "stale.json")))
        finally:
            shutil.rmtree(src, ignore_errors=True)
            shutil.rmtree(dst, ignore_errors=True)

    def test_default_folder_rescues_legacy(self):
        class Doc(object):
            Title = "TrackedModel"

        legacy = os.path.join(self.cfg.LEGACY_EXPORTS_ROOT, "TrackedModel",
                              "project_files")
        made_legacy = not os.path.isdir(legacy)
        os.makedirs(legacy)
        try:
            with open(os.path.join(legacy, "node_branches.json"),
                      "w") as f:
                f.write('{"branches": [1]}')
            folder = self.cfg.get_default_export_folder(Doc())
            self.assertTrue(folder.startswith(self.cfg.EXPORTS_ROOT))
            rescued = os.path.join(folder, "project_files",
                                   "node_branches.json")
            self.assertTrue(os.path.isfile(rescued))
        finally:
            if made_legacy:
                shutil.rmtree(os.path.join(self.cfg.LEGACY_EXPORTS_ROOT,
                                           "TrackedModel"),
                              ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
