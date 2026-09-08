# -*- coding: utf-8 -*-
"""Export Manholes - the pure parts of the manhole JSON export, shared
with the CPython tests. Nothing here touches the Revit API; the button
script feeds it plain numbers and strings.

The JSON contract (the Manhole Plan web tool's importer depends on it,
units millimetres):

  {"units": "mm", "source": "<document path>",
   "families": [{"family": "<name>",
                 "planes": [{"name": "a1", "axis": "a",
                             "normal": [nx, ny, nz],
                             "offset_mm": <signed distance from the
                                           family origin along normal>,
                             "origin_mm": [x, y, z]}],
                 "instances": [{"id": <int>, "mark": "<Mark>" | null,
                                "type": "<type name>",
                                "origin_mm": [x, y, z],
                                "family_x_axis": [x, y, z],
                                "family_y_axis": [x, y, z],
                                "rotation_deg": <family X in project XY>,
                                "mirrored": true | false}]}]}

Planes exported, by exact name (case-insensitive): a1-a7, b1-b7, z1-z6
and the six <axis>_conduit_boundary_<n> planes; every other reference
plane is ignored.
"""

import math
import re

NAME_RE = re.compile(r"^(?:[abz]\d+|[abz]_conduit_boundary_\d+)$", re.I)

EXPECTED_PLANES = (
    "a1-a7, b1-b7, z1-z6, a_conduit_boundary_1/2, "
    "b_conduit_boundary_1/2, z_conduit_boundary_1/2")

MM_PER_FOOT = 304.8


def plane_wanted(name):
    """True when a reference plane's name is one the export records."""
    if not name:
        return False
    return NAME_RE.match(u"{0}".format(name).strip()) is not None


def plane_axis(name):
    """'a', 'b' or 'z' - the first letter of a wanted plane name."""
    return u"{0}".format(name).strip()[0].lower()


def round_vec(x, y, z, places=6):
    return [round(float(x), places), round(float(y), places),
            round(float(z), places)]


def feet_to_mm(v, places=2):
    """Fallback unit conversion when the script cannot use UnitUtils."""
    return round(float(v) * MM_PER_FOOT, places)


def plane_offset(origin, normal):
    """Signed distance of a plane from the family origin along its
    normal: origin . normal (same units as origin)."""
    ox, oy, oz = origin
    nx, ny, nz = normal
    return ox * nx + oy * ny + oz * nz


def plane_record(name, origin, normal, to_mm):
    """One plane entry. origin / normal are (x, y, z) in internal units;
    to_mm converts one internal length to rounded millimetres."""
    return {"name": u"{0}".format(name).strip(),
            "axis": plane_axis(name),
            "normal": round_vec(*normal),
            "offset_mm": to_mm(plane_offset(origin, normal)),
            "origin_mm": [to_mm(origin[0]), to_mm(origin[1]),
                          to_mm(origin[2])]}


def sort_planes(records):
    """Planes ordered by axis then name - a1..a7, a_conduit_boundary_1..,
    b.., z.. - the order the reference script produced."""
    return sorted(records, key=lambda r: (r["axis"], r["name"]))


def rotation_deg(basis_x):
    """Degrees of the family X axis in the project XY plane."""
    return round(math.degrees(math.atan2(basis_x[1], basis_x[0])), 3)


def instance_record(elem_id, mark, type_name, origin, basis_x, basis_y,
                    mirrored, to_mm):
    """One placed-instance entry from plain values (internal units)."""
    return {"id": int(elem_id),
            "mark": mark if mark else None,
            "type": u"{0}".format(type_name),
            "origin_mm": [to_mm(origin[0]), to_mm(origin[1]),
                          to_mm(origin[2])],
            "family_x_axis": round_vec(*basis_x),
            "family_y_axis": round_vec(*basis_y),
            "rotation_deg": rotation_deg(basis_x),
            "mirrored": bool(mirrored)}


def family_record(name, planes, instances=None):
    return {"family": u"{0}".format(name), "planes": sort_planes(planes),
            "instances": list(instances or [])}


def export_document(source, families):
    """The whole file: only families that carry at least one wanted
    plane are kept."""
    return {"units": "mm", "source": u"{0}".format(source or u""),
            "families": [f for f in families if f and f.get("planes")]}


def default_file_name(title):
    """'<model name>-manholes.json' from a document title, with a
    trailing .rvt / .rfa dropped."""
    base = u"{0}".format(title or u"model").strip()
    low = base.lower()
    for ext in (".rvt", ".rfa"):
        if low.endswith(ext):
            base = base[:-len(ext)]
            break
    return u"{0}-manholes.json".format(base or u"model")


def ensure_json_ext(path):
    """A picked path that lacks .json gets it."""
    p = u"{0}".format(path or u"")
    if not p:
        return p
    return p if p.lower().endswith(".json") else p + u".json"


def summary_text(families, instances, path, family_doc=False):
    """The closing alert."""
    if not families:
        return (u"No family with the manhole plane convention was found.\n\n"
                u"Expected reference planes named: {0}.\n\n"
                u"Nothing was written.".format(EXPECTED_PLANES))
    what = u"family document" if family_doc else u"project"
    return (u"Exported from the {0}:\n\n"
            u"  {1} family(ies) with the plane convention\n"
            u"  {2} placed instance(s)\n\n"
            u"{3}".format(what, families, instances, path))
