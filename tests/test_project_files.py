#!/usr/bin/env python3
"""Unit tests for the per-project file store (lib/pymep_project_files.py
is Revit-free on purpose, so it imports and runs directly under
CPython).

Run:  python3 tests/test_project_files.py
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "pyMEP.extension", "lib"))
import pymep_project_files as pf


class ProjectFiles(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.base = os.path.join(self.tmp, "project_files")
        self.src = os.path.join(self.tmp, "HEL18 utilities.xml")
        with open(self.src, "w") as f:
            f.write("<LandXML/>")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_store_and_resolve(self):
        dest = pf.store_file(self.base, "dashboard_data", self.src)
        self.assertTrue(os.path.isfile(dest))
        self.assertEqual(pf.slot_file(self.base, "dashboard_data"), dest)
        # the original stays where it was
        self.assertTrue(os.path.isfile(self.src))

    def test_empty_slot_is_none(self):
        self.assertIsNone(pf.slot_file(self.base, "dashboard_data"))
        rows = pf.list_entries(self.base)
        self.assertEqual(rows[0][0], "dashboard_data")
        self.assertIsNone(rows[0][2])

    def test_replace_deletes_the_old_copy(self):
        pf.store_file(self.base, "dashboard_data", self.src)
        src2 = os.path.join(self.tmp, "newer.xml")
        with open(src2, "w") as f:
            f.write("<LandXML v2/>")
        dest2 = pf.store_file(self.base, "dashboard_data", src2)
        self.assertEqual(pf.slot_file(self.base, "dashboard_data"), dest2)
        self.assertFalse(os.path.isfile(
            os.path.join(self.base, "HEL18 utilities.xml")))

    def test_remove_slot_deletes_copy(self):
        dest = pf.store_file(self.base, "dashboard_data", self.src)
        self.assertTrue(pf.remove_slot(self.base, "dashboard_data"))
        self.assertIsNone(pf.slot_file(self.base, "dashboard_data"))
        self.assertFalse(os.path.isfile(dest))
        self.assertFalse(pf.remove_slot(self.base, "dashboard_data"))

    def test_missing_file_reported_not_crashed(self):
        dest = pf.store_file(self.base, "dashboard_data", self.src)
        os.remove(dest)
        self.assertIsNone(pf.slot_file(self.base, "dashboard_data"))
        rows = pf.list_entries(self.base)
        self.assertEqual(rows[0][2], "HEL18 utilities.xml")
        self.assertFalse(rows[0][3])

    def test_corrupt_registry_recovers(self):
        pf.ensure_dir(self.base)
        with open(os.path.join(self.base, pf.REGISTRY), "w") as f:
            f.write("{not json")
        self.assertEqual(pf.load_registry(self.base), {"slots": {}})
        pf.store_file(self.base, "dashboard_data", self.src)
        self.assertIsNotNone(pf.slot_file(self.base, "dashboard_data"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
