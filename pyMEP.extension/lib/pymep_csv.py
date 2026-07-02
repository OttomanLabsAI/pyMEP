# -*- coding: utf-8 -*-
"""CSV I/O and numeric rounding helpers for pyMEP."""

import csv
import math


def fmt(val, decimals=1):
    if val is None:
        return ""
    return "{:.{}f}".format(val, decimals)


def write_csv(filepath, header, rows):
    """Write CSV with no trailing blank lines."""
    with open(filepath, "w") as f:
        f.write(header + "\n")
        for row in rows:
            f.write(row + "\n")


def read_csv_dicts(filepath):
    """Read CSV into list of dicts. Tolerates CRLF."""
    with open(filepath, "r") as f:
        text = f.read().replace("\r", "")
    lines = [l for l in text.split("\n") if l.strip()]
    return list(csv.DictReader(lines))


def ceil_to_50(v):
    """Round UP to nearest 50 mm."""
    return math.ceil(v / 50.0) * 50.0
