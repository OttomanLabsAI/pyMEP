#!/usr/bin/env python3
"""Unit tests for pymep_json - the ASCII-safe JSON writer that keeps
IronPython from crashing on accented names ('unknown' codec can't
decode byte 0xe9). The rule: whatever goes in, the text on disk is
pure ASCII and json.loads gives back the exact original strings.

Run:  python3 tests/test_json_ascii.py
"""

import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "pyMEP.extension", "lib"))

import pymep_json
import pymep_vt_schema as S


class AsciiEscape(unittest.TestCase):
    def test_ascii_passes_through(self):
        self.assertEqual(pymep_json.ascii_escape(u'{"a": 1}'),
                         u'{"a": 1}')

    def test_latin1_char_escapes(self):
        # the exact field failure: 0xe9 at position 1
        self.assertEqual(pymep_json.ascii_escape(u"Béton"),
                         u"B\\u00e9ton")

    def test_astral_char_becomes_surrogate_pair(self):
        # U+1F600 -> 😀 (JSON's UTF-16 pair encoding)
        self.assertEqual(pymep_json.ascii_escape(u"\U0001F600"),
                         u"\\ud83d\\ude00")


class DumpsRoundTrip(unittest.TestCase):
    NAMES = [u"Béton armé", u"Réseau EU",
             u"Überlauf", u"雨水",  # Chinese 'rainwater'
             u"plain ascii", u"emoji \U0001F600"]

    def test_output_is_pure_ascii(self):
        text = pymep_json.dumps({"names": self.NAMES}, indent=2)
        self.assertTrue(all(ord(c) < 128 for c in text),
                        "non-ASCII char survived into the JSON text")

    def test_round_trip_exact(self):
        text = pymep_json.dumps({"names": self.NAMES}, sort_keys=True)
        self.assertEqual(json.loads(text)["names"], self.NAMES)

    def test_dump_writes_same_text(self):
        buf = io.StringIO()
        pymep_json.dump({"n": u"café"}, buf, indent=2)
        self.assertEqual(json.loads(buf.getvalue()), {"n": u"café"})

    def test_kwargs_pass_through(self):
        text = pymep_json.dumps({"b": 1, "a": 2}, sort_keys=True)
        self.assertLess(text.index('"a"'), text.index('"b"'))


class SchemaDumpsAscii(unittest.TestCase):
    """pymep_vt_schema.dumps (the export file writer) rides on the
    same escape - an accented filter name must neither crash nor leak
    a raw accent into the file."""

    def test_accented_filter_name_round_trips(self):
        doc = S.make_document("2025")
        doc["filters"].append({"name": u"Béton armé",
                               "categories": ["OST_Walls"]})
        text = S.dumps(doc)
        self.assertTrue(all(ord(c) < 128 for c in text))
        back = S.loads(text)
        self.assertEqual(back["filters"][0]["name"],
                         u"Béton armé")

    def test_canonical_ascii_output_unchanged(self):
        # pure-ASCII documents keep the exact old canonical text
        doc = S.make_document("2025")
        expected = json.dumps(doc, sort_keys=True, indent=2,
                              separators=(",", ": "))
        self.assertEqual(S.dumps(doc), expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
