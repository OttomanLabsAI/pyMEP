#!/usr/bin/env python3
"""Every name used in a pushbutton script or lib module must be defined
SOMEWHERE in that file (assigned, imported, def/class, argument) or be
a builtin.

This is the lint that would have caught v1.96's bug: a patch whose
anchor text had drifted silently failed to insert ``markers = ...``,
the script still parsed, and every run died on a NameError after the
first log line. Parsing is not enough - names must resolve.

The check is deliberately coarse (a name defined anywhere in the file
counts everywhere) so it never false-positives on scoping; it only
catches names that are used and defined NOWHERE.

Run:  python3 tests/test_defined_names.py
"""

import ast
import builtins
import glob
import io
import os
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "pyMEP.extension")

# injected by pyRevit / IronPython at runtime
RUNTIME_NAMES = {
    "__revit__", "__title__", "__author__", "__doc__", "__file__",
    "__name__", "__builtins__", "reload", "unicode", "basestring",
    "xrange", "long", "cmp", "reduce", "raw_input", "unichr",
}


def defined_names(tree):
    out = set(dir(builtins)) | RUNTIME_NAMES
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                out.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.Lambda):
            args = node.args
            for a in (list(args.args) +
                      list(getattr(args, "kwonlyargs", []))):
                out.add(a.arg)
            if args.vararg:
                out.add(args.vararg.arg)
            if args.kwarg:
                out.add(args.kwarg.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            out.add(node.name)
            if hasattr(node, "args"):
                args = node.args
                for a in (list(args.args) +
                          list(getattr(args, "kwonlyargs", []))):
                    out.add(a.arg)
                if args.vararg:
                    out.add(args.vararg.arg)
                if args.kwarg:
                    out.add(args.kwarg.arg)
        elif isinstance(node, ast.Name) and isinstance(
                node.ctx, (ast.Store, ast.Del)):
            out.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            out.add(node.name)
        elif isinstance(node, ast.Global):
            out.update(node.names)
    return out


def used_names(tree):
    return set(n.id for n in ast.walk(tree)
               if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load))


def undefined_in(path):
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    return sorted(used_names(tree) - defined_names(tree))


class EveryNameResolves(unittest.TestCase):

    def _scan(self, pattern):
        files = sorted(glob.glob(pattern))
        self.assertTrue(files, "no files matched " + pattern)
        problems = {}
        for path in files:
            bad = undefined_in(path)
            if bad:
                rel = os.path.relpath(path, ROOT)
                problems[rel] = bad
        self.assertEqual(problems, {},
                         "names used but defined nowhere: {}".format(
                             problems))

    def test_pushbutton_scripts(self):
        self._scan(os.path.join(ROOT, "pyMEP.tab", "*", "*", "script.py"))

    def test_stacked_and_split_scripts(self):
        self._scan(os.path.join(ROOT, "pyMEP.tab", "*", "*", "*",
                                "script.py"))

    def test_lib_modules(self):
        self._scan(os.path.join(ROOT, "lib", "*.py"))

    def test_startup(self):
        self._scan(os.path.join(ROOT, "startup.py"))

    def test_catches_the_v196_bug(self):
        import tempfile
        bad = tempfile.NamedTemporaryFile(mode="w", suffix=".py",
                                          delete=False)
        bad.write("x = markers\n")
        bad.close()
        self.assertEqual(undefined_in(bad.name), ["markers"])
        os.unlink(bad.name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
