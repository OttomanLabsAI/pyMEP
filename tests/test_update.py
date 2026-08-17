#!/usr/bin/env python3
"""Unit tests for the self-updater's pure pieces (stdlib only, no
Revit / .NET): version-tag parsing, the git refs-advertisement tag
parser, and the download-URL ladder. The module imports clr, so the
functions are extracted by AST like the survey-rotation tests do.

Run:  python3 tests/test_update.py
"""

import ast
import os
import re
import unittest

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "..", "pyMEP.extension", "lib")


def extract_function(module_file, func_name, ns=None):
    path = os.path.join(LIB, module_file)
    with open(path) as f:
        src = f.read()
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            ns = ns if ns is not None else {}
            ns.setdefault("re", re)
            exec(compile(ast.get_source_segment(src, node), path, "exec"),
                 ns)
            return ns[func_name]
    raise AssertionError("{} not found in {}".format(func_name, path))


_ns = {"re": re}
version_key = extract_function("pymep_update.py", "version_key", _ns)
parse_ref_tags = extract_function("pymep_update.py", "parse_ref_tags", _ns)
zip_candidates = extract_function("pymep_update.py", "zip_candidates", _ns)

REFS = (
    "001e# service=git-upload-pack\n"
    "0000015523abc HEAD\x00multi_ack symref=HEAD:refs/heads/main\n"
    "003f9c1f2a refs/heads/main\n"
    "003e11aa22 refs/tags/v1.207.0\n"
    "004112bb33 refs/tags/v1.207.0^{}\n"
    "003e44cc55 refs/tags/v1.208.0\n"
    "003d66dd77 refs/tags/not-a-version\n"
    "0000")


class ParseRefTags(unittest.TestCase):
    def test_tags_found_peeled_dropped(self):
        tags = parse_ref_tags(REFS)
        self.assertIn("v1.207.0", tags)
        self.assertIn("v1.208.0", tags)
        self.assertIn("not-a-version", tags)
        self.assertTrue(all(not t.endswith("^{}") for t in tags))

    def test_version_filter_and_sort(self):
        tags = [t for t in parse_ref_tags(REFS)
                if version_key(t) is not None]
        tags.sort(key=version_key, reverse=True)
        self.assertEqual(tags[0], "v1.208.0")

    def test_empty_and_none(self):
        self.assertEqual(parse_ref_tags(""), [])
        self.assertEqual(parse_ref_tags(None), [])


class ZipCandidates(unittest.TestCase):
    REPO = "OttomanLabsAI/pyMEP"
    API = "https://api.github.com/repos/OttomanLabsAI/pyMEP/zipball/v1.208.0"

    def test_tag_label_gets_codeload_tag_mirror(self):
        urls = zip_candidates(self.REPO, "v1.208.0", self.API)
        self.assertEqual(urls[0], self.API)
        self.assertIn("https://codeload.github.com/OttomanLabsAI/pyMEP"
                      "/zip/refs/tags/v1.208.0", urls)

    def test_branch_label_gets_head_mirrors(self):
        urls = zip_candidates(self.REPO,
                              "default branch (no releases/tags found)",
                              self.API)
        self.assertIn("https://codeload.github.com/OttomanLabsAI/pyMEP"
                      "/zip/refs/heads/main", urls)
        self.assertIn("https://codeload.github.com/OttomanLabsAI/pyMEP"
                      "/zip/refs/heads/master", urls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
