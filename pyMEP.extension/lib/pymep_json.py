# -*- coding: utf-8 -*-
"""ASCII-safe JSON writing that works under IronPython.

The stdlib's ``ensure_ascii=True`` path calls ``s.decode('utf-8')`` on
any string holding a char >= 0x80. Under IronPython every string is
already unicode, so that decode re-reads a char like é (0xE9) as UTF-8
BYTES and dies with "Unable to translate bytes [E9] ... to Unicode".
The cure: encode with ``ensure_ascii=False`` (that path never
decodes), then escape non-ASCII chars to \\uXXXX ourselves - the file
on disk is pure ASCII, valid JSON for any reader, no codec can
mangle it.

Pure module - no clr - so CPython tests import it too.
"""

import json


def ascii_escape(text):
    """Non-ASCII chars -> \\uXXXX JSON escapes (astral chars become a
    surrogate pair). Safe to run on whole JSON text: everything
    structural in JSON is ASCII, so only string contents change."""
    out = []
    for ch in text:
        o = ord(ch)
        if o < 0x80:
            out.append(ch)
        elif o <= 0xFFFF:
            out.append(u"\\u{:04x}".format(o))
        else:
            # CPython iterates astral chars as single code points;
            # JSON wants them as a UTF-16 surrogate pair
            o -= 0x10000
            out.append(u"\\u{:04x}\\u{:04x}".format(
                0xD800 + (o >> 10), 0xDC00 + (o & 0x3FF)))
    return u"".join(out)


def dumps(obj, **kw):
    """json.dumps that survives accented names under IronPython and
    writes pure-ASCII text. Keyword args pass through (indent,
    sort_keys, default, ...)."""
    kw["ensure_ascii"] = False
    return ascii_escape(json.dumps(obj, **kw))


def dump(obj, f, **kw):
    f.write(dumps(obj, **kw))
