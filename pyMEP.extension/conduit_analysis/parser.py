"""Parse pipe and fitting CSV data."""
import csv
import io
import numpy as np


def parse_csv(text):
    """Parse CSV text with blank-line tolerance. Returns list of dicts with _sp/_ep arrays."""
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    cleaned = "\n".join(lines)
    rows = []
    for r in csv.DictReader(io.StringIO(cleaned)):
        r["_sp"] = np.array([float(r["Start_X"]), float(r["Start_Y"]), float(r["Start_Z"])])
        r["_ep"] = np.array([float(r["End_X"]), float(r["End_Y"]), float(r["End_Z"])])
        rows.append(r)
    return rows


def parse_file(filepath):
    """Read and parse a CSV file."""
    with open(filepath) as f:
        return parse_csv(f.read())


def get_od(pipes):
    """Extract a single fallback OD in mm from pipe data (first valid value).

    NOTE: prefer get_od_map() so each pipe keeps its own OD. This single-value
    helper remains only as a default for pipes with a missing/blank OD_mm.
    """
    for p in pipes:
        val = p.get("OD_mm", "")
        if val not in (None, "", " "):
            try:
                return float(val)
            except (ValueError, TypeError):
                continue
    return 205.0


def get_od_map(pipes):
    """Map each pipe's element ID -> its own OD in mm.

    The encasement size must follow each pipe's real outer diameter; collapsing
    the whole network to one OD (the previous behaviour) made every duct the
    size of whichever pipe happened to be first in the export. Pipes with a
    missing/unparseable OD_mm are left out of the map and fall back to the
    network default from get_od().

    Returns: (od_by_id: dict[str, float], default_od: float)
    """
    od_by_id = {}
    for p in pipes:
        pid = (p.get("ID") or p.get("Id") or p.get("id") or "").strip()
        val = p.get("OD_mm", "")
        if val in (None, "", " "):
            continue
        try:
            od = float(val)
        except (ValueError, TypeError):
            continue
        if pid:
            od_by_id[pid] = od
    return od_by_id, get_od(pipes)
